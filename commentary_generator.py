"""
Commentary Generator - Phase 2 + 3 (Hindi + TTS integrated) - v2
----------------------------------------------------------------------
Polls the local live-cricket-score-api, detects what changed,
generates an ORIGINAL Hindi commentary sentence, and converts it
to speech (mp3) using edge-tts. Audio files are saved into the
"audio_queue" folder, numbered in order (0001.mp3, 0002.mp3, ...).
Phase 5 (streaming) will play these in order.

HONEST DATA LIMITATIONS (please read):
Our data source is a periodic SCORE SNAPSHOT (polled every few seconds),
not a true ball-by-ball feed. That means:
  - We CAN detect: runs scored (1/2/3/4/6), wickets falling, and a
    probable "extra" (team score rose but no batsman's score changed).
  - We CANNOT reliably tell apart: wide vs no-ball vs bye (all look the
    same to us - just "team score rose, batsman score didn't"), or HOW
    a wicket fell (bowled/caught/run-out/lbw - we only know one fell).
  - We CANNOT guarantee a literal "dot ball" happened when nothing
    changes - it might just be the poll interval catching a gap.
This script does NOT invent specifics it can't verify (e.g. it will
never claim "run out" or "wide ball" specifically) - it only reports
what the data can actually support, to keep the commentary honest.

Requirements:
    pip install requests edge-tts

Usage:
    1. Make sure the Phase 1 server is running:
       uvicorn app:app --host 0.0.0.0 --port 6020
    2. Put the live match id in match_id.txt (see get_current_match_id).
    3. Run: python3 commentary_generator.py
"""

import requests
import time
import random
import re
import os
import json
import asyncio
import edge_tts

# ---------- CONFIG ----------
SCORE_API_URL = "http://localhost:6020/"
MATCH_ID = "144758"          # fallback default if match_id.txt doesn't exist yet
MATCH_ID_FILE = "match_id.txt"   # <-- EDIT THIS FILE ON THE VPS to change matches, no git needed!
POLL_INTERVAL_SECONDS = 8    # checked more often, to catch more individual balls
VOICE = "hi-IN-MadhurNeural"    # try "hi-IN-SwaraNeural" for a female voice
VOICE_RATE = "+8%"           # slightly faster = more energetic commentary feel
VOICE_PITCH = "+0Hz"
AUDIO_DIR = "audio_queue"
BALL_HISTORY_FILE = "ball_history.json"
MAX_BALL_HISTORY = 12
# -----------------------------

os.makedirs(AUDIO_DIR, exist_ok=True)

# Original Hindi commentary templates (apni wording, Cricbuzz se copy nahi).
# Each run value gets its OWN set of lines for variety, instead of one
# generic template for everything.
ONE_RUN_LINES = [
    "{batsman} ने एक रन चुरा लिया।",
    "बल्ले का हल्का सा टच, {batsman} एक रन के लिए दौड़े।",
    "सिंगल रन, {batsman} स्ट्राइक बदलते हैं।",
]
TWO_RUN_LINES = [
    "अच्छी दौड़, {batsman} ने दो रन पूरे किए।",
    "गैप में गेंद, {batsman} की तरफ से दो रन।",
]
THREE_RUN_LINES = [
    "शानदार फील्डिंग के बावजूद {batsman} ने तीन रन दौड़ लिए।",
    "तीन रन! {batsman} और उनके साथी की बेहतरीन रनिंग।",
]
FOUR_LINES = [
    "चौका! {batsman} ने शानदार शॉट खेला।",
    "बहुत बढ़िया शॉट! {batsman} ने आराम से चार रन बटोरे।",
    "क्या टाइमिंग है! {batsman} की तरफ से एक और चौका।",
    "गेंद बाउंड्री के पार, चौका {batsman} के बल्ले से।",
]
SIX_LINES = [
    "छक्का! {batsman} ने गेंद स्टैंड्स में भेज दी!",
    "जबरदस्त शॉट! {batsman} का जबरदस्त छक्का!",
    "बहुत ऊंचा शॉट, सीधा दर्शकों के बीच! शानदार छक्का {batsman} का!",
    "क्या शक्तिशाली शॉट, गेंद मैदान के बाहर, छक्का {batsman} की तरफ से!",
]
WICKET_LINES = [
    "आउट! {bowler} ने बहुत बड़ा विकेट निकाला!",
    "गया! सही समय पर {bowler} ने कमाल कर दिया।",
    "विकेट गिर गया! {bowler} की जबरदस्त गेंदबाज़ी।",
    "बड़ा झटका! {bowler} ने बल्लेबाज़ी क्रम तोड़ दिया।",
]
# Deliberately generic - we genuinely cannot tell wide/no-ball/bye apart
# from this data source, so we don't pretend to.
EXTRA_RUN_LINES = [
    "टीम को अतिरिक्त रन मिला, स्कोर आगे बढ़ा।",
    "एक्स्ट्रा रन के साथ स्कोर में इज़ाफ़ा।",
]
OVER_LINES = [
    "ओवर खत्म हुआ। स्कोर है {score}।",
    "यह ओवर समाप्त, कुल स्कोर {score}।",
]
# Genuine dot balls (runs stayed same, but the striker's balls-faced count
# went up by 1) - kept short/occasional so it doesn't get repetitive when
# there's a run of them in a row.
DOT_BALL_LINES = [
    "डॉट बॉल, कोई रन नहीं।",
    "अच्छी गेंद, बल्लेबाज़ रन नहीं बना सके।",
    "रन रेट पर थोड़ा दबाव, यह गेंद डॉट रही।",
]
# Spoken once the match_result field appears (see app.py) - announced with
# top priority, ahead of any other commentary for that poll.
RESULT_LINES = [
    "मैच खत्म! {result}।",
    "यह रहा नतीजा - {result}।",
    "और इसी के साथ मैच का अंत, {result}।",
]

