"""
Commentary Generator - Phase 2 + 3 (Hindi + TTS integrated) - v3
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
import asyncio
import edge_tts

# ---------- CONFIG ----------
SCORE_API_URL = "http://localhost:6020/"
MATCH_ID = "144758"          # fallback default if match_id.txt doesn't exist yet
MATCH_ID_FILE = "match_id.txt"   # <-- EDIT THIS FILE ON THE VPS to change matches, no git needed!
POLL_INTERVAL_SECONDS = 5    # checked frequently, so commentary keeps up with every ball
VOICE = "hi-IN-MadhurNeural"    # try "hi-IN-SwaraNeural" for a female voice
VOICE_RATE = "+0%"           # natural pace - faster rates start to sound rushed/robotic
VOICE_PITCH = "+0Hz"
AUDIO_DIR = "audio_queue"
# -----------------------------

os.makedirs(AUDIO_DIR, exist_ok=True)

# Original Hindi commentary templates (apni wording, Cricbuzz se copy nahi).
# Each run value gets its OWN set of lines for variety, instead of one
# generic template for everything.
ONE_RUN_LINES = [
    "{batsman} ने एक रन चुरा लिया।",
    "बल्ले का हल्का सा टच, {batsman} एक रन के लिए दौड़े।",
    "सिंगल रन, {batsman} स्ट्राइक बदलते हैं।",
    "आराम से एक रन ले लिया {batsman} ने।",
    "फील्डर के पास गई, बस एक रन मिला।",
    "{batsman} जल्दी से क्रीज़ के दूसरी तरफ, आसान सिंगल।",
    "स्ट्राइक रोटेट करते हुए एक रन।",
]
TWO_RUN_LINES = [
    "अच्छी दौड़, {batsman} ने दो रन पूरे किए।",
    "गैप में गेंद, {batsman} की तरफ से दो रन।",
    "अच्छी रनिंग बिटवीन द विकेट्स, दो रन मिले।",
    "{batsman} और उनके पार्टनर की तेज़ दौड़, दो रन।",
]
THREE_RUN_LINES = [
    "शानदार फील्डिंग के बावजूद {batsman} ने तीन रन दौड़ लिए।",
    "तीन रन! {batsman} और उनके साथी की बेहतरीन रनिंग।",
    "डीप से थ्रो थोड़ा लेट, तीन रन भाग निकले।",
]
FOUR_LINES = [
    "चौका! {batsman} ने शानदार शॉट खेला।",
    "बहुत बढ़िया शॉट! {batsman} ने आराम से चार रन बटोरे।",
    "क्या टाइमिंग है! {batsman} की तरफ से एक और चौका।",
    "गेंद बाउंड्री के पार, चौका {batsman} के बल्ले से।",
    "वाह! बीच में से गेंद निकल गई, आसान चौका।",
    "{batsman} ने बैकफुट पे जाकर खूबसूरत चौका जड़ा।",
    "कवर की तरफ खूबसूरत टाइमिंग, गेंद बाउंड्री पार।",
    "फील्डर देखता रह गया, गेंद सीधा रस्सी के पार।",
]
SIX_LINES = [
    "छक्का! {batsman} ने गेंद स्टैंड्स में भेज दी!",
    "जबरदस्त शॉट! {batsman} का जबरदस्त छक्का!",
    "बहुत ऊंचा शॉट, सीधा दर्शकों के बीच! शानदार छक्का {batsman} का!",
    "क्या शक्तिशाली शॉट, गेंद मैदान के बाहर, छक्का {batsman} की तरफ से!",
    "वो गई! {batsman} ने गेंद को हवा में उड़ा दिया, बहुत बड़ा छक्का।",
    "क्या हिट है भाई! दर्शक झूम उठे, छक्का {batsman} का।",
    "लॉन्ग ऑन के ऊपर से सीधा मैदान के बाहर, बड़ा छक्का।",
]
WICKET_LINES = [
    "आउट! {bowler} ने बहुत बड़ा विकेट निकाला!",
    "गया! सही समय पर {bowler} ने कमाल कर दिया।",
    "विकेट गिर गया! {bowler} की जबरदस्त गेंदबाज़ी।",
    "बड़ा झटका! {bowler} ने बल्लेबाज़ी क्रम तोड़ दिया।",
    "ओह हो! विकेट गिर गया, {bowler} खुशी से उछल पड़े।",
    "यही चाहिए था टीम को! {bowler} ने ब्रेकथ्रू दिला दिया।",
    "क्या गेंद थी! {bowler} ने बल्लेबाज़ को छका दिया।",
]
# Preferred wicket lines - used when we can identify WHO got out and their
# score, so the commentary names the batsman + runs + bowler, not just the
# bowler alone.
WICKET_LINES_FULL = [
    "आउट! {batsman} {runs} रन बनाकर पवेलियन लौटे, गेंदबाज़ {bowler}।",
    "बड़ा विकेट! {bowler} ने {batsman} को {runs} रन पर आउट किया।",
    "गया विकेट! {batsman} की पारी {runs} रन पर खत्म, श्रेय {bowler} को।",
    "{bowler} की जबरदस्त गेंद, {batsman} {runs} रन बनाकर चलते बने।",
    "विकेट गिर गया! {batsman} {runs} रन पर आउट, {bowler} ने तोड़ी साझेदारी।",
]
# Deliberately generic - we genuinely cannot tell wide/no-ball/bye apart
# from this data source, so we don't pretend to.
EXTRA_RUN_LINES = [
    "टीम को अतिरिक्त रन मिला, स्कोर आगे बढ़ा।",
    "एक्स्ट्रा रन के साथ स्कोर में इज़ाफ़ा।",
    "कुछ अतिरिक्त रन टीम के खाते में जुड़ गए।",
]
OVER_LINES = [
    "स्कोर अभी है {runs} रन, {wickets} विकेट।",
    "अपडेटेड स्कोर - {runs} रन पर {wickets} विकेट।",
]
# Spoken specifically when an over completes (overs count ticks over to the
# next whole number) - explicitly calls out the score AND wickets down, as
# requested, separate from the generic OVER_LINES fallback above.
OVER_END_LINES = [
    "{runs} रन पर {wickets} विकेट, {overs} ओवर पूरे हुए।",
    "यह ओवर खत्म - {runs} रन, {wickets} विकेट, {overs} ओवर के बाद।",
    "छह गेंदें पूरी, स्कोर है {runs} रन, {wickets} विकेट, {overs} ओवर।",
    "{overs} ओवर पूरे, टीम अभी {runs} रन पर, {wickets} विकेट गंवाकर।",
]
# Genuine dot balls (runs stayed same, but the striker's balls-faced count
# went up by 1) - kept short/occasional so it doesn't get repetitive when
# there's a run of them in a row.
DOT_BALL_LINES = [
    "डॉट बॉल, कोई रन नहीं।",
    "अच्छी गेंद, बल्लेबाज़ रन नहीं बना सके।",
    "रन रेट पर थोड़ा दबाव, यह गेंद डॉट रही।",
    "गेंद सीधा बल्ले पर, कोई रन नहीं मिला।",
    "बल्लेबाज़ ने रोक ली, रन की गुंजाइश नहीं थी।",
    "टाइट लाइन, बल्लेबाज़ सिर्फ डिफेंड कर पाए।",
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

def get_team_wickets(score_str):
    """Parse 'IND 145/3 (20.2)' style string and return wickets down (3)."""
    if not score_str:
        return None
    m = re.match(r"[A-Za-z]+\s+\d+/(\d+)", score_str)
    return int(m.group(1)) if m else None

def get_overs(score_str):
    """Parse 'IND 145/3 (20.2)' style string and return overs bowled (20.2)."""
    if not score_str:
        return None
    m = re.search(r"\(([\d.]+)\)", score_str)
    return float(m.group(1)) if m else None

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
        print(f"[warn] could not fetch score: {e}", flush=True)
        return None

def get_striker_name(batsmen):
    """Finds whichever batsman is currently marked on_strike - used to
    attribute ball-by-ball commentary to the right batsman."""
    for b in batsmen or []:
        if b.get("on_strike") and b.get("name") and b.get("name") != "score not found":
            return b.get("name")
    return None

def find_dismissed_batsman(prev_batsmen, curr_batsmen):
    """Compares the two batsmen lists to find who just got out: someone
    present last poll who is no longer in the crease this poll. Returns
    (name, runs) so the wicket commentary can name them, or (None, None)
    if we can't confidently tell (e.g. both slots changed at once)."""
    curr_names = {b.get("name") for b in (curr_batsmen or [])}
    for b in prev_batsmen or []:
        name = b.get("name")
        if name and name != "score not found" and name not in curr_names:
            return name, get_batsman_runs(b)
    return None, None

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

    curr_bowler = (curr.get("current_bowler") or {}).get("name")
    striker_name = get_striker_name(curr.get("current_batsmen")) or "बल्लेबाज़"

    # --- Ball-by-ball events: driven by the SAME ground-truth "recent_balls"
    # list the scoreboard's RECENT strip displays (sourced from Cricbuzz's
    # own widget in app.py). Using this exact list - instead of separately
    # re-guessing events from batsmen score/ball-count diffs - guarantees
    # the commentary always matches what's visually shown on screen. ---
    prev_recent = prev.get("recent_balls") or []
    curr_recent = curr.get("recent_balls") or []

    if len(curr_recent) >= len(prev_recent) and curr_recent[:len(prev_recent)] == prev_recent:
        new_tags = curr_recent[len(prev_recent):]
    else:
        # list shrank, or diverged (new innings/match, or the window shifted) -
        # treat only the single latest entry as "new" to avoid re-announcing
        # a big backlog all at once.
        new_tags = curr_recent[-1:]

    for tag in new_tags:
        if tag == "W":
            if curr_bowler and curr_bowler != "score not found":
                dismissed_name, dismissed_runs = find_dismissed_batsman(
                    prev.get("current_batsmen"), curr.get("current_batsmen")
                )
                if dismissed_name:
                    lines.append(random.choice(WICKET_LINES_FULL).format(
                        batsman=dismissed_name,
                        runs=dismissed_runs if dismissed_runs is not None else "0",
                        bowler=curr_bowler,
                    ))
                else:
                    lines.append(random.choice(WICKET_LINES).format(bowler=curr_bowler))
        elif tag == "0":
            lines.append(random.choice(DOT_BALL_LINES))
        elif tag.lstrip("+").isdigit() and not tag.startswith("+"):
            diff = int(tag)
            templates = RUN_LINE_MAP.get(diff)
            if templates:
                lines.append(random.choice(templates).format(batsman=striker_name))
            else:
                # unusual run value (5, 7+) - still report it honestly
                lines.append(f"{striker_name} ने {diff} रन जोड़े।")
        elif tag.startswith("+"):
            lines.append(random.choice(EXTRA_RUN_LINES))
        # any other/unrecognised tag: skip silently rather than guessing

    # --- Over completed: overs count ticked over to the next whole number
    # (e.g. 12.6 -> 13.0). Speaks runs/wickets/overs as separate natural
    # numbers (NOT the raw "125/5" score string, which TTS was reading
    # awkwardly as "125 bata 5" instead of "125 run par 5 wicket"). ---
    curr_overs = get_overs(curr.get("score"))
    prev_overs = get_overs(prev.get("score"))
    if curr_overs is not None and prev_overs is not None and int(curr_overs) > int(prev_overs):
        wkts = get_team_wickets(curr.get("score"))
        runs = get_team_runs(curr.get("score"))
        lines.append(random.choice(OVER_END_LINES).format(
            runs=runs if runs is not None else "0",
            wickets=wkts if wkts is not None else "0",
            overs=int(curr_overs),
        ))

    # --- Fallback: score update line if nothing else was said ---
    if curr.get("score") != prev.get("score") and not lines:
        fallback_runs = get_team_runs(curr.get("score"))
        fallback_wkts = get_team_wickets(curr.get("score"))
        lines.append(random.choice(OVER_LINES).format(
            runs=fallback_runs if fallback_runs is not None else "0",
            wickets=fallback_wkts if fallback_wkts is not None else "0",
        ))

    return lines

