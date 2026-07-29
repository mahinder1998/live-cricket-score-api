"""
Scoreboard Generator - Phase 4 (v2 - richer layout)
------------------------------------------------------
Polls the local live-cricket-score-api and draws a scoreboard
graphic (board.png) styled like a broadcast overlay: batter cards,
bowler card, big score, LIVE badge. Overwrites board.png every cycle.

NOTE on data limits: our score API only gives us batsman name+runs(balls)
and bowler name (no 4s/6s/SR/economy/overs breakdown). So those fields
are simply not shown - showing fake numbers would be misleading. If you
later upgrade to a richer data source, this file is easy to extend.

NOTE on player photos: we intentionally use letter-avatars instead of
real player photos, since using a real person's photo without rights/
permission is a legal grey area we want to avoid.

Requirements:
    pip install requests pillow

Usage:
    python3 scoreboard_generator.py
"""

import requests
import time
from PIL import Image, ImageDraw, ImageFont

# ---------- CONFIG ----------
SCORE_API_URL = "http://localhost:6020/"
MATCH_ID = "144758"          # <-- change this to the live match id
POLL_INTERVAL_SECONDS = 10
OUTPUT_IMAGE = "board.png"
WIDTH, HEIGHT = 1280, 720
# -----------------------------

FONT_BOLD = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
FONT_REGULAR = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"

def load_font(path, size):
    try:
        return ImageFont.truetype(path, size)
    except Exception:
        return ImageFont.load_default()

FONT_TITLE = load_font(FONT_BOLD, 30)
FONT_BADGE = load_font(FONT_BOLD, 22)
FONT_SCORE = load_font(FONT_BOLD, 56)
FONT_LABEL = load_font(FONT_BOLD, 20)
FONT_NAME = load_font(FONT_BOLD, 28)
FONT_SUB = load_font(FONT_REGULAR, 20)
FONT_AVATAR = load_font(FONT_BOLD, 40)

COLOR_BG = (12, 22, 18)
COLOR_PANEL = (18, 40, 30)
COLOR_ACCENT = (255, 200, 0)
COLOR_LIVE_RED = (220, 40, 40)
COLOR_TEXT = (255, 255, 255)
COLOR_SUBTEXT = (170, 190, 180)
AVATAR_COLORS = [(30, 110, 90), (110, 60, 140)]

def fetch_state():
    try:
        resp = requests.get(SCORE_API_URL, params={"score": MATCH_ID}, timeout=10)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        print(f"[warn] could not fetch score: {e}")
        return None

def draw_avatar(draw, cx, cy, radius, letter, color):
    draw.ellipse([(cx - radius, cy - radius), (cx + radius, cy + radius)], fill=color)
    bbox = draw.textbbox((0, 0), letter, font=FONT_AVATAR)
    w, h = bbox[2] - bbox[0], bbox[3] - bbox[1]
    draw.text((cx - w / 2, cy - h / 2 - bbox[1]), letter, font=FONT_AVATAR, fill=COLOR_TEXT)

def batter_card(draw, x, y, w, h, name, score, color):
    draw.rounded_rectangle([(x, y), (x + w, y + h)], radius=14, fill=COLOR_PANEL, outline=color, width=2)
    draw_avatar(draw, x + 55, y + h // 2, 38, (name[0].upper() if name else "?"), color)
    text_x = x + 110
    draw.text((text_x, y + 20), name or "-", font=FONT_NAME, fill=COLOR_TEXT)
    draw.text((text_x, y + 60), score or "-", font=FONT_SUB, fill=COLOR_ACCENT)

def bowler_card(draw, x, y, w, h, name, color):
    draw.rounded_rectangle([(x, y), (x + w, y + h)], radius=14, fill=COLOR_PANEL, outline=color, width=2)
    draw_avatar(draw, x + w - 55, y + h // 2, 38, (name[0].upper() if name else "?"), color)
    draw.text((x + 20, y + 20), "BOWLER", font=FONT_LABEL, fill=COLOR_SUBTEXT)
    draw.text((x + 20, y + 55), name or "-", font=FONT_NAME, fill=COLOR_TEXT)

def render_board(state):
    img = Image.new("RGB", (WIDTH, HEIGHT), COLOR_BG)
    draw = ImageDraw.Draw(img)

    title = (state.get("title") if state else None) or "Waiting for live match..."
    score = (state.get("score") if state else None) or "--"
    batsmen = (state.get("current_batsmen") if state else None) or []
    bowler_name = ((state or {}).get("current_bowler") or {}).get("name")
    if bowler_name == "score not found":
        bowler_name = None

    # Header bar
    draw.rectangle([(0, 0), (WIDTH, 90)], fill=(8, 15, 12))
    draw.text((30, 25), title[:60], font=FONT_TITLE, fill=COLOR_TEXT)

    # LIVE badge
    draw.rounded_rectangle([(WIDTH - 140, 25), (WIDTH - 30, 65)], radius=8, fill=COLOR_LIVE_RED)
    draw.text((WIDTH - 120, 33), "LIVE", font=FONT_BADGE, fill=COLOR_TEXT)

    # Batter cards (stacked left)
    card_w, card_h = 560, 100
    y0 = 130
    for i in range(2):
        b = batsmen[i] if i < len(batsmen) else {}
        batter_card(
            draw, 40, y0 + i * (card_h + 20), card_w, card_h,
            b.get("name"), b.get("score"), AVATAR_COLORS[i % 2]
        )

    # Bowler card (right side)
    bowler_card(draw, 640, 130, 600, 100, bowler_name, (150, 90, 30))

    # Big score panel (center-right below bowler)
    score_y = 270
    draw.rounded_rectangle([(640, score_y), (1240, score_y + 160)], radius=14, fill=(8, 15, 12), outline=COLOR_ACCENT, width=2)
    draw.text((665, score_y + 20), "SCORE", font=FONT_LABEL, fill=COLOR_SUBTEXT)
    draw.text((665, score_y + 55), score, font=FONT_SCORE, fill=COLOR_ACCENT)

    # Footer strip
    draw.rectangle([(0, HEIGHT - 50), (WIDTH, HEIGHT)], fill=(8, 15, 12))
    draw.text((30, HEIGHT - 42), "Auto-generated live scoreboard - not affiliated with any official broadcaster", font=FONT_SUB, fill=COLOR_SUBTEXT)

    return img

def main():
    print(f"Starting scoreboard generator (v2) for match id {MATCH_ID}")
    while True:
        state = fetch_state()
        if state and state.get("score") == "score not found":
            state = None
        img = render_board(state)
        img.save(OUTPUT_IMAGE)
        print(f"[updated] {OUTPUT_IMAGE}")
        time.sleep(POLL_INTERVAL_SECONDS)

if __name__ == "__main__":
    main()