RUN_LINE_MAP = {
    1: ONE_RUN_LINES,
    2: TWO_RUN_LINES,
    3: THREE_RUN_LINES,
    4: FOUR_LINES,
    6: SIX_LINES,
}

def append_ball_event(tag):
    """Append a short event tag (e.g. '4', '6', 'W', '1', '0') to the shared
    ball history file, keeping only the most recent MAX_BALL_HISTORY entries."""
    history = []
    if os.path.exists(BALL_HISTORY_FILE):
        try:
            with open(BALL_HISTORY_FILE) as f:
                history = json.load(f)
        except Exception:
            history = []
    history.append(tag)
    history = history[-MAX_BALL_HISTORY:]
    try:
        with open(BALL_HISTORY_FILE, "w") as f:
            json.dump(history, f)
    except Exception as e:
        print(f"[warn] could not write ball history: {e}")


def get_batsman_runs(batsman):
    match = re.match(r"(\d+)", batsman.get("score", "") or "")
    return int(match.group(1)) if match else None

def get_batsman_balls(batsman):
    """Extracts the 'B' (balls faced) from a 'R(B)' score string like
    '14(25)'. Balls faced increments on every LEGAL delivery (dot balls
    included) even when runs don't change - that's the one signal our
    snapshot polling has to actually notice a dot ball happened, instead
    of just silently skipping it."""
    match = re.search(r"\((\d+)\)", batsman.get("score", "") or "")
    return int(match.group(1)) if match else None

def get_team_runs(score_str):
    """Parse 'IND 145/3 (20.2)' style string and return the total runs (145)."""
    if not score_str:
        return None
    m = re.match(r"[A-Za-z]+\s+(\d+)", score_str)
    return int(m.group(1)) if m else None

def get_current_match_id():
    """Reads match_id.txt fresh every time - so you can change the match
    just by editing this ONE file directly on the VPS (nano match_id.txt),
    no git push/pull, no restart needed. Falls back to MATCH_ID default."""
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

