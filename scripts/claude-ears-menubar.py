#!/usr/bin/env python3
"""
Claude Ears 👂 — macOS Menu Bar App
Listens via BlackHole for one or more words/phrases and shows hits in the menu bar.
"""

import rumps
import threading
import subprocess
import queue
import re
import sys
import os
import time
import json
import numpy as np
import whisper
from datetime import datetime

CHUNK_SECS       = 10
MODEL_SIZE       = "base"
SAMPLE_RATE      = 16000
BLACKHOLE_DEVICE = "0"

PRESETS = [
    "trump",
    "the president",
    "the united states",
    "the us",
    "conservative",
    "liberal",
    "republican",
    "democrat",
]

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

NUM_CUSTOM_SLOTS = 2
CONFIG_FILE = os.path.expanduser("~/ClaudeEars/config.json")

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
        self._ui_queue    = queue.Queue()  # thread-safe UI update queue
        self._custom_targets = []     # targets from the manual entry dialog
        self._qp_slots = self._load_config()  # [{word, active}, ...]

        # Preset submenu
        self._preset_items = {}
        preset_menu = rumps.MenuItem("Quick Picks")
        for p in PRESETS:
            item = rumps.MenuItem(p.title(), callback=self._toggle_preset)
            item.state = False
            self._preset_items[p] = item
            preset_menu[p.title()] = item

        # Custom slots
        self._slot_items = []
        for i in range(NUM_CUSTOM_SLOTS):
            label = self._slot_label(i)
            item = rumps.MenuItem(label, callback=self._toggle_slot)
            item.state = self._qp_slots[i]["active"]
            self._slot_items.append(item)
            preset_menu[f"__slot_{i}__"] = item
        edit_slots_item = rumps.MenuItem("Edit Custom Picks...", callback=self._edit_slots)
        preset_menu["__edit_slots__"] = edit_slots_item

        # Menu items
        self.status_item  = rumps.MenuItem("Not listening")
        self.hits_item    = rumps.MenuItem("Hits: 0")
        self.set_item     = rumps.MenuItem("Set Keywords...", callback=self.set_term)
        self.toggle_item  = rumps.MenuItem("Start Listening", callback=self.toggle_listen)
        self.notes_item   = rumps.MenuItem("Open Notes Folder", callback=self.open_notes)
        self.restart_item = rumps.MenuItem("Restart App", callback=self.restart_app)
        self.login_item   = rumps.MenuItem("Launch at Login", callback=self.toggle_launch_at_login)
        self.quit_item    = rumps.MenuItem("Quit", callback=self.quit_app)

        self.menu = [
            self.status_item,
            self.hits_item,
            None,
            preset_menu,
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

    @rumps.timer(0.3)
    def _drain_ui(self, _):
        while not self._ui_queue.empty():
            try:
                key, value = self._ui_queue.get_nowait()
                if key == 'title':
                    self.title = value
                elif key == 'status':
                    self.status_item.title = value
                elif key == 'hits':
                    self.hits_item.title = value
            except queue.Empty:
                break

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

    # ── Preset toggles ────────────────────────────────────────────────────────
    def _toggle_preset(self, sender):
        if self.listening:
            rumps.alert("Stop listening first before changing keywords.")
            return
        key = sender.title.lower()
        item = self._preset_items.get(key)
        if item:
            item.state = not item.state
        self._rebuild_targets()

    def _rebuild_targets(self):
        active_presets = [p for p in PRESETS if self._preset_items[p].state]
        active_slots = [s["word"] for s in self._qp_slots if s["word"] and s["active"]]
        all_qp = active_presets + [s for s in active_slots if s not in active_presets]
        self.targets = all_qp + [t for t in self._custom_targets if t not in all_qp]
        self.hit_counts = {t: 0 for t in self.targets}
        self._pattern = self._build_pattern()
        self.chunks = 0
        self.title = "👂"
        if self.targets:
            label = ", ".join(f'"{t}"' for t in self.targets)
            self.status_item.title = f"Ready: {label}"
        else:
            self.status_item.title = "Not listening"
        self.hits_item.title = "Hits: 0"
        self.toggle_item.title = "Start Listening"

    # ── Custom slots ──────────────────────────────────────────────────────────
    def _slot_label(self, idx):
        word = self._qp_slots[idx]["word"]
        return word.title() if word else f"Set Custom {idx + 1}..."

    def _load_config(self):
        try:
            with open(CONFIG_FILE) as f:
                data = json.load(f)
            slots = data.get("slots", [])
            result = []
            for i in range(NUM_CUSTOM_SLOTS):
                s = slots[i] if i < len(slots) else {}
                result.append({"word": s.get("word", ""), "active": s.get("active", False)})
            return result
        except Exception:
            return [{"word": "", "active": False} for _ in range(NUM_CUSTOM_SLOTS)]

    def _save_config(self):
        os.makedirs(os.path.dirname(CONFIG_FILE), exist_ok=True)
        with open(CONFIG_FILE, "w") as f:
            json.dump({"slots": self._qp_slots}, f, indent=2)

    def _toggle_slot(self, sender):
        idx = next((i for i, item in enumerate(self._slot_items) if item is sender), None)
        if idx is None:
            return
        slot = self._qp_slots[idx]
        if not slot["word"]:
            self._prompt_slot(idx)
        else:
            if self.listening:
                rumps.alert("Stop listening first before changing keywords.")
                return
            slot["active"] = not slot["active"]
            self._slot_items[idx].state = slot["active"]
            self._save_config()
            self._rebuild_targets()

    def _prompt_slot(self, idx):
        if self.listening:
            rumps.alert("Stop listening first before changing keywords.")
            return
        current = self._qp_slots[idx]["word"]
        response = rumps.Window(
            title="Claude Ears",
            message=f"Enter word or phrase for Custom Slot {idx + 1}:",
            default_text=current,
            ok="Set",
            cancel="Cancel",
            dimensions=(320, 24)
        ).run()
        if response.clicked:
            word = response.text.strip().lower()
            self._qp_slots[idx]["word"] = word
            self._qp_slots[idx]["active"] = bool(word)
            self._slot_items[idx].title = self._slot_label(idx)
            self._slot_items[idx].state = bool(word)
            self._save_config()
            self._rebuild_targets()

    def _edit_slots(self, _):
        if self.listening:
            rumps.alert("Stop listening first before changing keywords.")
            return
        current = ", ".join(s["word"] for s in self._qp_slots if s["word"])
        response = rumps.Window(
            title="Claude Ears — Edit Custom Picks",
            message="Enter up to 2 custom words/phrases (comma-separated). Clear to remove.",
            default_text=current,
            ok="Save",
            cancel="Cancel",
            dimensions=(320, 24)
        ).run()
        if response.clicked:
            words = [t.strip().lower() for t in response.text.split(",") if t.strip()][:NUM_CUSTOM_SLOTS]
            while len(words) < NUM_CUSTOM_SLOTS:
                words.append("")
            for i in range(NUM_CUSTOM_SLOTS):
                self._qp_slots[i]["word"] = words[i]
                if not words[i]:
                    self._qp_slots[i]["active"] = False
                self._slot_items[i].title = self._slot_label(i)
                self._slot_items[i].state = self._qp_slots[i]["active"]
            self._save_config()
            self._rebuild_targets()

    # ── Set term ──────────────────────────────────────────────────────────────
    def set_term(self, _):
        if self.listening:
            rumps.alert("Stop listening first before changing keywords.")
            return
        self._show_keyword_dialog()

    def _show_keyword_dialog(self):
        from AppKit import NSAlert, NSView, NSButton, NSTextField, NSFont

        ROW_H = 22
        W     = 360
        PAD   = 8

        slot_section   = NUM_CUSTOM_SLOTS * (ROW_H + 4) + 4 + 18 + PAD
        preset_section = len(PRESETS) * ROW_H + 4 + 18 + PAD
        input_section  = 18 + 4 + ROW_H + 4
        total_h = PAD + slot_section + preset_section + input_section + PAD

        view = NSView.alloc().initWithFrame_(((0, 0), (W, total_h)))
        y = PAD

        # ── Custom slots (bottom) ───────────────────────────────────────────
        slot_checks, slot_fields = [], []
        for i in range(NUM_CUSTOM_SLOTS - 1, -1, -1):
            tf = NSTextField.alloc().initWithFrame_(((28, y), (W - 28, ROW_H)))
            tf.setStringValue_(self._qp_slots[i]["word"])
            tf.setPlaceholderString_(f"Custom {i + 1}...")
            view.addSubview_(tf)

            cb = NSButton.alloc().initWithFrame_(((0, y + 2), (24, ROW_H)))
            cb.setButtonType_(3)   # NSButtonTypeSwitch (checkbox)
            cb.setTitle_("")
            cb.setState_(1 if (self._qp_slots[i]["active"] and self._qp_slots[i]["word"]) else 0)
            view.addSubview_(cb)

            slot_checks.insert(0, cb)
            slot_fields.insert(0, tf)
            y += ROW_H + 4

        # "Custom:" label
        y += 4
        lbl = NSTextField.alloc().initWithFrame_(((0, y), (W, 18)))
        lbl.setStringValue_("Custom:")
        lbl.setBezeled_(False); lbl.setDrawsBackground_(False)
        lbl.setEditable_(False); lbl.setSelectable_(False)
        lbl.setFont_(NSFont.boldSystemFontOfSize_(12))
        view.addSubview_(lbl)
        y += 18 + PAD

        # ── Preset checkboxes ───────────────────────────────────────────────
        preset_checks = {}
        for p in reversed(PRESETS):
            cb = NSButton.alloc().initWithFrame_(((0, y), (W, ROW_H)))
            cb.setButtonType_(3)
            cb.setTitle_(p.title())
            cb.setState_(1 if self._preset_items[p].state else 0)
            view.addSubview_(cb)
            preset_checks[p] = cb
            y += ROW_H

        # "Quick Picks:" label
        y += 4
        lbl2 = NSTextField.alloc().initWithFrame_(((0, y), (W, 18)))
        lbl2.setStringValue_("Quick Picks:")
        lbl2.setBezeled_(False); lbl2.setDrawsBackground_(False)
        lbl2.setEditable_(False); lbl2.setSelectable_(False)
        lbl2.setFont_(NSFont.boldSystemFontOfSize_(12))
        view.addSubview_(lbl2)
        y += 18 + PAD

        # ── Free-text entry (top) ───────────────────────────────────────────
        lbl3 = NSTextField.alloc().initWithFrame_(((0, y), (W, 18)))
        lbl3.setStringValue_("Type your own word or phrase (comma-separated):")
        lbl3.setBezeled_(False); lbl3.setDrawsBackground_(False)
        lbl3.setEditable_(False); lbl3.setSelectable_(False)
        lbl3.setFont_(NSFont.systemFontOfSize_(12))
        view.addSubview_(lbl3)
        y += 18 + 4

        text_field = NSTextField.alloc().initWithFrame_(((0, y), (W, ROW_H)))
        text_field.setStringValue_(", ".join(self._custom_targets))
        text_field.setPlaceholderString_("e.g. climate change, economy")
        view.addSubview_(text_field)

        # ── Build and show alert ────────────────────────────────────────────
        alert = NSAlert.alloc().init()
        alert.setMessageText_("Claude Ears — Keywords")
        alert.addButtonWithTitle_("Set")
        alert.addButtonWithTitle_("Cancel")
        alert.setAccessoryView_(view)
        alert.window().setInitialFirstResponder_(text_field)

        response = alert.runModal()

        if response == 1000:  # NSAlertFirstButtonReturn — "Set" clicked
            self._custom_targets = [
                t.strip().lower() for t in text_field.stringValue().split(",") if t.strip()
            ]
            for p, cb in preset_checks.items():
                self._preset_items[p].state = bool(cb.state())
            for i in range(NUM_CUSTOM_SLOTS):
                word   = slot_fields[i].stringValue().strip().lower()
                active = bool(slot_checks[i].state()) and bool(word)
                self._qp_slots[i]["word"]   = word
                self._qp_slots[i]["active"] = active
                self._slot_items[i].title   = self._slot_label(i)
                self._slot_items[i].state   = active
            self._save_config()
            self._rebuild_targets()

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
                self._ui_queue.put(('status', "⚠️ Restarting..."))
                self._session_gen += 1
                gen = self._session_gen
                self._last_chunk_time = time.time()
                self.thread = threading.Thread(target=self.listen_loop, args=(gen,), daemon=True)
                self.thread.start()
                break

    # ── Listen loop (background thread) ──────────────────────────────────────
    def listen_loop(self, gen):
        if self.model is None:
            self._ui_queue.put(('status', "Loading Whisper..."))
            self.model = whisper.load_model(MODEL_SIZE)

        label = ", ".join(f'"{t}"' for t in self.targets)
        self._ui_queue.put(('status', f"👂 Listening for: {label}"))

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

                    self._ui_queue.put(('title', f"🎯 {self.total_hits}"))
                    self._ui_queue.put(('hits', self._hits_display()))

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
