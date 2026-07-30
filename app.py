import re
import html
import time
from typing import List, Optional

import httpx
from bs4 import BeautifulSoup
from fastapi import FastAPI, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.responses import (
    JSONResponse,
    PlainTextResponse,
    HTMLResponse,
)
from pydantic import BaseModel, field_validator
from starlette.exceptions import HTTPException as StarletteHTTPException


NOT_FOUND = "score not found"
REQUEST_TIMEOUT = "request timeout"
INVALID_MATCH_ID = "invalid score id"

# Matches a player name made of words (letters, dots, apostrophes, hyphens),
# used when scraping the live "Batter R B 4s 6s SR" / "Bowler O M R W ECO"
# tables directly off the page text.
NAME_CHARS = r"[A-Za-z][A-Za-z.'\-]*(?:\s[A-Za-z][A-Za-z.'\-]*)*"

# Checked in order - first one found in the page text wins. Covers the
# common break/stoppage states Cricbuzz shows between overs, innings, days.
STATUS_KEYWORDS = [
    "Innings Break",
    "Strategic Time-out",
    "Strategic Timeout",
    "Rain Delay",
    "Bad Light",
    "Wet Outfield",
    "Drinks Break",
    "Tea Break",
    "Lunch Break",
    "Stumps",
    "Match Delayed",
    "Match Abandoned",
    "Match Interrupted",
]

# Matches the final result line Cricbuzz shows once a match has finished,
# e.g. "Galle Gallants won by 5 wickets", "India beat Australia by 20 runs
# (D/L method)", plus the no-clear-winner variants.
RESULT_PATTERNS = [
    r"([A-Za-z][A-Za-z .]{1,30}?\s+won\s+by\s+\d+\s+(?:runs?|wickets?)(?:\s*\([^)]+\))?)",
    r"([A-Za-z][A-Za-z .]{1,30}?\s+beat\s+[A-Za-z][A-Za-z .]{1,30}?\s+by\s+\d+\s+(?:runs?|wickets?))",
    r"(Match\s+Tied)",
    r"(No\s+Result)",
    r"(Match\s+Drawn)",
]


class APIError(Exception):
    def __init__(self, status_code: int, message: str):
        self.status_code = status_code
        self.message = message


class Batsman(BaseModel):
    name: str = NOT_FOUND
    score: str = NOT_FOUND
    on_strike: bool = False  # True for the batsman currently on strike (Cricbuzz marks this with "*")


class Bowler(BaseModel):
    name: str = NOT_FOUND
    overs: str = NOT_FOUND
    maidens: str = NOT_FOUND
    runs: str = NOT_FOUND
    wickets: str = NOT_FOUND
    economy: str = NOT_FOUND


class ScoreResponse(BaseModel):
    status: str
    title: str
    score: str
    all_scores: List[str] = []   # every innings score found (e.g. both teams in a Test)
    target_info: str = ""        # "XYZ need N runs" style text, if found
    match_status: str = ""       # "Innings Break" / "Stumps" / "Rain Delay" etc, if detected
    match_result: str = ""       # "TEAM won by N runs/wickets" / "Match Tied" / "No Result", once the match ends
    recent_balls: List[str] = []  # ground-truth ball-by-ball tags from Cricbuzz's own "Recent" widget
    current_batsmen: List[Batsman]
    current_bowler: Bowler


class MatchValidator(BaseModel):
    score: str

    @field_validator("score")
    @classmethod
    def validate_match_id(cls, value: str) -> str:
        value = value.strip()

        if not value:
            raise ValueError(INVALID_MATCH_ID)

        if not value.isdigit():
            raise ValueError("score id must contain digits only")

        if len(value) < 4:
            raise ValueError("score id must be at least 4 digits")

        if len(value) > 20:
            raise ValueError("score id too long")

        return value


