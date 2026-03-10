# Claude Ears 👂

**Keyword tracking for your system audio.**

Claude Ears sits in your macOS menu bar and listens to whatever's playing on your Mac — TV, podcasts, meetings, videos — and counts every time your target words are mentioned. Timestamps, full transcript context, session logs.

## How It Works

1. Mac audio routes through **BlackHole 2ch** (virtual audio driver)
2. **ffmpeg** captures the audio stream in 10-second chunks
3. **Whisper** (runs locally — no internet required) transcribes each chunk
4. The app scans the transcript for your keywords and logs every hit

## Features

- Track one or multiple keywords simultaneously (comma-separated)
- Live hit count in the menu bar
- Audio ping on every hit
- Session notes saved as markdown with timestamps and transcript context
- Watchdog auto-restarts if the listener goes dormant
- Launch at Login support
- Restart from menu bar without opening Terminal

## Requirements

- macOS
- [BlackHole 2ch](https://existential.audio/blackhole/) — virtual audio driver
- Multi-Output Device configured in Audio MIDI Setup (routes audio to both BlackHole and your speakers)
- ffmpeg — `brew install ffmpeg`
- Python 3.11+ with dependencies:

```bash
pip install openai-whisper rumps numpy
```

## Quick Start

```bash
python3 scripts/claude-ears-menubar.py
```

The 👂 icon appears in your menu bar. Click it to set keywords and start listening.

## Files

```
ClaudeEars/
├── scripts/
│   ├── claude-ears-menubar.py    # Menu bar app (main)
│   └── claude-ears.py            # Original CLI version
├── notes/                        # Session logs (auto-generated)
├── docs/
│   ├── setup.md                  # BlackHole setup guide
│   └── vision-script.md          # Explainer script
└── setup.py                      # py2app build config
```

## Whisper Models

The app uses the `base` model by default. To change it, edit `MODEL_SIZE` at the top of the script:

| Model | Accuracy | Speed |
|-------|----------|-------|
| `tiny` | Low | Fastest |
| `base` | Good | Fast (default) |
| `small` | Better | Moderate |
| `medium` | Best | Slower |
