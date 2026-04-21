P1 Alert Listener – Assets Folder
===================================

alert.wav
---------
Place a WAV audio file here named "alert.wav" to use WAV playback mode.

Requirements:
  - Format: PCM WAV (standard uncompressed .wav)
  - Recommended: short loop-friendly clip (1–5 seconds)
  - The winsound module on Windows requires standard PCM WAV files.
    MP3/OGG files are NOT supported – convert first with Audacity or ffmpeg.

Example sources for free alert sounds:
  - https://freesound.org (search "alarm", filter CC0 license)
  - ffmpeg -f lavfi -i "sine=frequency=1000:duration=1" alert.wav

If alert.wav is not present, the app falls back to winsound.Beep() automatically.

alert.ico
---------
Optional: Place a Windows .ico file here named "alert.ico" to set the taskbar icon.

Convert a PNG to ICO:
  - Online: https://convertio.co/png-ico/
  - Python: pip install pillow && python -c "from PIL import Image; Image.open('icon.png').save('alert.ico')"
