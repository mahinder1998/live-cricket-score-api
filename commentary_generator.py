"""
Commentary Generator - Phase 2 + 3 (Hindi + TTS integrated)
-------------------------------------------------------------
Polls the local live-cricket-score-api, detects what changed,
generates an ORIGINAL Hindi commentary sentence, and converts it
to speech (mp3) using edge-tts. Audio files are saved into the
"audio_queue" folder, numbered in order (0001.mp3, 0002.mp3, ...).
Phase 5 (streaming) will play these in order.

Requirements:
    pip install requests edge-tts

Usage:
    1. Make sure the Phase 1 server is running:
       uvicorn app:app --host 0.0.0.0 --port 6020
    2. Set MATCH_ID below to your Cricbuzz match id.
    3. Run: python3 commentary_generator.py
"""

import requests
import time
import random
import re
import os
import asyncio
import edge_tts

# ---------- CONFIG ----------
SCORE_API_URL = "http://localhost:6020/"
MATCH_ID = "144758"          # <-- change this to the live match id
POLL_INTERVAL_SECONDS = 15   # how often to check for updates
VOICE = "hi-IN-MadhurNeural"
AUDIO_DIR = "audio_queue"
# -----------------------------

os.makedirs(AUDIO_DIR, exist_ok=True)

# Original Hindi commentary templates (apni wording, Cricbuzz se copy nahi)
FOUR_LINES = [
    "चौका! {batsman} ने शानदार शॉट खेला।",
    "बहुत बढ़िया शॉट! {batsman} ने आराम से चार रन बटोरे।",
    "क्या टाइमिंग है! {batsman} की तरफ से एक और चौका।",
]
SIX_LINES = [
    "छक्का! {batsman} ने गेंद स्टैंड्स में भेज दी!",
    "जबरदस्त शॉट! {batsman} का जबरदस्त छक्का!",
    "बहुत ऊंचा शॉट, सीधा दर्शकों के बीच! शानदार छक्का {batsman} का!",
]
WICKET_LINES = [
    "आउट! {bowler} ने बहुत बड़ा विकेट निकाला!",
    "गया! सही समय पर {bowler} ने कमाल कर दिया।",
    "विकेट गिर गया! {bowler} की जबरदस्त गेंदबाज़ी।",
]
RUN_LINES = [
    "{batsman} ने {runs} रन जोड़े, स्कोर आगे बढ़ रहा है।",
    "अच्छी रनिंग, {batsman} की तरफ से {runs} रन।",
]
OVER_LINES = [
    "ओवर खत्म हुआ। स्कोर है {score}।",
    "यह ओवर समाप्त, कुल स्कोर {score}।",
]

def get_batsman_runs(batsman):
    match = re.match(r"(\d+)", batsman.get("score", "") or "")
    return int(match.group(1)) if match else None

def fetch_state():
    try:
        resp = requests.get(SCORE_API_URL, params={"score": MATCH_ID}, timeout=10)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        print(f"[warn] could not fetch score: {e}")
        return None

def generate_commentary(prev, curr):
    lines = []
    if not prev:
        return lines

    curr_batsmen = {b["name"]: get_batsman_runs(b) for b in curr.get("current_batsmen", [])}
    prev_batsmen = {b["name"]: get_batsman_runs(b) for b in prev.get("current_batsmen", [])}

    curr_bowler = (curr.get("current_bowler") or {}).get("name")

    new_names = set(curr_batsmen) - set(prev_batsmen)
    if new_names and curr_bowler and curr_bowler != "score not found":
        lines.append(random.choice(WICKET_LINES).format(bowler=curr_bowler))

    for name, curr_runs in curr_batsmen.items():
        if name in prev_batsmen and curr_runs is not None and prev_batsmen[name] is not None:
            diff = curr_runs - prev_batsmen[name]
            if diff == 4:
                lines.append(random.choice(FOUR_LINES).format(batsman=name))
            elif diff == 6:
                lines.append(random.choice(SIX_LINES).format(batsman=name))
            elif diff and diff > 0:
                lines.append(random.choice(RUN_LINES).format(batsman=name, runs=diff))

    if curr.get("score") != prev.get("score") and not lines:
        lines.append(random.choice(OVER_LINES).format(score=curr.get("score")))

    return lines

async def text_to_speech(text, filepath):
    communicate = edge_tts.Communicate(text, VOICE)
    await communicate.save(filepath)

def main():
    print(f"Starting Hindi commentary generator for match id {MATCH_ID}")
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