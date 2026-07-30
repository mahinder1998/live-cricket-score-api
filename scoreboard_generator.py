"""
Scoreboard Generator - Phase 4 (v8 - striker dot, full bowler figures, auto-hide RECENT,
live Hindi commentary ticker)
-------------------------------------------------------------------
Requires the UPDATED app.py (with on_strike + overs/maidens/runs/wickets/economy fields).

Changes in this version (v8):
    - NEW: reads commentary_feed.json (written by commentary_generator.py)
      and shows the latest Hindi commentary line as a ticker in the promo
      banner area for a few seconds, then falls back to the channel
      tagline until the next line arrives. Purely additive - if the feed
      file is missing/empty, behaviour is identical to before.

Changes from v7:
    - Striker indicator: a small glowing dot is drawn on the batter card of
      whichever batsman has on_strike=True (from the API), instead of
      relying on a raw "*" in the name text.
    - Bowler card now shows full figures: O-M-R-W and economy rate, pulled
      straight from the API's current_bowler fields.
    - The RECENT balls panel is completely skipped (not just emptied) when
      there's no ball history yet, and the player row shifts up to fill
      that space - so there's no dead/blank panel sitting on screen.

Requirements:
    pip install requests pillow

Usage:
    python3 scoreboard_generator.py
"""

import requests
import time
import json
import os
import re
import random
import math
from PIL import Image, ImageDraw, ImageFont

# ---------- CONFIG ----------
SCORE_API_URL = "http://localhost:6020/"
MATCH_ID = "144758"          # fallback default if match_id.txt doesn't exist yet
MATCH_ID_FILE = "match_id.txt"   # <-- EDIT THIS FILE ON THE VPS to change matches, no git needed!
POLL_INTERVAL_SECONDS = 10
OUTPUT_IMAGE = "board.png"
BALL_HISTORY_FILE = "ball_history.json"
COMMENTARY_FEED_FILE = "commentary_feed.json"   # written by commentary_generator.py
COMMENTARY_DISPLAY_SECONDS = 6   # how long a fresh commentary line replaces the tagline
WIDTH, HEIGHT = 1280, 720
CHANNEL_TAGLINE = "LIVE HINDI COMMENTARY  \u2022  SUBSCRIBE FOR MORE"
STADIUM_BG_PATH = "assets/stadium_background.jpg"  # real, freely-licensed Unsplash photo
CUSTOM_THUMBNAIL_PATHS = [
    "assets/custom_thumbnail.jpg",
    "assets/custom_thumbnail.jpeg",
    "assets/custom_thumbnail.png",
]  # <-- Upload YOUR OWN image with any of these exact names directly on
   # the VPS (no git needed) to use it in the promo area. If none exist,
   # an auto-generated TEAM1 vs TEAM2 box is used instead.
# -----------------------------

FONT_REGULAR = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
FONT_ANTON = "fonts/Anton-Regular.ttf"
FONT_BEBAS = "fonts/BebasNeue-Regular.ttf"

def load_font(path, size):
    try:
        return ImageFont.truetype(path, size)
    except Exception:
        return ImageFont.load_default()

FONT_TITLE = load_font(FONT_BEBAS, 40)
FONT_BADGE = load_font(FONT_BEBAS, 28)
FONT_SCORE = load_font(FONT_ANTON, 58)
FONT_TEAM = load_font(FONT_BEBAS, 32)
FONT_LABEL = load_font(FONT_BEBAS, 24)
FONT_NAME = load_font(FONT_BEBAS, 36)
FONT_STAT = load_font(FONT_BEBAS, 34)  # bigger font for runs/balls, SR, etc.
FONT_SUB = load_font(FONT_REGULAR, 21)
FONT_AVATAR = load_font(FONT_ANTON, 34)
FONT_BALL = load_font(FONT_ANTON, 22)
FONT_PROMO = load_font(FONT_BEBAS, 26)
FONT_STATUS = load_font(FONT_BEBAS, 32)      # bigger status-bar text (target/CRR/break status)
FONT_YET_TO_BAT = load_font(FONT_BEBAS, 30)
# Hindi commentary needs a font with Devanagari glyph support - DejaVu Sans
# does NOT cover Devanagari, so the ticker uses a Noto Sans Devanagari font
# if present (falls back gracefully to DejaVu/default, which will just show
# boxes for Hindi text if that font truly isn't installed - see notes below).
FONT_DEVANAGARI_PATH = "/usr/share/fonts/truetype/noto/NotoSansDevanagari-Regular.ttf"
FONT_COMMENTARY = load_font(FONT_DEVANAGARI_PATH, 24)

COLOR_ACCENT = (255, 196, 0)
COLOR_LIVE_RED = (220, 40, 40)
COLOR_TEXT = (255, 255, 255)
COLOR_SUBTEXT = (210, 220, 215)
COLOR_STRIKE_DOT = (60, 230, 110)  # bright green - the on-strike indicator

PANEL_FILL = (8, 14, 12, 205)          # semi-transparent panel background
PANEL_FILL_DARK = (4, 8, 14, 220)      # slightly darker/more opaque (header, footer)

BALL_COLORS = {
    "4": (50, 130, 220), "6": (185, 70, 220), "W": (220, 55, 55),
    # Extras (wide/no-ball/leg-bye/bye) get their OWN muted amber color -
    # distinct from the wicket-red "W", so a wide is never visually
    # confused with a wicket on the RECENT strip.
    "Wd": (200, 150, 40), "Nb": (200, 150, 40),
    "Lb": (120, 120, 130), "B": (120, 120, 130),
}
BALL_DEFAULT_COLOR = (75, 95, 85)

TEAM_COLORS = {
    "IND": (26, 60, 150), "AUS": (0, 100, 70), "ENG": (10, 40, 100),
    "PAK": (2, 95, 60), "SA": (0, 122, 61), "RSA": (0, 122, 61),
    "NZ": (25, 25, 25), "WI": (110, 20, 20), "SL": (0, 70, 130),
    "BAN": (0, 100, 70), "AFG": (20, 110, 180), "ZIM": (200, 130, 20),
    "IRE": (20, 120, 70), "SCO": (30, 60, 130), "NED": (200, 90, 20),
    "NEP": (150, 30, 40), "UAE": (150, 40, 40),
}
FALLBACK_COLORS = [(55, 115, 150), (140, 70, 160), (150, 110, 30), (65, 125, 90)]

TEAM_FLAG_STRIPES = {
    "IND": [(255, 153, 51), (255, 255, 255), (19, 136, 8)],
    "PAK": [(1, 90, 60), (255, 255, 255)],
    "AUS": [(0, 0, 139), (255, 255, 255), (206, 17, 38)],
    "ENG": [(206, 17, 38), (255, 255, 255)],
    "SA": [(0, 122, 61), (255, 204, 0), (0, 0, 0)],
    "RSA": [(0, 122, 61), (255, 204, 0), (0, 0, 0)],
    "NZ": [(0, 0, 0), (255, 255, 255)],
    "WI": [(127, 0, 0), (255, 215, 0), (0, 0, 0)],
    "SL": [(0, 71, 171), (255, 193, 7), (215, 25, 32)],
    "BAN": [(0, 106, 78), (213, 32, 39)],
    "AFG": [(0, 0, 0), (210, 16, 52), (0, 151, 66)],
    "ZIM": [(0, 0, 0), (253, 199, 0), (210, 16, 52), (0, 106, 78)],
}

