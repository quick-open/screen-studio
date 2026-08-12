# Screen Studio

A fast, **offline**, **100% open-source** screenshot & screen-recorder for Windows. Nothing is uploaded anywhere. Built entirely by AI with human testing and guidance, and published on [QuickOpen](https://quickopen.ai/projects/screen-studio).

> **100% AI-built and open source.** Apache-2.0.

## What it does

Capture the full screen, a chosen window, or a dragged region; annotate captures with arrows, boxes, text and highlights; and record your screen (with a region selector and adjustable FPS) to MP4 or animated GIF using a bundled FFmpeg. Copy to clipboard or save. Everything is local.

## Features

- **Capture** — full screen, a specific monitor, or a dragged **region** (a translucent full-screen overlay you drag a rectangle on). A best-effort "current window" grab is available on Windows.
- **Annotate** — arrow, box, ellipse, line, text, marker **highlight**, **blur** (redact), **crop**, and numbered step call-outs, all drawn on a canvas with a colour/width/size toolbar. Undo, Save, and Copy-to-clipboard (Windows).
- **Record** — capture the full screen or a region to **MP4** (H.264) or animated **GIF** at a chosen frame-rate, with Start/Stop and a live elapsed timer. Recording runs FFmpeg on a background thread so the UI stays responsive.
- **Recent** — quick access to files you have saved.
- **Offline & safe** — pure tkinter + [mss](https://github.com/BoboTiG/python-mss) + [Pillow](https://python-pillow.org/); nothing is uploaded. Dark mode included. If FFmpeg is missing, a banner appears but capture and annotation keep working.

> Screen capture and live recording target the Windows desktop. On a headless host the app degrades gracefully with a clear message instead of crashing.

## Install

Download **`ScreenStudio-Setup.exe`** from the [QuickOpen page](https://quickopen.ai/projects/screen-studio) or the [GitHub release](https://github.com/quickpod/screen-studio/releases/latest) and double-click it. It installs per-user, adds Desktop and Start Menu shortcuts, and can optionally trust the QuickOpen Root CA. Authenticode-signed by the QuickOpen Code Signing CA — verify at [quickopen.ai/trust](https://quickopen.ai/trust).

## Run from source

```sh
pip install -r requirements.txt
python screen_studio_app.py          # GUI
python -m screenkit --help    # CLI
```

## CLI examples

Run the command-line interface with `python -m screenkit` (or `ScreenStudio.exe <args>`):

```sh
# Screenshots
python -m screenkit shot --full -o shot.png
python -m screenkit shot --region 100,80,640,480 -o area.png
python -m screenkit shot --monitor 1 -o mon1.png

# Screen recording (needs FFmpeg; --duration is required on the CLI)
python -m screenkit record --out clip.mp4 --fps 30 --duration 10
python -m screenkit record --out clip.gif --gif --fps 12 --region 0,0,800,600 -t 6

# Annotate an existing image (one op per call → saves a new file)
python -m screenkit annotate shot.png arrow  --points 20,20,200,160 --color "#ff3b30"
python -m screenkit annotate shot.png box     --box 40,40,300,200 --width 5
python -m screenkit annotate shot.png text    --at 30,30 --text "Look here" --size 28
python -m screenkit annotate shot.png number  --at 120,90 --n 1
python -m screenkit annotate shot.png blur    --box 50,50,260,120      # redact
python -m screenkit annotate shot.png crop    --box 10,10,400,300 -o cropped.png
```

Any failure (no display, FFmpeg not found, bad arguments) prints a single clear message and exits non-zero — never a traceback.

## FFmpeg

Screen recording uses a bundled **FFmpeg** (LGPL/GPL), invoked as a separate program; see `ffmpeg-LICENSE.txt` and https://ffmpeg.org. Screen Studio's own code is Apache-2.0.


## License

Apache-2.0 — see [LICENSE](LICENSE). A 100% AI-built project published on QuickOpen.
