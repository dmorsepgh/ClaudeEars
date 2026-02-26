# Claude Ears 👂

**Always listening. Always taking notes.**

Claude Ears taps into BlackHole (virtual audio) to capture whatever's playing on your Mac — meetings, tutorials, videos, calls — and uses Whisper + Claude AI to transcribe and take intelligent notes in real time.

## How It Works

1. Mac audio routes through **BlackHole 2ch** (same setup as Trump Bingo)
2. **Whisper** (tiny/base model) transcribes 10-second audio chunks
3. **Claude API** analyzes each chunk in context and builds running notes
4. Notes saved as markdown in `~/ClaudeEars/notes/`

## Modes

- **Meeting Mode** — captures decisions, action items, key quotes
- **Learning Mode** — study notes, key concepts, summaries

## Files

```
ClaudeEars/
├── scripts/
│   ├── claude-ears.py          # Main listener script
│   └── Start-ClaudeEars.command  # Double-click launcher
├── notes/                      # Auto-generated markdown notes
└── docs/
    └── setup.md               # BlackHole setup guide
```

## Quick Start

```bash
# Listen to system audio (via BlackHole)
python3 scripts/claude-ears.py --mode meeting

# Learning/video mode
python3 scripts/claude-ears.py --mode learning
```

## Requirements

- BlackHole 2ch installed
- Multi-Output Device configured in Audio MIDI Setup
- `pip install openai-whisper anthropic numpy`
- ffmpeg installed