app = FastAPI(
    title="Score API",
    version="0.0.1",
    description="Live Cricket Score JSON API",
    docs_url=None,
    redoc_url=None
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["GET"],
    allow_headers=["*"],
)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)

    response.headers["Cache-Control"] = (
        "no-store, no-cache, must-revalidate, "
        "proxy-revalidate, max-age=0"
    )
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    response.headers["Surrogate-Control"] = "no-store"

    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Robots-Tag"] = "noindex, nofollow"

    response.headers["Strict-Transport-Security"] = (
        "max-age=31536000"
    )

    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        "connect-src 'self' https://cdn.jsdelivr.net; "
        "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
        "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
        "img-src 'self' data: https://fastapi.tiangolo.com; "
        "object-src 'none'; "
        "frame-ancestors 'none';"
    )

    return response


class ScoreService:
    HEADERS = {
        "User-Agent": (
            "Mozilla/5.0 "
            "(X11; Linux x86_64) "
            "AppleWebKit/537.36 "
            "(KHTML, like Gecko) "
            "Chrome/146.0.0.0 "
            "Safari/537.36"
        ),
        "Referer": "https://www.cricbuzz.com/",
        "Origin": "https://www.cricbuzz.com",
        "Cache-Control": "no-cache, no-store, max-age=0",
        "Pragma": "no-cache",
        "Expires": "0",
        "Connection": "close",
        "Accept": (
            "text/html,"
            "application/xhtml+xml,"
            "application/xml;q=0.9,"
            "*/*;q=0.8"
        ),
        "Accept-Language": "en-US,en;q=0.9",
    }

    # Caches the last known-good ScoreResponse per match, used by the
    # monotonic guard below. Cricbuzz's own backend can occasionally
    # serve a slightly-stale snapshot on one request and then "catch up"
    # on the next - since scoreboard_generator.py and commentary_generator.py
    # both poll this API frequently and independently, that stale blip was
    # visible as the overs count appearing to jump backward for a moment
    # (e.g. 32 -> 30 -> 31). Guarding here keeps both of them always in
    # sync and always moving forward.
    _last_good_response: dict = {}

    @classmethod
    def _apply_monotonic_guard(cls, match_id: str, new_response: "ScoreResponse") -> "ScoreResponse":
        def parse_overs(resp):
            m = re.search(r"\(([\d.]+)\)", resp.score or "")
            return float(m.group(1)) if m else None

        def team_prefix(resp):
            parts = (resp.score or "").split()
            return parts[0] if parts else None

        last = cls._last_good_response.get(match_id)
        if last is None:
            cls._last_good_response[match_id] = new_response
            return new_response

        new_overs = parse_overs(new_response)
        last_overs = parse_overs(last)

        # Only compare within the SAME team/innings - a genuine innings
        # change legitimately resets overs to 0, and that must go through.
        if (new_overs is not None and last_overs is not None
                and team_prefix(new_response) == team_prefix(last)
                and new_overs < last_overs):
            # This snapshot looks stale/out-of-order - serve the last
            # good one instead so downstream consumers never see it regress.
            return last

        cls._last_good_response[match_id] = new_response
        return new_response

    @staticmethod
    def clean(text: str) -> str:
        if not text:
            return NOT_FOUND
        return html.escape(" ".join(text.split()))

    @classmethod
    def default_batsmen(cls) -> List[Batsman]:
        return [Batsman(), Batsman()]

    @classmethod
    def format_tree(cls, data: ScoreResponse) -> str:
        batsmen_lines = "\n".join(
            f"│   ├── {player.name}{' *' if player.on_strike else ''} : {player.score}"
            for player in data.current_batsmen
        )

        bowler_line = (
            f"{data.current_bowler.name}  "
            f"O:{data.current_bowler.overs} M:{data.current_bowler.maidens} "
            f"R:{data.current_bowler.runs} W:{data.current_bowler.wickets} "
            f"ECO:{data.current_bowler.economy}"
        )

        return (
            "🏏 Live Score\n"
            "│\n"
            f"├── Match    : {data.title}\n"
            f"├── Score    : {data.score}\n"
            f"├── All Scores : {', '.join(data.all_scores) if data.all_scores else NOT_FOUND}\n"
            f"├── Target   : {data.target_info or NOT_FOUND}\n"
            f"├── Status   : {data.match_status or NOT_FOUND}\n"
            f"├── Result   : {data.match_result or NOT_FOUND}\n"
            f"├── Bowler   : {bowler_line}\n"
            "├── Batsmen\n"
            f"{batsmen_lines}"
        )

    @classmethod
    def _parse_batsmen_from_table(cls, page_text: str) -> List[Batsman]:
        """Parses the live 'Batter  R  B  4s  6s  SR' table directly off the
        page text. This is the primary/preferred source - it also gives us
        the on-strike marker ('*') which the og:title never contains."""
        batsmen: List[Batsman] = []

        section = re.search(
            r"Batter\s+R\s+B\s+4s\s+6s\s+SR(.*?)(?:Bowler\s+O\s+M\s+R\s+W\s+ECO|$)",
            page_text,
            re.IGNORECASE | re.DOTALL,
        )
        if not section:
            return batsmen

        for m in re.finditer(
            rf"({NAME_CHARS})(\s*\*)?\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+(?:\.\d+)?)",
            section.group(1),
        ):
            name, star, runs, balls, _fours, _sixes, _sr = m.groups()
            batsmen.append(
                Batsman(
                    name=cls.clean(name),
                    score=f"{runs}({balls})",
                    on_strike=bool(star),
                )
            )
            if len(batsmen) == 2:
                break

        return batsmen

    @classmethod
    def _parse_batsmen_from_title(cls, og_title: str) -> List[Batsman]:
        """Fallback: older method, parses batsmen out of the og:title meta
        tag. Doesn't know who's on strike (title never marks that)."""
        batsmen: List[Batsman] = []

        batsman_match = re.search(r"\((.*?)\)\s*\|", og_title)
        if batsman_match:
            players = re.findall(
                r"([A-Za-z\s.'-]+)\s+(\d+\(\d+\))",
                batsman_match.group(1)
            )
            batsmen = [
                Batsman(name=cls.clean(name), score=cls.clean(score_value))
                for name, score_value in players[:2]
            ]

        return batsmen

    @classmethod
    def _parse_bowler_from_table(cls, page_text: str) -> Optional[Bowler]:
        """Parses the live 'Bowler  O  M  R  W  ECO' table directly off the
        page text. Cricbuzz shows the CURRENT bowler's row marked with '*';
        if two rows are present we prefer the starred (current) one."""
        section = re.search(
            r"Bowler\s+O\s+M\s+R\s+W\s+ECO(.*?)(?:Have Your Say|Key Stats|Recent\s*:|$)",
            page_text,
            re.IGNORECASE | re.DOTALL,
        )
        if not section:
            return None

        rows = list(re.finditer(
            rf"({NAME_CHARS})(\s*\*)?\s+(\d+(?:\.\d+)?)\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+(?:\.\d+)?)",
            section.group(1),
        ))
        if not rows:
            return None

        chosen = next((r for r in rows if r.group(2)), rows[0])
        name, _star, overs, maidens, runs, wickets, economy = chosen.groups()

        return Bowler(
            name=cls.clean(name),
            overs=overs,
            maidens=maidens,
            runs=runs,
            wickets=wickets,
            economy=economy,
        )

    @classmethod
    def _parse_bowler_name_only(cls, page_text: str) -> str:
        """Last-resort fallback: just get a bowler name if the full table
        couldn't be parsed for some reason."""
        bowler_match = re.search(
            r"Bowler.*?([A-Za-z.'\- ]+?)\s+\d+\s+\d+",
            page_text,
            re.IGNORECASE
        )
        return cls.clean(bowler_match.group(1)) if bowler_match else NOT_FOUND

    @classmethod
    def _parse_all_scores_from_page(cls, page_text: str, title: str) -> List[str]:
        """Reads BOTH teams' scores directly from the page's own score
        summary (e.g. 'NED 225-9 (50)  NEP 11-1 (4.1)'), which is far more
        reliable than the og:title meta tag - that tag usually only ever
        reports whichever team is CURRENTLY batting, so a completed first
        innings would otherwise vanish entirely once the 2nd innings starts.

        Cricbuzz renders the score with a '-' between runs/wickets visually,
        but get_text() can also produce '/' depending on markup, so both are
        accepted. The search is anchored to just before the live "Batter"
        table (or after our own match title, as a second anchor) so it
        can't accidentally pick up scores from OTHER matches shown in the
        site's top ticker bar.
        """
        region = page_text

        if title and title != NOT_FOUND and len(title) >= 10:
            idx = page_text.find(title[:15])
            if idx != -1:
                region = page_text[idx:]

        end_anchor = re.search(r"Batter\s+R\s+B\s+4s\s+6s\s+SR", region, re.IGNORECASE)
        region = region[:end_anchor.start()] if end_anchor else region[:800]

        scores: List[str] = []
        seen = set()
        for team, runs, wickets, overs in re.findall(
            r"([A-Z]{2,4})\s+(\d+)\s*[/\-]\s*(\d+)\s*\(\s*([\d.]+)\s*\)",
            region,
        ):
            key = team.upper()
            if key in seen:
                continue
            seen.add(key)
            scores.append(f"{team} {runs}/{wickets} ({overs})")

        return scores

    @classmethod
    async def fetch_score(cls, match_id: str) -> ScoreResponse:
        try:
            url = (
                "https://www.cricbuzz.com/live-cricket-scores/"
                f"{match_id}?_={time.time_ns()}"
            )

            async with httpx.AsyncClient(
                timeout=10.0,
                follow_redirects=True
            ) as client:
                response = await client.get(
                    url,
                    headers=cls.HEADERS
                )
                response.raise_for_status()

            soup = BeautifulSoup(response.text, "lxml")

            title = cls.clean(
                re.sub(
                    r"^Cricket commentary\s*\|\s*",
                    "",
                    soup.title.get_text(strip=True)
                    if soup.title
                    else NOT_FOUND,
                    flags=re.IGNORECASE
                )
            )

            og_tag = soup.find("meta", property="og:title")
            og_title = og_tag.get("content", "") if og_tag else ""

            page_text = cls.clean(
                soup.get_text(" ", strip=True)
            )

            # ---- All scores: prefer reading BOTH teams directly off the
            # page's own score summary (reliable even after the first
            # innings has finished and dropped out of the title). Falls
            # back to the og:title meta tag only if that yields nothing. ----
            all_scores = cls._parse_all_scores_from_page(page_text, title)
            if not all_scores:
                for team, runs, wickets, overs in re.findall(
                    r"([A-Z]{2,4})\s+(\d+)/(\d+)\s*\(([\d.]+)\)",
                    og_title
                ):
                    all_scores.append(f"{team} {runs}/{wickets} ({overs})")

            score = all_scores[0] if all_scores else NOT_FOUND

            # ---- Batsmen: prefer the live scorecard table (gives on-strike
            # marker too), fall back to og:title parsing if that fails ----
            # IMPORTANT: only fall back further when we found NOTHING at
            # all. A partner who's just been dismissed (new batter not yet
            # facing a ball) is a completely normal state where Cricbuzz
            # legitimately shows only ONE batter row - that single valid
            # entry must be kept, not thrown away in favour of two "score
            # not found" placeholders.
            batsmen = cls._parse_batsmen_from_table(page_text)
            if not batsmen:
                batsmen = cls._parse_batsmen_from_title(og_title)
            if not batsmen:
                batsmen = cls.default_batsmen()

            # ---- Bowler: prefer the live scorecard table (gives full
            # O/M/R/W/ECO), fall back to name-only if that fails ----
            bowler = cls._parse_bowler_from_table(page_text)
            if bowler is None:
                bowler = Bowler(name=cls._parse_bowler_name_only(page_text))

            # IMPORTANT: Cricbuzz's page also includes a top ticker bar
            # listing OTHER live matches (e.g. "NED vs NEP - Need 1...",
            # "WEF vs TRE - TRE opt..."). If we search the entire page text
            # for target/status phrases, we can accidentally pick up some
            # OTHER match's status (e.g. seeing "Innings Break" that
            # actually belongs to a different game entirely). To avoid
            # this, we anchor the search to start at THIS match's own
            # score line (e.g. "NED 225") - found via the abbreviation we
            # already parsed into all_scores - and end at the Batter
            # table. This is more reliable than a fixed character offset,
            # since it doesn't depend on how much nav/ticker/ad text
            # happens to precede it on any given page.
            info_window = page_text
            start_idx = 0
            if all_scores:
                first_abbr = all_scores[0].split()[0] if all_scores[0].split() else None
                if first_abbr:
                    anchor_match = re.search(rf"\b{re.escape(first_abbr)}\s+\d+", page_text)
                    if anchor_match:
                        start_idx = anchor_match.start()
            batter_idx = page_text.lower().find("batter", start_idx)
            end_idx = batter_idx if batter_idx != -1 else len(page_text)
            info_window = page_text[start_idx:end_idx]

            # Best-effort search for a "need N runs" / target/status line.
            # TIGHTLY bounded (can't run on into unrelated trailing text),
            # AND anchored with \b + a longer name allowance (2-20 letters)
            # so full team names like "Nepal" or "Netherlands" aren't cut
            # into the middle of the word (which used to produce garbled
            # output like "epal need 214 runs" instead of "Nepal need 215
            # runs" - {2,4} was too short and had no word-boundary anchor).
            target_match = re.search(
                r"\b([A-Za-z]{2,20}\s+need\s+\d+\s+runs?"
                r"(?:\s+(?:in|from|off)\s+[\d.]+\s+(?:balls?|overs?))?"
                r"(?:\s+to\s+win)?)",
                info_window,
                re.IGNORECASE
            )
            target_info = cls.clean(target_match.group(1)) if target_match else ""
            if target_info == NOT_FOUND:
                target_info = ""

            # Detect innings breaks / stoppages so the scoreboard can show a
            # clear status instead of just blank batter/bowler cards.
            match_status = ""
            for keyword in STATUS_KEYWORDS:
                if re.search(rf"\b{re.escape(keyword)}\b", info_window, re.IGNORECASE):
                    match_status = keyword
                    break

            # Detect the final match result (once the match has finished).
            # Checked independently of match_status since a finished match
            # should show the RESULT, not a break/stoppage label.
            match_result = ""
            for pattern in RESULT_PATTERNS:
                m = re.search(pattern, info_window, re.IGNORECASE)
                if m:
                    match_result = cls.clean(m.group(1))
                    break

            # Ground-truth ball-by-ball tags, straight from Cricbuzz's own
            # "Recent : 4 4 1 4 1 2" widget - oldest ball first, newest
            # (most recent) last. This is far more reliable than inferring
            # events from score snapshots polled every few seconds (that
            # approach can never see dot balls, and can merge multiple
            # deliveries into one wrong combined number).
            recent_balls: List[str] = []
            recent_match = re.search(
                r"Recent\s*:\s*((?:[0-9]+|W)(?:\s+(?:[0-9]+|W))*)",
                page_text,
            )
            if recent_match:
                recent_balls = recent_match.group(1).split()

            result = ScoreResponse(
                status="success",
                title=title,
                score=score,
                all_scores=all_scores,
                target_info=target_info,
                match_status=match_status,
                match_result=match_result,
                recent_balls=recent_balls,
                current_batsmen=batsmen,
                current_bowler=bowler
            )
            return cls._apply_monotonic_guard(match_id, result)

        except httpx.TimeoutException:
            raise APIError(408, REQUEST_TIMEOUT)

        except httpx.HTTPStatusError:
            raise APIError(404, "score data unavailable")

        except Exception:
            raise APIError(500, "failed to process score data")

