# BlackHole Setup for Claude Ears

## Audio Routing

Claude Ears listens via BlackHole 2ch — the same virtual audio device used by Trump Bingo.

### If Already Configured (Trump Bingo users)
You're good to go. Just make sure your Mac output is set to the Multi-Output Device.

### Fresh Setup

1. Install BlackHole: https://existential.audio/blackhole/
2. Open **Audio MIDI Setup** (Applications → Utilities)
3. Click **+** → **Create Multi-Output Device**
4. Check both: your speakers/headphones AND BlackHole 2ch
5. Go to **System Preferences → Sound → Output**
6. Select the Multi-Output Device

Audio now plays through your speakers AND gets captured by Claude Ears simultaneously.

## Finding Your BlackHole Device Index

Run this to find the right device number:

```bash
ffmpeg -f avfoundation -list_devices true -i "" 2>&1 | grep -i "blackhole\|built-in"
```

Update `BLACKHOLE_DEVICE` in `claude-ears.py` if needed (default: 0).
