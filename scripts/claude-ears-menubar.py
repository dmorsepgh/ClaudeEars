#!/usr/bin/env python3
"""
Claude Ears 👂 — macOS Menu Bar App
Listens via BlackHole for a word/phrase and shows hits in the menu bar.
"""

import rumps
import threading
import subprocess
import re
import os
import numpy as np
import whisper
from datetime import datetime

CHUNK_SECS       = 10
MODEL_SIZE       = "tiny"
SAMPLE_RATE      = 16000
BLACKHOLE_DEVICE = "0"
NOTES_DIR        = os.path.expanduser("~/ClaudeEars/notes")
os.makedirs(NOTES_DIR, exist_ok=True)

class ClaudeEarsApp(rumps.App):
    def __init__(self):
        super().__init__("👂", quit_button=None)
        self.target      = None
        self.hit_count   = 0
        self.chunks      = 0
        self.listening   = False
        self.model       = None
        self.thread      = None
        self.session_file = None

        # Menu items
        self.status_item  = rumps.MenuItem("Not listening")
        self.hits_item    = rumps.MenuItem("Hits: 0")
        self.set_item     = rumps.MenuItem("Set word/phrase...", callback=self.set_term)
        self.toggle_item  = rumps.MenuItem("Start Listening", callback=self.toggle_listen)
        self.notes_item   = rumps.MenuItem("Open Notes Folder", callback=self.open_notes)
        self.quit_item    = rumps.MenuItem("Quit", callback=self.quit_app)

        self.menu = [
            self.status_item,
            self.hits_item,
            None,
            self.set_item,
            self.toggle_item,
            None,
            self.notes_item,
            None,
            self.quit_item,
        ]

        self.toggle_item.set_callback(self.toggle_listen)

    # ── Set term ──────────────────────────────────────────────────────────────
    @rumps.clicked("Set word/phrase...")
    def set_term(self, _):
        if self.listening:
            rumps.alert("Stop listening first before changing the term.")
            return
        response = rumps.Window(
            title="Claude Ears",
            message="Enter the word or phrase to listen for:",
            default_text=self.target or "",
            ok="Set",
            cancel="Cancel",
            dimensions=(300, 24)
        ).run()
        if response.clicked and response.text.strip():
            self.target = response.text.strip().lower()
            self.hit_count = 0
            self.chunks = 0
            self.title = "👂"
            self.status_item.title = f"Listening for: \"{self.target}\""
            self.hits_item.title = "Hits: 0"
            self.toggle_item.title = "Start Listening"

    # ── Toggle listen ─────────────────────────────────────────────────────────
    def toggle_listen(self, _):
        if self.listening:
            self.stop_listening()
        else:
            self.start_listening()

    def start_listening(self):
        if not self.target:
            rumps.alert("Set a word or phrase first.")
            return

        self.listening  = True
        self.hit_count  = 0
        self.chunks     = 0
        self.toggle_item.title = "Stop Listening"
        self.title = "🔴"

        # New session file
        safe = self.target.replace(" ", "-")
        self.session_file = os.path.join(
            NOTES_DIR,
            f"{datetime.now().strftime('%Y-%m-%d_%H-%M-%S')}_{safe}.md"
        )
        with open(self.session_file, "w") as f:
            f.write(f"# Claude Ears Session\n")
            f.write(f"**Listening for:** `{self.target}`\n")
            f.write(f"**Started:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n")
            f.write(f"---\n\n## Hits\n\n")

        self.thread = threading.Thread(target=self.listen_loop, daemon=True)
        self.thread.start()

    def stop_listening(self):
        self.listening = False
        self.toggle_item.title = "Start Listening"
        self.title = "👂"
        self.status_item.title = f"Stopped | \"{self.target}\" — {self.hit_count} hits"

        if self.session_file and os.path.exists(self.session_file):
            with open(self.session_file, "a") as f:
                f.write(f"\n---\n\n## Summary\n\n")
                f.write(f"- **Total hits:** {self.hit_count}\n")
                f.write(f"- **Chunks processed:** {self.chunks}\n")
                f.write(f"- **Ended:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")

    # ── Listen loop (background thread) ──────────────────────────────────────
    def listen_loop(self):
        if self.model is None:
            self.status_item.title = "Loading Whisper..."
            self.model = whisper.load_model(MODEL_SIZE)

        self.status_item.title = f"👂 Listening for: \"{self.target}\""

        while self.listening:
            try:
                self.chunks += 1
                audio = self.capture_chunk()
                if audio is None or len(audio) < SAMPLE_RATE:
                    continue

                result = self.model.transcribe(audio, language="en", fp16=False)
                text   = result["text"].strip()

                hits = len(re.findall(
                    rf'\b{re.escape(self.target)}\b',
                    text.lower()
                ))

                if hits:
                    self.hit_count += hits
                    ts = datetime.now().strftime("%H:%M:%S")
                    self.title = f"🎯 {self.hit_count}"
                    self.hits_item.title = f"Hits: {self.hit_count}"

                    # Ding!
                    subprocess.Popen(
                        ["afplay", "/System/Library/Sounds/Ping.aiff"],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
                    )

                    with open(self.session_file, "a") as f:
                        f.write(f"**[{ts}] Hit #{self.hit_count}**\n")
                        f.write(f"> {text}\n\n")

            except Exception as e:
                print(f"Error: {e}")
                import time; __import__('time').sleep(2)

    def capture_chunk(self):
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
        try:
            result = subprocess.run(cmd, capture_output=True, timeout=CHUNK_SECS + 15)
            if not result.stdout:
                return None
            return np.frombuffer(result.stdout, dtype=np.float32)
        except Exception:
            return None

    # ── Helpers ───────────────────────────────────────────────────────────────
    def open_notes(self, _):
        subprocess.run(["open", NOTES_DIR])

    def quit_app(self, _):
        if self.listening:
            self.stop_listening()
        rumps.quit_application()


if __name__ == "__main__":
    ClaudeEarsApp().run()
