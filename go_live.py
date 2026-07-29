"""
Go Live - Phase 5
--------------------
Combines:
  - board.png            (kept updated by scoreboard_generator.py)
  - audio_queue/*.mp3     (kept filled by commentary_generator.py)
  - crowd_ambient.mp3     (looping background crowd noise)
into a single continuous video+audio stream pushed to YouTube via ffmpeg.

IMPORTANT: Run this ALONGSIDE (not instead of):
  - uvicorn app:app --host 0.0.0.0 --port 6020   (Phase 1)
  - python3 commentary_generator.py               (Phase 2+3)
  - python3 scoreboard_generator.py               (Phase 4)
All four should be running at the same time, each in its own terminal.

Requirements:
    pip install pillow
    sudo apt install ffmpeg -y

Setup:
    1. Create a file called "stream_key.txt" in this same folder containing
       ONLY your YouTube stream key (nothing else). This keeps the key out
       of the code, so you never accidentally paste/share it anywhere.
    2. Make sure crowd_ambient.mp3 exists in this folder.
    3. Make sure board.png exists (scoreboard_generator.py creates it).

Run:
    python3 go_live.py
"""

import os
import subprocess
import threading
import time
import glob

# ---------- CONFIG ----------
BOARD_IMAGE = "board.png"
CROWD_FILE = "crowd_ambient.mp3"
AUDIO_QUEUE_DIR = "audio_queue"
AUDIO_PLAYED_DIR = "audio_queue/played"
STREAM_KEY_FILE = "stream_key.txt"
RTMP_BASE_URL = "rtmp://a.rtmp.youtube.com/live2/"

# Set this to True to test the whole pipeline WITHOUT a YouTube stream key.
# Instead of pushing to YouTube, it records ~30 seconds to test_output.mp4
# so you can download and watch it to confirm video+audio are working.
TEST_MODE = True
TEST_DURATION_SECONDS = 30

VIDEO_WIDTH, VIDEO_HEIGHT = 1280, 720
FRAMERATE = 2          # scoreboard doesn't need to be smooth, 2fps is plenty
AUDIO_SAMPLE_RATE = 24000
SPEECH_FIFO = "/tmp/speech_audio.fifo"
# -----------------------------

os.makedirs(AUDIO_PLAYED_DIR, exist_ok=True)


def read_stream_key():
    if not os.path.exists(STREAM_KEY_FILE):
        raise SystemExit(
            f"[error] {STREAM_KEY_FILE} not found. Create it and paste ONLY "
            f"your YouTube stream key inside, then run this again."
        )
    with open(STREAM_KEY_FILE) as f:
        key = f.read().strip()
    if not key:
        raise SystemExit(f"[error] {STREAM_KEY_FILE} is empty.")
    return key


def ensure_fifo(path):
    if os.path.exists(path):
        os.remove(path)
    os.mkfifo(path)


def video_writer(proc):
    """Continuously feed the current board.png into ffmpeg's stdin as frames,
    until ffmpeg itself exits (e.g. after the test duration, or if streaming stops)."""
    frame_interval = 1.0 / FRAMERATE
    while proc.poll() is None:
        try:
            with open(BOARD_IMAGE, "rb") as f:
                data = f.read()
            proc.stdin.write(data)
            proc.stdin.flush()
        except (BrokenPipeError, FileNotFoundError):
            break
        except Exception as e:
            print(f"[video_writer warn] {e}")
        time.sleep(frame_interval)
    print("[go_live] ffmpeg process has exited, shutting down.")


def decode_to_pcm(mp3_path):
    """Use ffmpeg to convert an mp3 clip to raw PCM bytes matching our audio format."""
    result = subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-i", mp3_path,
            "-f", "s16le", "-ar", str(AUDIO_SAMPLE_RATE), "-ac", "1", "-",
        ],
        stdout=subprocess.PIPE,
    )
    return result.stdout


def audio_writer():
    """Continuously write PCM audio into the speech FIFO: TTS clips when
    available (in order), silence otherwise, so the FIFO never starves."""
    silence_chunk = b"\x00" * (AUDIO_SAMPLE_RATE * 2 // 10)  # ~0.1 sec of silence

    fifo_fd = open(SPEECH_FIFO, "wb")

    while True:
        pending = sorted(glob.glob(os.path.join(AUDIO_QUEUE_DIR, "*.mp3")))
        if pending:
            clip = pending[0]
            print(f"[audio_writer] playing {clip}")
            pcm = decode_to_pcm(clip)
            try:
                fifo_fd.write(pcm)
                fifo_fd.flush()
            except BrokenPipeError:
                pass
            # move to "played" so we don't repeat it
            os.rename(clip, os.path.join(AUDIO_PLAYED_DIR, os.path.basename(clip)))
        else:
            try:
                fifo_fd.write(silence_chunk)
                fifo_fd.flush()
            except BrokenPipeError:
                pass
            time.sleep(0.1)


def build_ffmpeg_command(stream_key):
    rtmp_url = RTMP_BASE_URL + stream_key
    output_args = ["-f", "flv", rtmp_url]

    if TEST_MODE:
        # Write a local file for a limited duration instead of streaming to YouTube
        output_args = ["-t", str(TEST_DURATION_SECONDS), "-f", "mp4", "test_output.mp4"]

    return [
        "ffmpeg",
        "-y", "-hide_banner", "-loglevel", "warning",

        # Video: PNG frames coming from our stdin pipe
        "-f", "image2pipe", "-framerate", str(FRAMERATE), "-i", "pipe:0",

        # Audio input 1: looping crowd ambience (ffmpeg handles the loop itself)
        "-stream_loop", "-1", "-i", CROWD_FILE,

        # Audio input 2: our speech FIFO (TTS commentary + silence)
        "-f", "s16le", "-ar", str(AUDIO_SAMPLE_RATE), "-ac", "1", "-i", SPEECH_FIFO,

        # Mix crowd (quieter) + speech (louder) into one audio track
        "-filter_complex",
        "[1:a]volume=0.35[bg];[2:a]volume=1.0[fg];[bg][fg]amix=inputs=2:duration=first:dropout_transition=2[aout]",

        "-map", "0:v", "-map", "[aout]",

        "-c:v", "libx264", "-preset", "veryfast", "-b:v", "2500k",
        "-pix_fmt", "yuv420p", "-g", str(FRAMERATE * 2),
        "-c:a", "aac", "-b:a", "128k", "-ar", "44100",
    ] + output_args


def main():
    if TEST_MODE:
        stream_key = "test-mode-no-key-needed"
        print("[go_live] TEST_MODE is ON - recording locally instead of streaming to YouTube.")
    else:
        stream_key = read_stream_key()
    ensure_fifo(SPEECH_FIFO)

    if not os.path.exists(CROWD_FILE):
        raise SystemExit(f"[error] {CROWD_FILE} not found in this folder.")
    if not os.path.exists(BOARD_IMAGE):
        print(f"[warn] {BOARD_IMAGE} not found yet - waiting for scoreboard_generator.py to create it...")
        while not os.path.exists(BOARD_IMAGE):
            time.sleep(1)

    cmd = build_ffmpeg_command(stream_key)
    print("[go_live] starting ffmpeg:\n", " ".join(cmd))

    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)

    # Audio writer must open the FIFO for writing - this call blocks until
    # ffmpeg opens it for reading, so start it in a thread.
    threading.Thread(target=audio_writer, daemon=True).start()

    # Feed video frames on the main thread until ffmpeg exits on its own
    video_writer(proc)
    proc.wait()
    print("[go_live] Done. If TEST_MODE, check test_output.mp4 now.")


if __name__ == "__main__":
    main()