async def text_to_speech(text, filepath):
    communicate = edge_tts.Communicate(text, VOICE, rate=VOICE_RATE, pitch=VOICE_PITCH)
    await communicate.save(filepath)

async def text_to_speech_atomic(text, final_path):
    """Saves the TTS audio to a TEMP filename first, then renames it to
    the final .mp3 name only once fully written. This is essential: without
    it, go_live.py's audio_writer (which continuously scans audio_queue/
    for *.mp3 files) can grab a file WHILE edge-tts is still writing it,
    getting a truncated/corrupt mp3 that ffmpeg then fails to decode
    ("Failed to find two consecutive MPEG audio frames"). The temp name
    uses a non-'.mp3' suffix so it can never accidentally match the
    audio_writer's "*.mp3" glob pattern while the write is in progress."""
    tmp_path = final_path + ".tmp"
    await text_to_speech(text, tmp_path)
    os.rename(tmp_path, final_path)

def main():
    print(f"Starting Hindi commentary generator (reads {MATCH_ID_FILE} live, currently: {get_current_match_id()})", flush=True)
    prev_state = None
    clip_number = 1

    while True:
        curr_state = fetch_state()

        if curr_state and curr_state.get("score") == "score not found":
            print("[info] Match abhi live nahi hai. Wait kar rahe hain...", flush=True)
        elif curr_state:
            new_lines = generate_commentary(prev_state, curr_state)
            for line in new_lines:
                print(f">> {line}", flush=True)
                filename = os.path.join(AUDIO_DIR, f"{clip_number:04d}.mp3")
                try:
                    asyncio.run(text_to_speech_atomic(line, filename))
                    print(f"   [audio saved] {filename}", flush=True)
                    clip_number += 1
                except Exception as e:
                    print(f"   [warn] TTS failed: {e}", flush=True)
            prev_state = curr_state

        time.sleep(POLL_INTERVAL_SECONDS)

if __name__ == "__main__":
    main()