# ---------------- data helpers ----------------

def team_color(team_abbr, fallback_index=0):
    if team_abbr:
        key = team_abbr.strip().upper()
        if key in TEAM_COLORS:
            return TEAM_COLORS[key]
    return FALLBACK_COLORS[fallback_index % len(FALLBACK_COLORS)]

TEAM_NAME_TO_ABBR = {
    "india": "IND", "england": "ENG", "pakistan": "PAK", "australia": "AUS",
    "south africa": "SA", "new zealand": "NZ", "west indies": "WI",
    "sri lanka": "SL", "bangladesh": "BAN", "afghanistan": "AFG",
    "zimbabwe": "ZIM", "ireland": "IRE", "scotland": "SCO",
    "netherlands": "NED", "nepal": "NEP", "united arab emirates": "UAE",
    "uae": "UAE",
}

def guess_team_abbr_from_name(name):
    if not name:
        return None, None
    clean = name.strip()
    # Titles often already use short codes directly (e.g. "ENG vs IND",
    # "SUL vs MSG") - if it looks like one, use it as-is.
    if re.fullmatch(r"[A-Za-z]{2,5}", clean) and clean == clean.upper():
        return clean.upper(), clean
    lower = clean.lower()
    for key, abbr in TEAM_NAME_TO_ABBR.items():
        if key in lower:
            return abbr, clean
    return None, clean

def parse_teams_from_title(title):
    """Match titles always contain 'TEAM1 vs TEAM2, ...' - use this to fill
    in the second team's name/flag when only one team's score is available
    (common for ODI/T20 where only the batting team's score is returned)."""
    if not title:
        return (None, None), (None, None)
    m = re.search(r"([A-Za-z .]+?)\s+vs\.?\s+([A-Za-z .]+?)(?:,|$)", title, re.IGNORECASE)
    if not m:
        return (None, None), (None, None)
    return guess_team_abbr_from_name(m.group(1)), guess_team_abbr_from_name(m.group(2))

def extract_team_abbr(score_str):
    if not score_str:
        return None
    return score_str.split()[0] if score_str.split() else None

def compute_strike_rate(score_str):
    """Parse '15(6)' style batsman score and compute strike rate (runs/balls*100)."""
    if not score_str:
        return None
    m = re.search(r"(\d+)\((\d+)\)", score_str)
    if not m:
        return None
    runs, balls = m.groups()
    try:
        balls_i = int(balls)
        return round(int(runs) / balls_i * 100, 2) if balls_i > 0 else None
    except Exception:
        return None

def compute_run_rate(score_str):
    if not score_str:
        return None
    m = re.search(r"(\d+)/\d+\s*\(([\d.]+)\)", score_str)
    if not m:
        return None
    runs, overs = m.groups()
    try:
        overs_f = float(overs)
        return round(int(runs) / overs_f, 2) if overs_f > 0 else None
    except Exception:
        return None

def format_bowler_figures(bowler):
    """Builds 'O-M-R-W' + economy text from the bowler dict, tolerating
    missing/placeholder fields gracefully (shows nothing rather than
    fake/misleading numbers)."""
    if not bowler:
        return "", ""
    placeholder = "score not found"

    def ok(v):
        return v is not None and v != placeholder and v != ""

    overs, maidens, runs, wickets, economy = (
        bowler.get("overs"), bowler.get("maidens"),
        bowler.get("runs"), bowler.get("wickets"), bowler.get("economy"),
    )
    if ok(overs) and ok(maidens) and ok(runs) and ok(wickets):
        figures = f"{overs}-{maidens}-{runs}-{wickets}"
    else:
        figures = ""
    eco_text = f"ECO {economy}" if ok(economy) else ""
    return figures, eco_text

# Caches the last-known score string for each team abbreviation, keyed to
# the current match. This is needed because Cricbuzz's title usually only
# reports the CURRENTLY-BATTING team's score - once the 2nd innings starts,
# the 1st innings' final total drops out of the title entirely. Without a
# cache we'd lose that score and have to show blanks for team 1.
# _teams_seen_order tracks the ORDER abbreviations first showed up in the
# live scores (most reliable source - it's the site's own scorecard code),
# which we lean on for franchise/league matches whose team names (e.g.
# "Galle Gallants") don't resolve via the country-name dictionary.
_score_cache = {}
_teams_seen_order = []
_score_cache_match_id = None

def _reset_score_cache_if_new_match(match_id):
    global _score_cache, _teams_seen_order, _score_cache_match_id
    if match_id != _score_cache_match_id:
        _score_cache = {}
        _teams_seen_order = []
        _score_cache_match_id = match_id

def _update_score_cache(all_scores):
    for s in all_scores:
        abbr = extract_team_abbr(s)
        if abbr:
            abbr = abbr.upper()
            _score_cache[abbr] = s
            if abbr not in _teams_seen_order:
                _teams_seen_order.append(abbr)

def _name_initials(name):
    return "".join(w[0] for w in re.findall(r"[A-Za-z]+", name or "")).upper()

def _abbr_name_similarity(abbr, name):
    """Crude letter-overlap score between a scorecard abbreviation (e.g.
    'GAG') and a team's word-initials (e.g. 'Galle Gallants' -> 'GG')."""
    if not abbr or not name:
        return -1
    ini = _name_initials(name)
    return sum(1 for ch in abbr.upper() if ch in ini)

def build_score_slots(title, all_scores, match_id):
    """Always returns exactly 2 slots: (label, score_text_or_None, color).
    score_text is None when that team hasn't batted yet - the caller shows
    'YET TO BAT' for those. Uses the cross-poll cache so a completed first
    innings score keeps showing even once the site stops mentioning it.

    IMPORTANT: slot order always follows the order teams actually appeared
    in the live scores (= true batting order), NEVER the title's word order
    ("Nepal vs Netherlands" does NOT necessarily mean Nepal batted first -
    trusting title order here was a real bug)."""
    _reset_score_cache_if_new_match(match_id)
    _update_score_cache(all_scores)

    (t1_abbr, t1_name), (t2_abbr, t2_name) = parse_teams_from_title(title)
    known = list(_teams_seen_order)

    if len(known) >= 2:
        return [
            (known[0], _score_cache.get(known[0]), team_color(known[0], 0)),
            (known[1], _score_cache.get(known[1]), team_color(known[1], 1)),
        ]

    if len(known) == 1:
        slot1_abbr = known[0]

        def _matches_slot1(abbr):
            return bool(abbr) and abbr.upper() == slot1_abbr

        if _matches_slot1(t1_abbr):
            remaining_abbr, remaining_name = t2_abbr, t2_name
        elif _matches_slot1(t2_abbr):
            remaining_abbr, remaining_name = t1_abbr, t1_name
        elif t1_name and t2_name:
            # Neither resolved directly (franchise/league names like "Galle
            # Gallants") - use letter-overlap similarity to figure out
            # which title-name is the team we ALREADY have a score for, so
            # we label the OTHER one for the Yet To Bat slot.
            if _abbr_name_similarity(slot1_abbr, t1_name) >= _abbr_name_similarity(slot1_abbr, t2_name):
                remaining_abbr, remaining_name = t2_abbr, t2_name
            else:
                remaining_abbr, remaining_name = t1_abbr, t1_name
        else:
            remaining_abbr, remaining_name = (t2_abbr or t1_abbr), (t2_name or t1_name)

        remaining_label = remaining_abbr or (_name_initials(remaining_name)[:4] if remaining_name else "") or "TBC"
        return [
            (slot1_abbr, _score_cache.get(slot1_abbr), team_color(slot1_abbr, 0)),
            (remaining_label, None, team_color(remaining_abbr, 1)),
        ]

    # Nothing known yet at all (very first fetch ever, before either team
    # has faced a ball) - show placeholders from the title's two team names.
    # Order genuinely doesn't matter yet here since neither has batted.
    return [
        (t1_abbr or _name_initials(t1_name)[:4] or "-", None, team_color(t1_abbr, 0)),
        (t2_abbr or _name_initials(t2_name)[:4] or "-", None, team_color(t2_abbr, 1)),
    ]

