#!/usr/bin/env python3
"""
Claude Ears 👂
Continuously listens via BlackHole and tracks mentions of a target word or phrase.

Usage:
  python3 claude-ears.py "bitcoin"
  python3 claude-ears.py "interest rates"
  python3 claude-ears.py "trump"
"""

import sys
import subprocess
import json
import time
import os
import re
import numpy as np
import whisper
from datetime import datetime

# ── Config ─────────────────────────────────────────────────────────────────────
TARGET       = " ".join(sys.argv[1:]) if len(sys.argv) > 1 else "claude"
CHUNK_SECS   = 10
MODEL_SIZE   = "tiny"      # tiny=fastest, base=more accurate
SAMPLE_RATE  = 16000
BLACKHOLE_DEVICE = "0"     # Run: ffmpeg -f avfoundation -list_devices true -i ""

NOTES_DIR    = os.path.expanduser("~/ClaudeEars/notes")
os.makedirs(NOTES_DIR, exist_ok=True)

SESSION_FILE = os.path.join(NOTES_DIR, f"{datetime.now().strftime('%Y-%m-%d_%H-%M-%S')}_{TARGET.replace(' ', '-')}.md")

# ── State ───────────────────────────────────────────────────────────────────────
state = {
    "target":       TARGET,
    "hit_count":    0,
    "chunks":       0,
    "started":      datetime.now().isoformat(),
    "hits":         [],
}

# ── Session file ────────────────────────────────────────────────────────────────
def init_session():
    with open(SESSION_FILE, "w") as f:
        f.write(f"# Claude Ears Session\n")
        f.write(f"**Listening for:** `{TARGET}`\n")
        f.write(f"**Started:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n")
        f.write(f"---\n\n")
        f.write(f"## Hits\n\n")
    print(f"📝 Notes: {SESSION_FILE}")

def log_hit(timestamp, text, count):
    with open(SESSION_FILE, "a") as f:
        f.write(f"**[{timestamp}] Hit #{count}**\n")
        f.write(f"> {text}\n\n")

def finalize_session():
    with open(SESSION_FILE, "a") as f:
        f.write(f"\n---\n\n")
        f.write(f"## Summary\n\n")
        f.write(f"- **Total hits:** {state['hit_count']}\n")
        f.write(f"- **Chunks processed:** {state['chunks']}\n")
        f.write(f"- **Duration:** {state['started']} → {datetime.now().isoformat()}\n")

# ── Audio capture ───────────────────────────────────────────────────────────────
def capture_chunk():
    cmd = [
        "ffmpeg", "-loglevel", "quiet",
        "-f", "avfoundation",
        "-i", f":{BLACKHOLE_DEVICE}",
        "-t", str(CHUNK_SECS),
        "-acodec", "pcm_f32le",
        "-ar", str(SAMPLE_RATE),
        "-ac", "1",
        "-f", "f32le",
        "pipe:1"
    ]
    result = subprocess.run(cmd, capture_output=True, timeout=CHUNK_SECS + 15)
    if not result.stdout:
        return None
    return np.frombuffer(result.stdout, dtype=np.float32)

# ── Main ────────────────────────────────────────────────────────────────────────
def main():
    print(f"\n👂 Claude Ears")
    print(f"🎯 Listening for: \"{TARGET}\"")
    print(f"🎧 Source: BlackHole 2ch (system audio)")
    print(f"🤖 Whisper model: {MODEL_SIZE}")
    print(f"⏱️  Chunk size: {CHUNK_SECS}s")
    print(f"Press Ctrl+C to stop\n")

    init_session()

    print(f"Loading Whisper ({MODEL_SIZE})...", end="", flush=True)
    model = whisper.load_model(MODEL_SIZE)
    print(f" ready!\n")

    while True:
        try:
            state["chunks"] += 1
            ts = datetime.now().strftime("%H:%M:%S")
            print(f"[{ts}] Chunk #{state['chunks']} — capturing...", end="", flush=True)

            audio = capture_chunk()
            if audio is None or len(audio) < SAMPLE_RATE:
                print(" ⚠️  No audio, retrying...")
                time.sleep(2)
                continue

            print(f" transcribing...", end="", flush=True)
            result = model.transcribe(audio, language="en", fp16=False)
            text = result["text"].strip()

            # Count hits
            hits = len(re.findall(rf'\b{re.escape(TARGET.lower())}\b', text.lower()))

            if hits:
                state["hit_count"] += hits
                print(f" 🎯 HIT x{hits}! (total: {state['hit_count']})")
                print(f"   → \"{text[:80]}\"")
                log_hit(ts, text, state["hit_count"])
            else:
                print(f" — nothing | \"{text[:60]}\"")

        except KeyboardInterrupt:
            print(f"\n\n👋 Done!")
            print(f"   \"{TARGET}\" mentioned: {state['hit_count']} times")
            print(f"   Chunks processed: {state['chunks']}")
            print(f"   Notes saved: {SESSION_FILE}")
            finalize_session()
            break
        except Exception as e:
            print(f" ❌ Error: {e}")
            time.sleep(3)

if __name__ == "__main__":
    main()