@app.get("/docs", include_in_schema=False)
async def custom_swagger_docs():
    try:
        html = get_swagger_ui_html(
            openapi_url=app.openapi_url,
            title="Live Cricket Score API Docs",
            swagger_favicon_url="https://fastapi.tiangolo.com/img/favicon.png"
        )

        content = html.body.decode("utf-8")

        if "</head>" not in content:
            raise ValueError("Invalid Swagger HTML")

        custom_style = """
        <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1">
        <style>
            html, body {
                margin: 0;
                padding: 0;
                width: 100%;
                overflow-x: hidden;
                -webkit-text-size-adjust: 100%;
            }

            .swagger-ui {
                width: 100%;
                overflow-x: hidden;
            }

            .swagger-ui .wrapper {
                width: 100%;
                max-width: 100% !important;
                padding: 10px !important;
                box-sizing: border-box;
            }

            .swagger-ui .opblock-summary {
                flex-wrap: wrap !important;
                gap: 6px;
            }

            .swagger-ui .opblock-summary-path {
                white-space: normal !important;
                word-break: break-word !important;
                overflow-wrap: anywhere !important;
                font-size: 14px !important;
                line-height: 1.4;
            }

            .swagger-ui pre,
            .swagger-ui code,
            .swagger-ui .microlight,
            .swagger-ui .highlight-code {
                white-space: pre-wrap !important;
                word-break: break-word !important;
                overflow-wrap: anywhere !important;
                overflow-x: auto !important;
                max-width: 100% !important;
                max-height: 220px !important;
                overflow-y: auto !important;
                box-sizing: border-box;
                font-size: 12px !important;
                line-height: 1.5 !important;
                border-radius: 8px;
            }

            .swagger-ui table {
                display: block;
                width: 100%;
                overflow-x: auto;
            }

            .swagger-ui textarea,
            .swagger-ui input,
            .swagger-ui select {
                width: 100% !important;
                box-sizing: border-box;
                font-size: 16px !important;
            }

            .swagger-ui .btn {
                min-height: 42px !important;
                white-space: normal !important;
            }

            @media (max-width: 768px) {
                .swagger-ui .wrapper {
                    padding: 8px !important;
                }

                .swagger-ui pre,
                .swagger-ui code {
                    max-height: 180px !important;
                    font-size: 11px !important;
                }
            }
        </style>
        """

        content = content.replace(
            "</head>",
            custom_style + "</head>"
        )

        response = HTMLResponse(content=content)

        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"

        return response

    except Exception:
        return HTMLResponse(
            content="""
            <html>
                <head>
                    <meta name="viewport" content="width=device-width, initial-scale=1.0">
                    <title>Docs Error</title>
                </head>
                <body style="font-family:sans-serif;padding:20px;">
                    <h2>Unable to load Swagger docs</h2>
                </body>
            </html>
            """,
            status_code=500
        )