def get_current_match_id():
    """Reads match_id.txt fresh every time - change matches by editing this
    ONE file directly on the VPS, no git push/pull, no restart needed."""
    if os.path.exists(MATCH_ID_FILE):
        try:
            with open(MATCH_ID_FILE) as f:
                value = f.read().strip()
            if value:
                return value
        except Exception:
            pass
    return MATCH_ID

def fetch_state():
    try:
        current_match_id = get_current_match_id()
        resp = requests.get(SCORE_API_URL, params={"score": current_match_id}, timeout=10)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        print(f"[warn] could not fetch score: {e}")
        return None

def fetch_ball_history():
    if not os.path.exists(BALL_HISTORY_FILE):
        return []
    try:
        with open(BALL_HISTORY_FILE) as f:
            return json.load(f)
    except Exception:
        return []

def fetch_latest_commentary():
    """Reads the latest Hindi commentary line written by
    commentary_generator.py (commentary_feed.json), if it's still
    "fresh" (younger than COMMENTARY_DISPLAY_SECONDS). Returns None if the
    feed file is missing, empty, unreadable, or the latest line has aged
    out - in which case the caller falls back to the normal tagline."""
    if not os.path.exists(COMMENTARY_FEED_FILE):
        return None
    try:
        with open(COMMENTARY_FEED_FILE, encoding="utf-8") as f:
            entries = json.load(f)
        if not entries:
            return None
        latest = entries[-1]
        age = time.time() - latest.get("ts", 0)
        if age <= COMMENTARY_DISPLAY_SECONDS:
            return latest.get("text")
    except Exception:
        return None
    return None

def fit_text_to_width(draw, text, font, max_width):
    """Truncates text with a trailing '...' if it's wider than max_width,
    so a long Hindi commentary line never overflows/collides with the
    footer text next to it."""
    if not text:
        return text
    bbox = draw.textbbox((0, 0), text, font=font)
    if bbox[2] - bbox[0] <= max_width:
        return text
    ellipsis = "..."
    lo, hi = 0, len(text)
    while lo < hi:
        mid = (lo + hi) // 2
        candidate = text[:mid].rstrip() + ellipsis
        cbbox = draw.textbbox((0, 0), candidate, font=font)
        if cbbox[2] - cbbox[0] <= max_width:
            lo = mid + 1
        else:
            hi = mid
    return text[:max(lo - 1, 1)].rstrip() + ellipsis

# ---------------- full-frame stadium background (self-drawn, not a photo) ----------------

def draw_glow(layer_draw, cx, cy, radius, color):
    for r, alpha in [(radius, 45), (radius * 0.65, 100), (radius * 0.35, 170), (radius * 0.15, 255)]:
        layer_draw.ellipse([(cx - r, cy - r), (cx + r, cy + r)], fill=(*color, int(alpha)))

def get_custom_thumbnail():
    """Looks for a user-uploaded custom thumbnail file (re-checks every
    call so a freshly-uploaded file is picked up without restarting)."""
    for path in CUSTOM_THUMBNAIL_PATHS:
        if os.path.exists(path):
            try:
                return Image.open(path).convert("RGB")
            except Exception:
                continue
    return None

def cover_resize_crop(img, target_w, target_h):
    """Resize+crop an image to exactly fill target_w x target_h (like CSS background-size: cover)."""
    src_w, src_h = img.size
    src_ratio = src_w / src_h
    target_ratio = target_w / target_h
    if src_ratio > target_ratio:
        new_h = target_h
        new_w = int(new_h * src_ratio)
    else:
        new_w = target_w
        new_h = int(new_w / src_ratio)
    img = img.resize((new_w, new_h), Image.LANCZOS)
    left = (new_w - target_w) // 2
    top = (new_h - target_h) // 2
    return img.crop((left, top, left + target_w, top + target_h))

def apply_readability_gradient(img):
    """Darken the top and bottom of the photo so white text stays readable,
    while keeping the middle brighter so the stadium photo still shows through."""
    w, h = img.size
    overlay = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    odraw = ImageDraw.Draw(overlay)
    for y in range(h):
        if y < h * 0.30:
            alpha = int(190 * (1 - y / (h * 0.30)))
        elif y > h * 0.78:
            alpha = int(190 * (y - h * 0.78) / (h * 0.22))
        else:
            alpha = 60
        odraw.line([(0, y), (w, y)], fill=(2, 4, 8, alpha))
    return Image.alpha_composite(img.convert("RGBA"), overlay).convert("RGB")

def build_stadium_background(w, h):
    """Uses the real (freely-licensed) stadium photo if present, falling
    back to a self-drawn illustrated stadium if the file isn't found."""
    if os.path.exists(STADIUM_BG_PATH):
        try:
            photo = Image.open(STADIUM_BG_PATH).convert("RGB")
            photo = cover_resize_crop(photo, w, h)
            return apply_readability_gradient(photo)
        except Exception as e:
            print(f"[warn] could not load stadium photo, using illustration instead: {e}")
    return build_illustrated_stadium_fallback(w, h)

