#!/usr/bin/env python3
"""
Claude Ears 👂 — macOS Menu Bar App
Listens via BlackHole for one or more words/phrases and shows hits in the menu bar.
"""

import rumps
import threading
import subprocess
import re
import sys
import os
import time
import numpy as np
import whisper
from datetime import datetime

CHUNK_SECS       = 10
MODEL_SIZE       = "base"
SAMPLE_RATE      = 16000
BLACKHOLE_DEVICE = "0"

def _find_ffmpeg():
    for path in ["/opt/homebrew/bin/ffmpeg", "/usr/local/bin/ffmpeg", "/usr/bin/ffmpeg"]:
        if os.path.isfile(path):
            return path
    result = subprocess.run(["which", "ffmpeg"], capture_output=True, text=True)
    if result.returncode == 0:
        return result.stdout.strip()
    return "ffmpeg"

FFMPEG_PATH = _find_ffmpeg()
NOTES_DIR        = os.path.expanduser("~/ClaudeEars/notes")
os.makedirs(NOTES_DIR, exist_ok=True)

PLIST_LABEL = "com.dmpgh.claude-ears"
PLIST_PATH  = os.path.expanduser(f"~/Library/LaunchAgents/{PLIST_LABEL}.plist")
SCRIPT_PATH = os.path.abspath(__file__)
PYTHON_PATH = sys.executable