@app.get("/", response_model=ScoreResponse)
async def root(
    score: Optional[str] = Query(
        None,
        min_length=4,
        max_length=20
    ),
    text: bool = Query(False)
):
    if score is None:
        return ScoreResponse(
            status="success",
            title="Live Score API",
            score=NOT_FOUND,
            all_scores=[],
            target_info="",
            match_status="",
            match_result="",
            recent_balls=[],
            current_batsmen=ScoreService.default_batsmen(),
            current_bowler=Bowler()
        )

    try:
        MatchValidator(score=score)
    except Exception as exc:
        return JSONResponse(
            status_code=422,
            content={
                "status": "error",
                "code": 422,
                "message": "score id must be at least 4 digits"
            }
        )

    result = await ScoreService.fetch_score(score)

    if text:
        return PlainTextResponse(
            ScoreService.format_tree(result)
        )

    return result


@app.exception_handler(APIError)
async def api_error_handler(request: Request, exc: APIError):
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "status": "error",
            "code": exc.status_code,
            "message": "score id must be at least 4 digits"
        }
    )


@app.exception_handler(StarletteHTTPException)
async def http_error_handler(
    request: Request,
    exc: StarletteHTTPException
):
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "status": "error",
            "code": exc.status_code,
            "message": "invalid api route"
        }
    )


@app.exception_handler(Exception)
async def global_error_handler(request: Request, exc: Exception):
    return JSONResponse(
        status_code=500,
        content={
            "status": "error",
            "code": 500,
            "message": "internal server error"
        }
    )