def build_illustrated_stadium_fallback(w, h):
    """Self-drawn stadium graphic (floodlights, crowd texture, mowed-grass
    field) - used only if the real photo file is missing."""
    base = Image.new("RGB", (w, h), (5, 9, 20))
    draw = ImageDraw.Draw(base)
    sky_h = int(h * 0.24)
    for i in range(sky_h):
        ratio = i / sky_h
        r = int(4 + (18 - 4) * ratio)
        g = int(8 + (22 - 8) * ratio)
        b = int(22 + (48 - 22) * ratio)
        draw.line([(0, i), (w, i)], fill=(r, g, b))
    draw.rectangle([(0, sky_h - 20), (w, h)], fill=(48, 32, 28))
    rnd = random.Random(7)
    for _ in range(2200):
        px = rnd.randint(0, w)
        py = rnd.randint(sky_h - 10, h)
        shade = rnd.choice([(235, 205, 165), (205, 165, 130), (175, 135, 105), (245, 215, 175), (90, 60, 45)])
        draw.ellipse([(px, py), (px + 2, py + 2)], fill=shade)
    glow_layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    gdraw = ImageDraw.Draw(glow_layer)
    fl_top, fl_base = 8, sky_h - 8
    for fx_ratio in (0.06, 0.28, 0.50, 0.72, 0.94):
        fx = int(w * fx_ratio)
        draw.rectangle([(fx - 4, fl_top + 16), (fx + 4, fl_base)], fill=(60, 60, 65))
        draw.rectangle([(fx - 26, fl_top), (fx + 26, fl_top + 18)], fill=(45, 45, 50), outline=(85, 85, 90), width=1)
        draw_glow(gdraw, fx, fl_top + 10, 55, (255, 228, 150))
    base = Image.alpha_composite(base.convert("RGBA"), glow_layer).convert("RGB")
    draw = ImageDraw.Draw(base)
    field_pad_x = int(w * 0.06)
    fx0, fy0 = field_pad_x, sky_h + 10
    fx1, fy1 = w - field_pad_x, h - int(h * 0.05)
    ring_greens = [(32, 108, 52), (42, 124, 60)]
    for i in range(8, 0, -1):
        ratio = i / 8
        ex0 = fx0 + (fx1 - fx0) * (1 - ratio) / 2
        ex1 = fx1 - (fx1 - fx0) * (1 - ratio) / 2
        ey0 = fy0 + (fy1 - fy0) * (1 - ratio) / 2
        ey1 = fy1 - (fy1 - fy0) * (1 - ratio) / 2
        draw.ellipse([(ex0, ey0), (ex1, ey1)], fill=ring_greens[i % 2])
    draw.ellipse([(fx0, fy0), (fx1, fy1)], outline=(235, 235, 235), width=2)
    cx, cy = (fx0 + fx1) / 2, (fy0 + fy1) / 2
    pw, ph = w * 0.11, h * 0.055
    draw.rounded_rectangle([(cx - pw / 2, cy - ph / 2), (cx + pw / 2, cy + ph / 2)], radius=4,
                            fill=(205, 182, 130), outline=(230, 230, 230), width=1)
    draw.line([(cx, cy - ph / 2 + 3), (cx, cy + ph / 2 - 3)], fill=(230, 230, 230), width=1)
    vignette = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    ImageDraw.Draw(vignette).rectangle([(0, 0), (w, h)], fill=(0, 0, 0, 55))
    return Image.alpha_composite(base.convert("RGBA"), vignette).convert("RGB")

# ---------------- foreground UI (drawn as semi-transparent overlay) ----------------

def panel_rect(odraw, x, y, w, h, radius=12, fill=PANEL_FILL, outline=None, width=2):
    odraw.rounded_rectangle([(x, y), (x + w, y + h)], radius=radius, fill=fill, outline=outline, width=width)

FLAG_DIR = "assets/flags"  # real public-domain flag PNGs go here (see setup notes)

_flag_cache = {}

def load_flag_image(team_abbr):
    key = (team_abbr or "").strip().upper()
    if key in _flag_cache:
        return _flag_cache[key]
    path = os.path.join(FLAG_DIR, f"{key}.png")
    img = None
    if os.path.exists(path):
        try:
            img = Image.open(path).convert("RGB")
        except Exception:
            img = None
    _flag_cache[key] = img
    return img

def draw_flag_circle(img, draw, cx, cy, radius, team_abbr):
    size = radius * 2
    real_flag = load_flag_image(team_abbr)

    if real_flag is not None:
        flag_img = cover_resize_crop(real_flag, size, size)
    else:
        # fallback: simple horizontal color stripes (correct orientation
        # for tricolor-style flags) if the real PNG isn't available
        key = (team_abbr or "").strip().upper()
        colors = TEAM_FLAG_STRIPES.get(key)
        flag_img = Image.new("RGB", (size, size), (40, 40, 40))
        fdraw = ImageDraw.Draw(flag_img)
        if colors:
            n = len(colors)
            band_h = size / n
            for i, c in enumerate(colors):
                fdraw.rectangle([(0, i * band_h), (size, (i + 1) * band_h)], fill=c)
        else:
            fdraw.rectangle([(0, 0), (size, size)], fill=team_color(team_abbr))

    mask_hi = Image.new("L", (size * 4, size * 4), 0)
    ImageDraw.Draw(mask_hi).ellipse([(0, 0), (size * 4, size * 4)], fill=255)
    mask = mask_hi.resize((size, size), Image.LANCZOS)
    img.paste(flag_img, (cx - radius, cy - radius), mask)

def smooth_circle_layer(radius, fill_color, ss=4):
    """Render a circle at a higher resolution then downscale with LANCZOS
    to get smooth, anti-aliased edges (PIL's basic ellipse() is jagged)."""
    size = radius * 2 * ss
    tmp = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    ImageDraw.Draw(tmp).ellipse([(0, 0), (size, size)], fill=(*fill_color, 255) if len(fill_color) == 3 else fill_color)
    return tmp.resize((radius * 2, radius * 2), Image.LANCZOS)

def draw_avatar(img, cx, cy, radius, letter, color):
    white_ring = smooth_circle_layer(radius + 2, (255, 255, 255))
    img.paste(white_ring, (cx - radius - 2, cy - radius - 2), white_ring)
    circle = smooth_circle_layer(radius, color)
    img.paste(circle, (cx - radius, cy - radius), circle)
    draw = ImageDraw.Draw(img)
    bbox = draw.textbbox((0, 0), letter, font=FONT_AVATAR)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    draw.text((cx - tw / 2, cy - th / 2 - bbox[1]), letter, font=FONT_AVATAR, fill=COLOR_TEXT)

def draw_strike_dot(img, cx, cy, radius=9, pulse=0.0):
    """Small glowing green dot marking the batsman currently on strike -
    drawn at the bottom-right edge of their avatar circle. `pulse` (0..1,
    driven by wall-clock time in the main loop) makes the glow gently
    breathe in and out across successive board.png regenerations."""
    breathe = 0.5 + 0.5 * math.sin(2 * math.pi * pulse)  # 0..1
    glow_r = radius + 3 + int(3 * breathe)
    glow_alpha = int(70 + 50 * breathe)
    glow = smooth_circle_layer(glow_r, (*COLOR_STRIKE_DOT, glow_alpha))
    img.paste(glow, (cx - glow_r, cy - glow_r), glow)
    white_ring = smooth_circle_layer(radius + 2, (255, 255, 255))
    img.paste(white_ring, (cx - radius - 2, cy - radius - 2), white_ring)
    dot = smooth_circle_layer(radius, COLOR_STRIKE_DOT)
    img.paste(dot, (cx - radius, cy - radius), dot)

