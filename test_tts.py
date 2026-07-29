"""
Phase 3 - Text-to-Speech test
------------------------------
This confirms TTS works on your machine BEFORE we wire it into the
live commentary loop. No live match needed to test this.

Install:
    pip install edge-tts

Run:
    python3 test_tts.py

It will create a file called "test_output.mp3" - play it to confirm
the voice sounds good. Try different voices below if you like.
"""

import asyncio
import edge_tts

# Some good English (Indian accent) voices to try:
#   en-IN-NeerjaNeural   (female)
#   en-IN-PrabhatNeural  (male)
# Hindi voices:
#   hi-IN-SwaraNeural    (female)
#   hi-IN-MadhurNeural   (male)

VOICE = "hi-IN-MadhurNeural"
TEXT = "छक्का! क्या जबरदस्त शॉट है, गेंद सीधा स्टैंड्स में चली गई!"
OUTPUT_FILE = "test_output_hindi.mp3"

async def main():
    communicate = edge_tts.Communicate(TEXT, VOICE)
    await communicate.save(OUTPUT_FILE)
    print(f"Done! Saved to {OUTPUT_FILE} - play it and check how it sounds.")

if __name__ == "__main__":
    asyncio.run(main())