def generate_commentary(prev, curr):
    lines = []
    if not prev:
        return lines

    # --- Match result: highest priority. Once this appears, announce it
    # and stop - the batsmen/bowler data may reset to placeholders right
    # as the match ends, which would otherwise misfire as a fake "wicket". --
    curr_result = (curr.get("match_result") or "").strip()
    prev_result = (prev.get("match_result") or "").strip()
    if curr_result and curr_result != prev_result:
        lines.append(random.choice(RESULT_LINES).format(result=curr_result))
        return lines

    curr_batsmen = {b["name"]: get_batsman_runs(b) for b in curr.get("current_batsmen", [])}
    prev_batsmen = {b["name"]: get_batsman_runs(b) for b in prev.get("current_batsmen", [])}
    curr_batsmen_balls = {b["name"]: get_batsman_balls(b) for b in curr.get("current_batsmen", [])}
    prev_batsmen_balls = {b["name"]: get_batsman_balls(b) for b in prev.get("current_batsmen", [])}

    curr_bowler = (curr.get("current_bowler") or {}).get("name")

    # --- Wicket ---
    new_names = set(curr_batsmen) - set(prev_batsmen)
    wicket_happened = bool(new_names) and curr_bowler and curr_bowler != "score not found"
    if wicket_happened:
        lines.append(random.choice(WICKET_LINES).format(bowler=curr_bowler))
        append_ball_event("W")

    # --- Runs off the bat (1/2/3/4/6) ---
    explained_batsman_runs = 0
    for name, curr_runs in curr_batsmen.items():
        if name in prev_batsmen and curr_runs is not None and prev_batsmen[name] is not None:
            diff = curr_runs - prev_batsmen[name]
            if diff and diff > 0:
                explained_batsman_runs += diff
                templates = RUN_LINE_MAP.get(diff)
                if templates:
                    lines.append(random.choice(templates).format(batsman=name))
                    append_ball_event(str(diff))
                else:
                    # unusual run value (5, 7+) - still report it honestly
                    lines.append(f"{name} ने {diff} रन जोड़े।")
                    append_ball_event(str(diff))

    # --- Genuine dot ball(s): runs unchanged for a batsman, but their
    # BALLS FACED count went up - that only happens on a legal delivery,
    # so it's the one reliable signal we have that a dot ball actually
    # happened (rather than just no new poll data yet). If the gap covers
    # more than one dot ball, log all of them so the RECENT strip stays
    # accurate, but only *speak* about it some of the time so it doesn't
    # get repetitive during a quiet spell. ---
    dot_ball_count = 0
    if not wicket_happened and not explained_batsman_runs:
        for name, curr_balls in curr_batsmen_balls.items():
            if (name in prev_batsmen_balls and curr_balls is not None
                    and prev_batsmen_balls[name] is not None):
                balls_diff = curr_balls - prev_batsmen_balls[name]
                runs_diff = 0
                if (name in curr_batsmen and name in prev_batsmen
                        and curr_batsmen[name] is not None and prev_batsmen[name] is not None):
                    runs_diff = curr_batsmen[name] - prev_batsmen[name]
                if balls_diff and balls_diff > 0 and runs_diff == 0:
                    dot_ball_count = balls_diff
                    break

    if dot_ball_count:
        for _ in range(dot_ball_count):
            append_ball_event("0")
        if random.random() < 0.35:
            lines.append(random.choice(DOT_BALL_LINES))

    # --- Extras: team total rose but no batsman's score explains it ---
    curr_team_runs = get_team_runs(curr.get("score"))
    prev_team_runs = get_team_runs(prev.get("score"))
    if (curr_team_runs is not None and prev_team_runs is not None
            and not wicket_happened and not explained_batsman_runs and not dot_ball_count):
        team_diff = curr_team_runs - prev_team_runs
        if team_diff > 0:
            lines.append(random.choice(EXTRA_RUN_LINES))
            append_ball_event("+" + str(team_diff))

    # --- Fallback: over/score update line if nothing else was said ---
    if curr.get("score") != prev.get("score") and not lines and not dot_ball_count:
        lines.append(random.choice(OVER_LINES).format(score=curr.get("score")))

    return lines

async def text_to_speech(text, filepath):
    communicate = edge_tts.Communicate(text, VOICE, rate=VOICE_RATE, pitch=VOICE_PITCH)
    await communicate.save(filepath)

def main():
    print(f"Starting Hindi commentary generator (reads {MATCH_ID_FILE} live, currently: {get_current_match_id()})")
    prev_state = None
    clip_number = 1

    while True:
        curr_state = fetch_state()

        if curr_state and curr_state.get("score") == "score not found":
            print("[info] Match abhi live nahi hai. Wait kar rahe hain...")
        elif curr_state:
            new_lines = generate_commentary(prev_state, curr_state)
            for line in new_lines:
                print(f">> {line}")
                filename = os.path.join(AUDIO_DIR, f"{clip_number:04d}.mp3")
                try:
                    asyncio.run(text_to_speech(line, filename))
                    print(f"   [audio saved] {filename}")
                    clip_number += 1
                except Exception as e:
                    print(f"   [warn] TTS failed: {e}")
            prev_state = curr_state

        time.sleep(POLL_INTERVAL_SECONDS)

if __name__ == "__main__":
    main() 