def score_panel(img, odraw, draw, x, y, w, h, score_str, color):
    panel_rect(odraw, x, y, w, h, radius=12, fill=(*color, 210), outline=(*COLOR_ACCENT, 255), width=2)
    team = extract_team_abbr(score_str) or "-"
    rest = score_str[len(team):].strip() if score_str else "-"
    draw_flag_circle(img, draw, x + w - 44, y + h // 2, 26, team)
    draw.text((x + 16, y + 12), team, font=FONT_TEAM, fill=COLOR_TEXT)
    bbox = draw.textbbox((0, 0), rest or "-", font=FONT_SCORE)
    text_h = bbox[3] - bbox[1]
    score_y = y + 40 + (h - 40 - text_h) / 2 - bbox[1]
    draw.text((x + 16, score_y), rest or "-", font=FONT_SCORE, fill=COLOR_TEXT)

def batter_card(odraw, draw, x, y, w, h, name, score, color):
    panel_rect(odraw, x, y, w, h, outline=(*color, 255), width=3)
    draw_avatar(draw, x + 46, y + h // 2, 30, (name[0].upper() if name else "?"), color)
    text_x = x + 90
    draw.text((text_x, y + 14), name or "-", font=FONT_NAME, fill=COLOR_TEXT)
    draw.text((text_x, y + 48), score or "-", font=FONT_SUB, fill=COLOR_ACCENT)

def bowler_card(odraw, draw, x, y, w, h, name, color):
    panel_rect(odraw, x, y, w, h, outline=(*color, 255), width=3)
    draw_avatar(draw, x + w - 46, y + h // 2, 30, (name[0].upper() if name else "?"), color)
    draw.text((x + 16, y + 12), "BOWLER", font=FONT_LABEL, fill=COLOR_SUBTEXT)
    draw.text((x + 16, y + 38), name or "-", font=FONT_NAME, fill=COLOR_TEXT)

def draw_recent_balls(odraw, draw, x, y, w, h, history):
    panel_rect(odraw, x, y, w, h, outline=(*COLOR_ACCENT, 200), width=1)
    draw.text((x + 16, y + 8), "RECENT", font=FONT_LABEL, fill=COLOR_SUBTEXT)
    if not history:
        draw.text((x + 16, y + 36), "waiting for play...", font=FONT_SUB, fill=COLOR_SUBTEXT)
        return
    circle_r = 17
    start_x = x + w - 20
    cy = y + h // 2 + 6
    for tag in reversed(history[-10:]):
        color = BALL_COLORS.get(tag, BALL_DEFAULT_COLOR)
        cx = start_x - circle_r
        draw.ellipse([(cx - circle_r - 1, cy - circle_r - 1), (cx + circle_r + 1, cy + circle_r + 1)], fill=(255, 255, 255))
        draw.ellipse([(cx - circle_r, cy - circle_r), (cx + circle_r, cy + circle_r)], fill=color)
        bbox = draw.textbbox((0, 0), tag, font=FONT_BALL)
        tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
        draw.text((cx - tw / 2, cy - th / 2 - bbox[1]), tag, font=FONT_BALL, fill=COLOR_TEXT)
        start_x -= (circle_r * 2 + 8)
        if start_x < x + 100:
            break

def clipped_rect(x, y, w, h, cut=20):
    """A rectangle with the top-right corner sliced off diagonally - a
    common 'sporty' broadcast-graphic silhouette instead of a plain box."""
    return [(x, y), (x + w - cut, y), (x + w, y + cut), (x + w, y + h), (x, y + h)]

def paste_polygon_gradient(base_img, points, color_top, color_bottom, alpha):
    """Fill a polygon with a soft top-to-bottom gradient at the given alpha,
    properly alpha-blended onto whatever is already in base_img at that
    spot (so the stadium photo still shows through, semi-transparently)."""
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    x0, y0, x1, y1 = min(xs), min(ys), max(xs), max(ys)
    w, h = max(int(x1 - x0), 1), max(int(y1 - y0), 1)

    mask = Image.new("L", (w, h), 0)
    ImageDraw.Draw(mask).polygon([(px - x0, py - y0) for px, py in points], fill=alpha)

    grad = Image.new("RGB", (w, h))
    gdraw = ImageDraw.Draw(grad)
    for row in range(h):
        ratio = row / h
        c = tuple(int(color_top[i] + (color_bottom[i] - color_top[i]) * ratio) for i in range(3))
        gdraw.line([(0, row), (w, row)], fill=c)

    grad_rgba = grad.convert("RGBA")
    grad_rgba.putalpha(mask)

    region = base_img.crop((int(x0), int(y0), int(x0) + w, int(y0) + h))
    blended = Image.alpha_composite(region, grad_rgba)
    base_img.paste(blended, (int(x0), int(y0)))

CRICKET_ICON_PATH = "assets/icons/bat_only.png"
_cricket_icon_cache = None

def get_cricket_icon():
    global _cricket_icon_cache
    if _cricket_icon_cache is None and os.path.exists(CRICKET_ICON_PATH):
        try:
            _cricket_icon_cache = Image.open(CRICKET_ICON_PATH).convert("RGBA")
        except Exception:
            _cricket_icon_cache = False
    return _cricket_icon_cache or None

def draw_bat_icon(img, cx, cy, h, color):
    icon = get_cricket_icon()
    if icon is not None:
        w0, h0 = icon.size
        size_h = int(h)
        size_w = int(size_h * w0 / h0)
        resized = icon.resize((size_w, size_h), Image.LANCZOS)
        img.paste(resized, (int(cx - size_w / 2), int(cy - size_h / 2)), resized)
        return
    # fallback if the icon file isn't present
    draw = ImageDraw.Draw(img)
    blade_w, blade_h = h * 0.34, h * 0.58
    draw.rounded_rectangle([(cx - blade_w / 2, cy - h / 2), (cx + blade_w / 2, cy - h / 2 + blade_h)],
                            radius=int(blade_w * 0.35), fill=color)

def draw_ball_icon(img, cx, cy, r):
    """A proper-looking red cricket ball (not team-colored) with a visible
    stitched seam - separate and distinct from the bat icon."""
    ball_red = (178, 30, 30)
    circle = smooth_circle_layer(r, ball_red)
    img.paste(circle, (int(cx - r), int(cy - r)), circle)
    draw = ImageDraw.Draw(img)
    # curved seam line across the ball
    draw.arc([(cx - r, cy - r * 0.9), (cx + r, cy + r * 0.9)], start=0, end=180, fill=(255, 255, 255), width=2)
    # small seam stitches
    for t in range(-2, 3):
        sx = cx + t * (r * 0.32)
        sy = cy - (r * 0.9) * (1 - (t * 0.28) ** 2) ** 0.5 if abs(t * 0.28) <= 1 else cy
        draw.line([(sx - 2, sy - 2), (sx + 2, sy + 2)], fill=(255, 255, 255), width=1)



def draw_ribbon_tag(draw, x, y, text, font, fill, text_color=(255, 255, 255)):
    bbox = draw.textbbox((0, 0), text, font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    pad_x, pad_y, notch = 10, 5, 8
    w, h = tw + pad_x * 2 + notch, th + pad_y * 2
    pts = [(x, y), (x + w - notch, y), (x + w, y + h / 2), (x + w - notch, y + h), (x, y + h)]
    draw.polygon(pts, fill=fill)
    draw.text((x + pad_x, y + pad_y - bbox[1]), text, font=font, fill=text_color)
    return w, h

def draw_corner_accent(draw, x, y, size, color, corner="tl"):
    if corner == "tl":
        pts = [(x, y), (x + size, y), (x, y + size)]
    elif corner == "tr":
        pts = [(x, y), (x - size, y), (x, y + size)]
    else:
        pts = [(x, y), (x + size, y), (x, y - size)]
    draw.polygon(pts, fill=color)

# ---------------- main render ----------------

POPUP_COLORS = {"FOUR": (50, 130, 220), "SIX": (185, 70, 220), "WICKET": (220, 55, 55)}
POPUP_LABELS = {"FOUR": "FOUR !", "SIX": "SIX !", "WICKET": "WICKET !"}

def draw_event_popup(img, draw, popup, progress):
    """Draws a big FOUR!/SIX!/WICKET! banner that pops in, holds, then
    fades out across the popup's lifetime (progress goes 0->1). Because
    go_live.py continuously re-reads board.png at ~2fps, regenerating this
    file every ~0.3-0.5s with a slightly different `progress` value makes
    it appear as a real animated pop-up in the actual live stream."""
    if not popup or progress is None:
        return
    if progress < 0.15:
        t = progress / 0.15
        alpha = t
        scale = 0.7 + 0.3 * t
    elif progress > 0.75:
        t = (progress - 0.75) / 0.25
        alpha = max(0.0, 1 - t)
        scale = 1.0
    else:
        alpha = 1.0
        scale = 1.0

    color = POPUP_COLORS.get(popup, COLOR_ACCENT)
    label = POPUP_LABELS.get(popup, popup)

    cx, cy = WIDTH // 2, 300
    base_w, base_h = 360, 116
    w, h = max(1, int(base_w * scale)), max(1, int(base_h * scale))

    banner = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    bdraw = ImageDraw.Draw(banner)
    bdraw.rounded_rectangle(
        [(0, 0), (w - 1, h - 1)], radius=max(4, int(22 * scale)),
        fill=(*color, int(235 * alpha)),
        outline=(255, 255, 255, int(255 * alpha)), width=max(2, int(3 * scale)),
    )
    bbox = bdraw.textbbox((0, 0), label, font=FONT_SCORE)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    bdraw.text(((w - tw) / 2, (h - th) / 2 - bbox[1]), label, font=FONT_SCORE, fill=(255, 255, 255, int(255 * alpha)))

    img.paste(banner, (cx - w // 2, cy - h // 2), banner)

_cached_stadium_bg = None

def render_board(state, ball_history, popup=None, popup_progress=0.0, pulse_phase=0.0):
    global _cached_stadium_bg
    if _cached_stadium_bg is None:
        _cached_stadium_bg = build_stadium_background(WIDTH, HEIGHT)
    base = _cached_stadium_bg

    overlay = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
    odraw = ImageDraw.Draw(overlay)

    title = (state.get("title") if state else None) or "Waiting for live match..."
    score = (state.get("score") if state else None) or "--"
    all_scores = (state.get("all_scores") if state else None) or []
    target_info = (state.get("target_info") if state else None) or ""
    match_status = (state.get("match_status") if state else None) or ""
    match_result = (state.get("match_result") if state else None) or ""
    batsmen = (state.get("current_batsmen") if state else None) or []
    bowler = (state or {}).get("current_bowler") or {}
    bowler_name = bowler.get("name")
    if bowler_name == "score not found":
        bowler_name = None
    bowler_figures, bowler_eco = format_bowler_figures(bowler)

    has_recent = bool(ball_history)

    # Header strip
    odraw.rectangle([(0, 0), (WIDTH, 66)], fill=(4, 8, 14, 220))
    odraw.rectangle([(0, 64), (WIDTH, 68)], fill=(*COLOR_ACCENT, 255))
    badge_text = "RESULT" if match_result else "LIVE"
    badge_color = (30, 150, 80) if match_result else COLOR_LIVE_RED
    badge_w = 132 if match_result else 92
    badge_x0 = WIDTH - 24 - badge_w
    odraw.rounded_rectangle([(badge_x0, 16), (WIDTH - 24, 50)], radius=8, fill=(*badge_color, 255))

    # Score panels - ALWAYS 2, side by side: 1st innings + 2nd innings.
    # If the 2nd team hasn't batted yet, that slot shows "YET TO BAT"
    # instead of a score (see build_score_slots / _score_cache above).
    panel_y, panel_h = 80, 128
    panel_w = (WIDTH - 24 * 3) // 2
    score_slots = build_score_slots(title, all_scores, get_current_match_id())
    score_positions = [(24, score_slots[0]), (24 * 2 + panel_w, score_slots[1])]
    for px, (abbr, s, c) in score_positions:
        pts = clipped_rect(px, panel_y, panel_w, panel_h, cut=28)
        darker = tuple(max(0, ch - 60) for ch in c)
        top_color = tuple(min(255, ch + 25) for ch in c)
        # dim the panel slightly when the team hasn't batted yet, so the
        # active innings visually stands out more
        alpha = 225 if s else 165
        paste_polygon_gradient(overlay, pts, top_color, darker, alpha)
        # (border intentionally removed)

    # Status bar - shows (in priority order) an innings-break/stoppage
    # status, then a target/chase line, then the current run rate. Text
    # is bigger now (FONT_STATUS) so it reads clearly on a stream.
    status_y = panel_y + panel_h + 14
    status_h = 42
    odraw.rounded_rectangle([(24, status_y), (WIDTH - 24, status_y + status_h)], radius=8, fill=PANEL_FILL_DARK)

    # RECENT strip - FULL WIDTH - only drawn when there IS ball history yet.
    # When there's none (e.g. very start of an innings/match), it's skipped
    # entirely rather than shown empty, and the player row moves up to
    # take its place.
    recent_y = status_y + status_h + 14
    recent_h = 78
    if has_recent:
        recent_pts = clipped_rect(24, recent_y, WIDTH - 48, recent_h, cut=20)
        card_top, card_bottom = (14, 34, 24, 230), (5, 12, 9, 230)
        paste_polygon_gradient(overlay, recent_pts, card_top, card_bottom, 225)
        row_y = recent_y + recent_h + 14
    else:
        card_top, card_bottom = (14, 34, 24, 230), (5, 12, 9, 230)
        row_y = recent_y

    # Player row: 2 batters + 1 bowler, SIDE BY SIDE (matches reference's
    # bottom row of 3 cards, instead of stacked)
    row_h = 158
    gap = 14
    card_w3 = (WIDTH - 48 - gap * 2) // 3
    card_x_positions = [24, 24 + card_w3 + gap, 24 + 2 * (card_w3 + gap)]
    for cx0 in card_x_positions:
        pts = clipped_rect(cx0, row_y, card_w3, row_h, cut=18)
        paste_polygon_gradient(overlay, pts, card_top, card_bottom, 225)

    # Match info thumbnail area (fills the empty space instead of leaving
    # it blank). If you upload your OWN custom_thumbnail.jpg/png directly
    # onto the VPS (no git needed - just like match_id.txt), it will be
    # used here automatically. Otherwise falls back to an auto-generated
    # TEAM1 vs TEAM2 box using the flags we already have.
    thumb_y = row_y + row_h + 16
    thumb_h = (HEIGHT - 74) - thumb_y - 14
    have_thumb = thumb_h > 60
    custom_thumb_img = get_custom_thumbnail() if have_thumb else None

    if have_thumb and custom_thumb_img is not None:
        thumb_w = WIDTH - 48
        thumb_x = 24
        fitted = cover_resize_crop(custom_thumb_img, thumb_w, thumb_h).convert("RGB")
        thumb_mask_pts = [(px - thumb_x, py - thumb_y) for px, py in clipped_rect(thumb_x, thumb_y, thumb_w, thumb_h, cut=16)]
        mask_hi = Image.new("L", (thumb_w, thumb_h), 0)
        ImageDraw.Draw(mask_hi).polygon(thumb_mask_pts, fill=255)
        fitted_rgba = fitted.convert("RGBA")
        fitted_rgba.putalpha(mask_hi)
        region = overlay.crop((thumb_x, thumb_y, thumb_x + thumb_w, thumb_y + thumb_h))
        blended = Image.alpha_composite(region, fitted_rgba)
        overlay.paste(blended, (thumb_x, thumb_y))
    elif have_thumb:
        thumb_w = 420
        thumb_x = (WIDTH - thumb_w) // 2
        thumb_pts = clipped_rect(thumb_x, thumb_y, thumb_w, thumb_h, cut=16)
        paste_polygon_gradient(overlay, thumb_pts, (18, 24, 40, 230), (8, 10, 18, 230), 220)

    # Promo banner + footer backgrounds
    odraw.rectangle([(0, HEIGHT - 74), (WIDTH, HEIGHT - 34)], fill=(20, 30, 60, 235))
    odraw.rectangle([(0, HEIGHT - 74), (WIDTH, HEIGHT - 71)], fill=(*COLOR_ACCENT, 255))
    odraw.rectangle([(0, HEIGHT - 34), (WIDTH, HEIGHT)], fill=(4, 8, 14, 235))

    # Composite the semi-transparent overlay onto the stadium background
    composited = Image.alpha_composite(base.convert("RGBA"), overlay).convert("RGB")
    draw = ImageDraw.Draw(composited)

    # ---- Now draw all opaque foreground content (text, avatars, flags, icons) ----

    draw_corner_accent(draw, 0, 0, 22, COLOR_ACCENT, "tl")
    draw_corner_accent(draw, WIDTH, 0, 22, (60, 80, 200), "tr")
    draw.text((24, 18), title[:66], font=FONT_TITLE, fill=COLOR_TEXT)
    live_box = (badge_x0, 16, WIDTH - 24, 50)
    lbbox = draw.textbbox((0, 0), badge_text, font=FONT_BADGE)
    ltw, lth = lbbox[2] - lbbox[0], lbbox[3] - lbbox[1]
    ltx = live_box[0] + ((live_box[2] - live_box[0]) - ltw) / 2
    lty = live_box[1] + ((live_box[3] - live_box[1]) - lth) / 2 - lbbox[1]
    draw.text((ltx, lty), badge_text, font=FONT_BADGE, fill=COLOR_TEXT)

    for px, (abbr, s, c) in score_positions:
        team = abbr or "-"
        draw_flag_circle(composited, draw, px + panel_w - 44, panel_y + panel_h // 2, 26, team)
        draw.text((px + 16, panel_y + 12), team, font=FONT_TEAM, fill=COLOR_TEXT)
        if s:
            rest = s[len(team):].strip() if team and s.upper().startswith(team.upper()) else s
            bbox = draw.textbbox((0, 0), rest or "-", font=FONT_SCORE)
            text_h = bbox[3] - bbox[1]
            score_y = panel_y + 40 + (panel_h - 40 - text_h) / 2 - bbox[1]
            draw.text((px + 16, score_y), rest or "-", font=FONT_SCORE, fill=COLOR_TEXT)
        else:
            yts_text = "YET TO BAT"
            bbox = draw.textbbox((0, 0), yts_text, font=FONT_YET_TO_BAT)
            text_h = bbox[3] - bbox[1]
            score_y = panel_y + 40 + (panel_h - 40 - text_h) / 2 - bbox[1]
            draw.text((px + 16, score_y), yts_text, font=FONT_YET_TO_BAT, fill=COLOR_SUBTEXT)

    # Priority: final result > innings-break/stoppage status > chase/target line > run rate.
    if match_result:
        status_text = match_result.upper()
        status_color = COLOR_ACCENT
    elif match_status:
        status_text = match_status.upper()
        status_color = COLOR_ACCENT
    elif target_info:
        status_text = target_info
        status_color = COLOR_TEXT
    else:
        crr = compute_run_rate(all_scores[0] if all_scores else score)
        status_text = f"CRR: {crr}" if crr is not None else "LIVE UPDATES"
        status_color = COLOR_ACCENT
    sbbox = draw.textbbox((0, 0), status_text, font=FONT_STATUS)
    stext_h = sbbox[3] - sbbox[1]
    stext_y = status_y + (status_h - stext_h) / 2 - sbbox[1]
    draw.text((36, stext_y), status_text, font=FONT_STATUS, fill=status_color)

    # RECENT strip foreground content (only when there's history to show)
    if has_recent:
        draw_ribbon_tag(draw, 24, recent_y + 8, "RECENT", FONT_LABEL, (*COLOR_ACCENT, 255), text_color=(20, 20, 20))
        circle_r = 23
        start_x = WIDTH - 48
        cy = recent_y + recent_h // 2 + 4
        for tag in reversed(ball_history[-6:]):
            color = BALL_COLORS.get(tag, BALL_DEFAULT_COLOR)
            cx = start_x - circle_r
            white_bg = smooth_circle_layer(circle_r + 1, (255, 255, 255))
            composited.paste(white_bg, (cx - circle_r - 1, cy - circle_r - 1), white_bg)
            ball_circle = smooth_circle_layer(circle_r, color)
            composited.paste(ball_circle, (cx - circle_r, cy - circle_r), ball_circle)
            bbox = draw.textbbox((0, 0), tag, font=FONT_BALL)
            tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
            draw.text((cx - tw / 2, cy - th / 2 - bbox[1]), tag, font=FONT_BALL, fill=COLOR_TEXT)
            start_x -= (circle_r * 2 + 8)
            if start_x < 24 + 160:
                break

    # Player row foreground: batter 1, batter 2, bowler
    for i in range(2):
        b = batsmen[i] if i < len(batsmen) else {}
        name = b.get("name")
        if name == "score not found":
            name = None
        on_strike = bool(b.get("on_strike"))
        cx0 = card_x_positions[i]
        sr = compute_strike_rate(b.get("score"))
        draw_ribbon_tag(draw, cx0, row_y + 8, "BATTER", FONT_LABEL, (*FALLBACK_COLORS[2], 255))
        draw_bat_icon(composited, cx0 + card_w3 - 36, row_y + 76, 64, FALLBACK_COLORS[2])
        draw_avatar(composited, cx0 + 48, row_y + 76, 32, (name[0].upper() if name else "?"), FALLBACK_COLORS[2])
        if on_strike:
            # small glowing dot at the bottom-right of the avatar marks
            # the batsman who is currently on strike
            draw_strike_dot(composited, cx0 + 48 + 23, row_y + 76 + 23, pulse=pulse_phase)
        name_x = cx0 + 92
        display_name = name or "New Batsman"
        display_score = (b.get("score") or "") if name else ""
        if display_score == "score not found":
            display_score = ""
        draw.text((name_x, row_y + 44), display_name, font=FONT_NAME, fill=COLOR_TEXT if name else COLOR_SUBTEXT)
        if display_score:
            draw.text((name_x, row_y + 88), display_score, font=FONT_STAT, fill=COLOR_ACCENT)
        sr_text = f"SR: {sr}" if sr is not None else ""
        if sr_text:
            draw.text((cx0 + 16, row_y + row_h - 32), sr_text, font=FONT_SUB, fill=COLOR_SUBTEXT)

    bowler_x = card_x_positions[2]
    draw_ribbon_tag(draw, bowler_x, row_y + 8, "BOWLER", FONT_LABEL, (*FALLBACK_COLORS[3], 255))
    draw_ball_icon(composited, bowler_x + card_w3 - 44, row_y + 76, 26)
    draw_avatar(composited, bowler_x + 48, row_y + 76, 32, (bowler_name[0].upper() if bowler_name else "?"), FALLBACK_COLORS[3])
    draw.text((bowler_x + 92, row_y + 44), bowler_name or "-", font=FONT_NAME, fill=COLOR_TEXT)
    if bowler_figures:
        draw.text((bowler_x + 92, row_y + 88), bowler_figures, font=FONT_STAT, fill=COLOR_ACCENT)
    if bowler_eco:
        draw.text((bowler_x + 16, row_y + row_h - 32), bowler_eco, font=FONT_SUB, fill=COLOR_SUBTEXT)

    if have_thumb and custom_thumb_img is None:
        team1 = score_positions[0][1][0] if score_positions else None
        team2 = score_positions[1][1][0] if len(score_positions) > 1 else None
        team1_label, team2_label = team1, team2
        if not team2:
            (t1_abbr, t1_name), (t2_abbr, t2_name) = parse_teams_from_title(title)
            # only fill in the team we're missing - don't override a real score's team
            if not team1:
                team1 = t1_abbr
                team1_label = t1_abbr or t1_name
            team2 = t2_abbr
            team2_label = t2_abbr or t2_name
        cy_thumb = thumb_y + thumb_h // 2
        draw_flag_circle(composited, draw, thumb_x + 46, cy_thumb, 28, team1)
        draw_flag_circle(composited, draw, thumb_x + thumb_w - 46, cy_thumb, 28, team2)
        vs_text = "VS"
        vbbox = draw.textbbox((0, 0), vs_text, font=FONT_NAME)
        vtw = vbbox[2] - vbbox[0]
        draw.text((thumb_x + thumb_w / 2 - vtw / 2, cy_thumb - 16), vs_text, font=FONT_NAME, fill=COLOR_ACCENT)
        cap_text = f"{team1_label or '?'} vs {team2_label or '?'}"
        cbbox = draw.textbbox((0, 0), cap_text, font=FONT_SUB)
        ctw = cbbox[2] - cbbox[0]
        draw.text((thumb_x + thumb_w / 2 - ctw / 2, thumb_y + thumb_h - 24), cap_text, font=FONT_SUB, fill=COLOR_SUBTEXT)

    # ---- Promo banner: shows a fresh Hindi commentary line (if one just
    # came in from commentary_generator.py) instead of the channel tagline,
    # for COMMENTARY_DISPLAY_SECONDS - then falls back to the tagline. This
    # gives viewers a readable caption of what's being said in the audio,
    # without adding a whole new panel to an already-full 720p layout. ----
    latest_commentary = fetch_latest_commentary()
    promo_max_w = WIDTH - 48
    if latest_commentary:
        promo_text = fit_text_to_width(draw, latest_commentary, FONT_COMMENTARY, promo_max_w)
        promo_font = FONT_COMMENTARY
        promo_color = COLOR_TEXT
        cc_w, cc_h = draw_ribbon_tag(draw, 24, HEIGHT - 74 + 6, "COMMENTARY", FONT_LABEL, (*COLOR_ACCENT, 255), text_color=(20, 20, 20))
        text_x = 24 + cc_w + 12
        bbox = draw.textbbox((0, 0), promo_text, font=promo_font)
        text_h = bbox[3] - bbox[1]
        text_y = HEIGHT - 74 + (40 - text_h) / 2 - bbox[1]
        remaining_w = WIDTH - text_x - 16
        promo_text = fit_text_to_width(draw, latest_commentary, promo_font, remaining_w)
        draw.text((text_x, text_y), promo_text, font=promo_font, fill=promo_color)
    else:
        bbox = draw.textbbox((0, 0), CHANNEL_TAGLINE, font=FONT_PROMO)
        tw = bbox[2] - bbox[0]
        draw.text(((WIDTH - tw) / 2, HEIGHT - 74 + (40 - 24) / 2), CHANNEL_TAGLINE, font=FONT_PROMO, fill=COLOR_ACCENT)

    draw.text((24, HEIGHT - 27), "Auto-generated live scoreboard - not affiliated with any official broadcaster", font=FONT_SUB, fill=COLOR_SUBTEXT)

    draw_event_popup(composited, draw, popup, popup_progress)

    return composited

RENDER_INTERVAL_SECONDS = 0.4     # how often board.png is rewritten (go_live.py reads it continuously at ~2fps, so frequent small changes here become real animation in the stream)
EVENT_POPUP_SECONDS = 2.2         # how long the FOUR!/SIX!/WICKET! banner stays on screen
PULSE_PERIOD_SECONDS = 3.0        # how long one "breathe" cycle of the striker-dot glow takes

def main():
    print(f"Starting scoreboard generator (reads {MATCH_ID_FILE} live, currently: {get_current_match_id()})")
    state = None
    history = []
    last_data_fetch = 0.0
    popup_type = None
    popup_start = 0.0

    while True:
        now = time.time()

        if now - last_data_fetch >= POLL_INTERVAL_SECONDS:
            new_state = fetch_state()
            if new_state and new_state.get("score") == "score not found":
                new_state = None

            # Prefer recent_balls straight from the API (Cricbuzz's own
            # ground-truth "Recent" widget - includes dot balls correctly,
            # unlike the old score-snapshot-diff approach). Falls back to
            # the legacy ball_history.json only if the API doesn't have it.
            new_history = (new_state.get("recent_balls") if new_state else None)
            if not new_history:
                new_history = fetch_ball_history()

            # Detect brand-new ball(s) since the last poll, to trigger the
            # FOUR!/SIX!/WICKET! popup animation. Skipped on the very first
            # fetch (nothing to compare against yet) and safely handles a
            # shorter list (new match/innings) by treating everything as new.
            if history:
                new_tags = new_history[len(history):] if len(new_history) >= len(history) else new_history
            else:
                new_tags = []  # don't fire a popup on process startup
            for tag in new_tags:
                if tag in ("4", "6", "W"):
                    popup_type = {"4": "FOUR", "6": "SIX", "W": "WICKET"}[tag]
                    popup_start = now

            state = new_state
            history = new_history
            last_data_fetch = now
            print(f"[data] refreshed (next in {POLL_INTERVAL_SECONDS}s)")

        popup_progress = None
        if popup_type:
            elapsed = now - popup_start
            if elapsed > EVENT_POPUP_SECONDS:
                popup_type = None
            else:
                popup_progress = elapsed / EVENT_POPUP_SECONDS

        pulse_phase = (now % PULSE_PERIOD_SECONDS) / PULSE_PERIOD_SECONDS

        img = render_board(state, history, popup=popup_type, popup_progress=popup_progress or 0.0, pulse_phase=pulse_phase)
        tmp_path = os.path.join(os.path.dirname(OUTPUT_IMAGE) or ".", "." + os.path.basename(OUTPUT_IMAGE) + ".tmp")
        img.save(tmp_path, format="PNG")
        os.replace(tmp_path, OUTPUT_IMAGE)
        time.sleep(RENDER_INTERVAL_SECONDS)

if __name__ == "__main__":
    main()