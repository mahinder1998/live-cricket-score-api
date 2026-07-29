"""
Scoreboard Generator - Phase 4 (v7 - striker dot, full bowler figures, auto-hide RECENT)
-------------------------------------------------------------------
Requires the UPDATED app.py (with on_strike + overs/maidens/runs/wickets/economy fields).

Changes in this version:
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
from PIL import Image, ImageDraw, ImageFont

# ---------- CONFIG ----------
SCORE_API_URL = "http://localhost:6020/"
MATCH_ID = "144758"          # fallback default if match_id.txt doesn't exist yet
MATCH_ID_FILE = "match_id.txt"   # <-- EDIT THIS FILE ON THE VPS to change matches, no git needed!
POLL_INTERVAL_SECONDS = 10
OUTPUT_IMAGE = "board.png"
BALL_HISTORY_FILE = "ball_history.json"
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

FONT_TITLE = load_font(FONT_BEBAS, 32)
FONT_BADGE = load_font(FONT_BEBAS, 24)
FONT_SCORE = load_font(FONT_ANTON, 46)
FONT_TEAM = load_font(FONT_BEBAS, 24)
FONT_LABEL = load_font(FONT_BEBAS, 20)
FONT_NAME = load_font(FONT_BEBAS, 28)
FONT_STAT = load_font(FONT_BEBAS, 26)  # bigger font for runs/balls, SR, etc.
FONT_SUB = load_font(FONT_REGULAR, 17)
FONT_AVATAR = load_font(FONT_ANTON, 30)
FONT_BALL = load_font(FONT_ANTON, 18)
FONT_PROMO = load_font(FONT_BEBAS, 22)

COLOR_ACCENT = (255, 196, 0)
COLOR_LIVE_RED = (220, 40, 40)
COLOR_TEXT = (255, 255, 255)
COLOR_SUBTEXT = (210, 220, 215)
COLOR_STRIKE_DOT = (60, 230, 110)  # bright green - the on-strike indicator

PANEL_FILL = (8, 14, 12, 205)          # semi-transparent panel background
PANEL_FILL_DARK = (4, 8, 14, 220)      # slightly darker/more opaque (header, footer)

BALL_COLORS = {"4": (50, 130, 220), "6": (185, 70, 220), "W": (220, 55, 55)}
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

def draw_strike_dot(img, cx, cy, radius=9):
    """Small glowing green dot marking the batsman currently on strike -
    drawn at the bottom-right edge of their avatar circle."""
    glow = smooth_circle_layer(radius + 4, (*COLOR_STRIKE_DOT, 90))
    img.paste(glow, (cx - radius - 4, cy - radius - 4), glow)
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

def render_board(state, ball_history):
    base = build_stadium_background(WIDTH, HEIGHT)

    overlay = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
    odraw = ImageDraw.Draw(overlay)

    title = (state.get("title") if state else None) or "Waiting for live match..."
    score = (state.get("score") if state else None) or "--"
    all_scores = (state.get("all_scores") if state else None) or []
    target_info = (state.get("target_info") if state else None) or ""
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
    odraw.rounded_rectangle([(WIDTH - 116, 16), (WIDTH - 24, 50)], radius=8, fill=(*COLOR_LIVE_RED, 255))

    # Score panel(s) - clipped-corner shape with a diagonal gradient fill
    panel_y, panel_h = 80, 108
    if len(all_scores) >= 2:
        panel_w = (WIDTH - 24 * 3) // 2
        score_positions = [(24, all_scores[0], team_color(extract_team_abbr(all_scores[0]), 0)),
                            (24 * 2 + panel_w, all_scores[1], team_color(extract_team_abbr(all_scores[1]), 1))]
    else:
        panel_w = WIDTH - 48
        score_positions = [(24, score, team_color(extract_team_abbr(score), 0))]
    for px, s, c in score_positions:
        pts = clipped_rect(px, panel_y, panel_w, panel_h, cut=28)
        darker = tuple(max(0, ch - 60) for ch in c)
        paste_polygon_gradient(overlay, pts, tuple(min(255, ch + 25) for ch in c), darker, 225)
        # (border intentionally removed)

    # Status bar
    status_y = panel_y + panel_h + 14
    status_h = 34
    odraw.rounded_rectangle([(24, status_y), (WIDTH - 24, status_y + status_h)], radius=8, fill=PANEL_FILL_DARK)

    # RECENT strip - FULL WIDTH - only drawn when there IS ball history yet.
    # When there's none (e.g. very start of an innings/match), it's skipped
    # entirely rather than shown empty, and the player row moves up to
    # take its place.
    recent_y = status_y + status_h + 14
    recent_h = 70
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
    row_h = 140
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
    live_box = (WIDTH - 116, 16, WIDTH - 24, 50)
    lbbox = draw.textbbox((0, 0), "LIVE", font=FONT_BADGE)
    ltw, lth = lbbox[2] - lbbox[0], lbbox[3] - lbbox[1]
    ltx = live_box[0] + ((live_box[2] - live_box[0]) - ltw) / 2
    lty = live_box[1] + ((live_box[3] - live_box[1]) - lth) / 2 - lbbox[1]
    draw.text((ltx, lty), "LIVE", font=FONT_BADGE, fill=COLOR_TEXT)

    for px, s, c in score_positions:
        team = extract_team_abbr(s) or "-"
        rest = s[len(team):].strip() if s else "-"
        draw_flag_circle(composited, draw, px + panel_w - 44, panel_y + panel_h // 2, 26, team)
        draw.text((px + 16, panel_y + 12), team, font=FONT_TEAM, fill=COLOR_TEXT)
        bbox = draw.textbbox((0, 0), rest or "-", font=FONT_SCORE)
        text_h = bbox[3] - bbox[1]
        score_y = panel_y + 40 + (panel_h - 40 - text_h) / 2 - bbox[1]
        draw.text((px + 16, score_y), rest or "-", font=FONT_SCORE, fill=COLOR_TEXT)

    status_text = target_info
    if not status_text:
        crr = compute_run_rate(all_scores[0] if all_scores else score)
        status_text = f"CRR: {crr}" if crr is not None else "LIVE UPDATES"
    draw.text((36, status_y + 6), status_text, font=FONT_SUB, fill=COLOR_ACCENT if not target_info else COLOR_TEXT)

    # RECENT strip foreground content (only when there's history to show)
    if has_recent:
        draw_ribbon_tag(draw, 24, recent_y + 8, "RECENT", FONT_LABEL, (*COLOR_ACCENT, 255), text_color=(20, 20, 20))
        circle_r = 20
        start_x = WIDTH - 48
        cy = recent_y + recent_h // 2 + 4
        for tag in reversed(ball_history[-14:]):
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
        on_strike = bool(b.get("on_strike"))
        cx0 = card_x_positions[i]
        sr = compute_strike_rate(b.get("score"))
        draw_ribbon_tag(draw, cx0, row_y + 8, "BATTER", FONT_LABEL, (*FALLBACK_COLORS[2], 255))
        draw_bat_icon(composited, cx0 + card_w3 - 32, row_y + 68, 56, FALLBACK_COLORS[2])
        draw_avatar(composited, cx0 + 44, row_y + 68, 28, (name[0].upper() if name else "?"), FALLBACK_COLORS[2])
        if on_strike:
            # small glowing dot at the bottom-right of the avatar marks
            # the batsman who is currently on strike
            draw_strike_dot(composited, cx0 + 44 + 20, row_y + 68 + 20)
        name_x = cx0 + 82
        draw.text((name_x, row_y + 48), name or "-", font=FONT_NAME, fill=COLOR_TEXT)
        draw.text((name_x, row_y + 82), b.get("score") or "-", font=FONT_STAT, fill=COLOR_ACCENT)
        sr_text = f"SR: {sr}" if sr is not None else ""
        if sr_text:
            draw.text((cx0 + 16, row_y + row_h - 24), sr_text, font=FONT_SUB, fill=COLOR_SUBTEXT)

    bowler_x = card_x_positions[2]
    draw_ribbon_tag(draw, bowler_x, row_y + 8, "BOWLER", FONT_LABEL, (*FALLBACK_COLORS[3], 255))
    draw_ball_icon(composited, bowler_x + card_w3 - 40, row_y + 68, 22)
    draw_avatar(composited, bowler_x + 44, row_y + 68, 28, (bowler_name[0].upper() if bowler_name else "?"), FALLBACK_COLORS[3])
    draw.text((bowler_x + 82, row_y + 48), bowler_name or "-", font=FONT_NAME, fill=COLOR_TEXT)
    if bowler_figures:
        draw.text((bowler_x + 82, row_y + 82), bowler_figures, font=FONT_STAT, fill=COLOR_ACCENT)
    if bowler_eco:
        draw.text((bowler_x + 16, row_y + row_h - 24), bowler_eco, font=FONT_SUB, fill=COLOR_SUBTEXT)

    if have_thumb and custom_thumb_img is None:
        team1 = extract_team_abbr(score_positions[0][1]) if score_positions else None
        team2 = extract_team_abbr(score_positions[1][1]) if len(score_positions) > 1 else None
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

    bbox = draw.textbbox((0, 0), CHANNEL_TAGLINE, font=FONT_PROMO)
    tw = bbox[2] - bbox[0]
    draw.text(((WIDTH - tw) / 2, HEIGHT - 74 + (40 - 24) / 2), CHANNEL_TAGLINE, font=FONT_PROMO, fill=COLOR_ACCENT)
    draw.text((24, HEIGHT - 27), "Auto-generated live scoreboard - not affiliated with any official broadcaster", font=FONT_SUB, fill=COLOR_SUBTEXT)

    return composited

def main():
    print(f"Starting scoreboard generator (reads {MATCH_ID_FILE} live, currently: {get_current_match_id()})")
    while True:
        state = fetch_state()
        if state and state.get("score") == "score not found":
            state = None
        history = fetch_ball_history()
        img = render_board(state, history)
        img.save(OUTPUT_IMAGE)
        print(f"[updated] {OUTPUT_IMAGE}")
        time.sleep(POLL_INTERVAL_SECONDS)

if __name__ == "__main__":
    main()