class ClaudeEarsApp(rumps.App):
    def __init__(self):
        super().__init__("👂", quit_button=None)
        self.targets      = []        # list of lowercase strings
        self.hit_counts   = {}        # {word: count}
        self.chunks       = 0
        self.listening    = False
        self.model        = None
        self.thread       = None
        self.session_file = None
        self._pattern     = None      # compiled regex
        self._session_gen = 0         # incremented each start; kills stale threads
        self._last_chunk_time = None  # watchdog timestamp

        # Menu items
        self.status_item  = rumps.MenuItem("Not listening")
        self.hits_item    = rumps.MenuItem("Hits: 0")
        self.set_item     = rumps.MenuItem("Set word/phrase...", callback=self.set_term)
        self.toggle_item  = rumps.MenuItem("Start Listening", callback=self.toggle_listen)
        self.notes_item   = rumps.MenuItem("Open Notes Folder", callback=self.open_notes)
        self.restart_item = rumps.MenuItem("Restart App", callback=self.restart_app)
        self.login_item   = rumps.MenuItem("Launch at Login", callback=self.toggle_launch_at_login)
        self.quit_item    = rumps.MenuItem("Quit", callback=self.quit_app)

        self.menu = [
            self.status_item,
            self.hits_item,
            None,
            self.set_item,
            self.toggle_item,
            None,
            self.notes_item,
            self.restart_item,
            self.login_item,
            None,
            self.quit_item,
        ]

        self.login_item.state = self._launch_at_login_enabled()

    @property
    def total_hits(self):
        return sum(self.hit_counts.values())

    def _build_pattern(self):
        if not self.targets:
            return None
        escaped = [re.escape(t) for t in self.targets]
        return re.compile(r'\b(' + '|'.join(escaped) + r')\b', re.IGNORECASE)

    def _hits_display(self):
        if not self.hit_counts:
            return "Hits: 0"
        total = self.total_hits
        if len(self.targets) == 1:
            return f"Hits: {total}"
        breakdown = "  |  ".join(f"{w}: {self.hit_counts.get(w, 0)}" for w in self.targets)
        return f"Hits: {total}  ({breakdown})"

    # ── Set term ──────────────────────────────────────────────────────────────
    def set_term(self, _):
        if self.listening:
            rumps.alert("Stop listening first before changing the terms.")
            return
        current = ", ".join(self.targets) if self.targets else ""
        response = rumps.Window(
            title="Claude Ears",
            message="Enter words or phrases to listen for (comma-separated):",
            default_text=current,
            ok="Set",
            cancel="Cancel",
            dimensions=(320, 24)
        ).run()
        if response.clicked and response.text.strip():
            self.targets = [t.strip().lower() for t in response.text.split(",") if t.strip()]
            self.hit_counts = {t: 0 for t in self.targets}
            self._pattern = self._build_pattern()
            self.chunks = 0
            self.title = "👂"
            label = ", ".join(f'"{t}"' for t in self.targets)
            self.status_item.title = f"Listening for: {label}"
            self.hits_item.title = "Hits: 0"
            self.toggle_item.title = "Start Listening"

    # ── Toggle listen ─────────────────────────────────────────────────────────
    def toggle_listen(self, _):
        if self.listening:
            self.stop_listening()
        else:
            self.start_listening()

    def start_listening(self):
        if not self.targets:
            rumps.alert("Set a word or phrase first.")
            return

        self._session_gen += 1
        self.listening  = True
        self.hit_counts = {t: 0 for t in self.targets}
        self.chunks     = 0
        self.hits_item.title = "Hits: 0"
        self.toggle_item.title = "Stop Listening"
        self.title = "🔴"

        ts = datetime.now().strftime('%Y-%m-%d_%H-%M-%S')
        slug = ", ".join(self.targets).replace(" ", "-")
        self.session_file = os.path.join(NOTES_DIR, f"{ts}_{slug}.md")

        label = ", ".join(f'"{t}"' for t in self.targets)
        with open(self.session_file, "w") as f:
            f.write(f"# Claude Ears Session\n")
            f.write(f"**Listening for:** {label}\n")
            f.write(f"**Started:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n")
            f.write(f"---\n\n## Hits\n\n")

        self._last_chunk_time = time.time()
        gen = self._session_gen
        self.thread = threading.Thread(target=self.listen_loop, args=(gen,), daemon=True)
        self.thread.start()
        watchdog = threading.Thread(target=self._watchdog, args=(gen,), daemon=True)
        watchdog.start()

    def stop_listening(self):
        self.listening = False
        self.toggle_item.title = "Start Listening"
        self.title = "👂"
        self.status_item.title = f"Stopped — {self.total_hits} total hits"

        if self.session_file and os.path.exists(self.session_file):
            with open(self.session_file, "a") as f:
                f.write(f"\n---\n\n## Summary\n\n")
                f.write(f"- **Total hits:** {self.total_hits}\n")
                for word, count in self.hit_counts.items():
                    f.write(f"  - \"{word}\": {count}\n")
                f.write(f"- **Chunks processed:** {self.chunks}\n")
                f.write(f"- **Ended:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")

    # ── Watchdog (restarts dead listen thread) ────────────────────────────────
    def _watchdog(self, gen):
        TIMEOUT = 60  # seconds before assuming thread is dead
        while self.listening and gen == self._session_gen:
            time.sleep(15)
            if not self.listening or gen != self._session_gen:
                break
            elapsed = time.time() - (self._last_chunk_time or time.time())
            if elapsed > TIMEOUT:
                self.status_item.title = "⚠️ Restarting..."
                self._session_gen += 1
                gen = self._session_gen
                self._last_chunk_time = time.time()
                self.thread = threading.Thread(target=self.listen_loop, args=(gen,), daemon=True)
                self.thread.start()
                break

    # ── Listen loop (background thread) ──────────────────────────────────────
    def listen_loop(self, gen):
        if self.model is None:
            self.status_item.title = "Loading Whisper..."
            self.model = whisper.load_model(MODEL_SIZE)

        label = ", ".join(f'"{t}"' for t in self.targets)
        self.status_item.title = f"👂 Listening for: {label}"

        while self.listening and gen == self._session_gen:
            try:
                self.chunks += 1
                audio = self.capture_chunk()
                self._last_chunk_time = time.time()
                if audio is None or len(audio) < SAMPLE_RATE:
                    time.sleep(2)
                    continue

                result = self.model.transcribe(audio, language="en", fp16=False)
                text   = result["text"].strip()

                if not text or not self._pattern:
                    continue

                matches = self._pattern.findall(text)

                if matches:
                    ts = datetime.now().strftime("%H:%M:%S")
                    for word in matches:
                        key = word.lower()
                        self.hit_counts[key] = self.hit_counts.get(key, 0) + 1

                    self.title = f"🎯 {self.total_hits}"
                    self.hits_item.title = self._hits_display()

                    subprocess.Popen(
                        ["afplay", "/System/Library/Sounds/Ping.aiff"],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
                    )

                    hit_words = ", ".join(f'"{w}"' for w in set(m.lower() for m in matches))
                    with open(self.session_file, "a") as f:
                        f.write(f"**[{ts}] Hit** — {hit_words}\n")
                        f.write(f"> {text}\n\n")

            except Exception as e:
                print(f"Error: {e}")
                time.sleep(2)

    def capture_chunk(self):
        cmd = [
            FFMPEG_PATH, "-loglevel", "quiet",
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

    # ── Launch at Login ───────────────────────────────────────────────────────
    def _launch_at_login_enabled(self):
        return os.path.exists(PLIST_PATH)

    def toggle_launch_at_login(self, _):
        if self._launch_at_login_enabled():
            os.remove(PLIST_PATH)
            self.login_item.state = False
        else:
            plist = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>{PLIST_LABEL}</string>
    <key>ProgramArguments</key>
    <array>
        <string>{PYTHON_PATH}</string>
        <string>{SCRIPT_PATH}</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <false/>
</dict>
</plist>"""
            os.makedirs(os.path.dirname(PLIST_PATH), exist_ok=True)
            with open(PLIST_PATH, "w") as f:
                f.write(plist)
            self.login_item.state = True

    # ── Helpers ───────────────────────────────────────────────────────────────
    def open_notes(self, _):
        subprocess.run(["open", NOTES_DIR])

    def restart_app(self, _):
        if self.listening:
            self.stop_listening()
        subprocess.Popen([PYTHON_PATH, SCRIPT_PATH])
        rumps.quit_application()

    def quit_app(self, _):
        if self.listening:
            self.stop_listening()
        rumps.quit_application()


if __name__ == "__main__":
    ClaudeEarsApp().run()
