"""Tests for the pure ffmpeg argument builders (no subprocess, no ffmpeg)."""

import pytest

from screenkit import ffmpeg as ff
from screenkit.errors import ScreenKitError


# ---- backend selection ----------------------------------------------------
def test_grab_backend_per_platform():
    assert ff.grab_backend("win32") == "gdigrab"
    assert ff.grab_backend("darwin") == "avfoundation"
    assert ff.grab_backend("linux") == "x11grab"


# ---- MP4 / gdigrab --------------------------------------------------------
def test_mp4_gdigrab_full_desktop():
    args = ff.build_record_args("out.mp4", fps=30, platform="win32")
    assert args[0] == "ffmpeg"
    assert "-f" in args and args[args.index("-f") + 1] == "gdigrab"
    assert args[args.index("-framerate") + 1] == "30"
    assert args[args.index("-i") + 1] == "desktop"
    assert "libx264" in args
    assert "-pix_fmt" in args and args[args.index("-pix_fmt") + 1] == "yuv420p"
    assert args[-1] == "out.mp4"


def test_mp4_gdigrab_region_offsets_and_size():
    args = ff.build_record_args("r.mp4", fps=24, region=(100, 50, 640, 480),
                                platform="win32")
    assert args[args.index("-offset_x") + 1] == "100"
    assert args[args.index("-offset_y") + 1] == "50"
    assert args[args.index("-video_size") + 1] == "640x480"
    assert args[args.index("-framerate") + 1] == "24"


def test_duration_flag_added():
    args = ff.build_record_args("d.mp4", fps=15, duration=5, platform="win32")
    assert "-t" in args
    assert args[args.index("-t") + 1] == "5.0"


# ---- GIF ------------------------------------------------------------------
def test_gif_uses_palette_filtergraph():
    args = ff.build_record_args("clip.gif", fps=12, gif=True, platform="win32")
    assert "-vf" in args
    vf = args[args.index("-vf") + 1]
    assert "palettegen" in vf and "paletteuse" in vf
    assert "fps=12" in vf
    assert "-loop" in args and args[args.index("-loop") + 1] == "0"
    assert args[-1] == "clip.gif"


def test_gif_region_and_scale():
    args = ff.build_gif_args("s.gif", fps=10, region=(0, 0, 800, 600),
                             scale=480, platform="win32")
    vf = args[args.index("-vf") + 1]
    assert "scale=480:-1:flags=lanczos" in vf
    assert args[args.index("-video_size") + 1] == "800x600"


# ---- x11grab (linux) ------------------------------------------------------
def test_x11grab_full_and_region(monkeypatch):
    monkeypatch.setenv("DISPLAY", ":0.0")
    full = ff.build_record_args("l.mp4", fps=15, platform="linux")
    assert full[full.index("-f") + 1] == "x11grab"
    assert full[full.index("-i") + 1] == ":0.0"

    region = ff.build_record_args("l.mp4", fps=15, region=(10, 20, 300, 200),
                                  platform="linux")
    assert region[region.index("-video_size") + 1] == "300x200"
    assert region[region.index("-i") + 1] == ":0.0+10,20"


# ---- validation -----------------------------------------------------------
def test_missing_output_raises():
    with pytest.raises(ScreenKitError):
        ff.build_record_args("", fps=15, platform="win32")


def test_bad_duration_raises():
    with pytest.raises(ScreenKitError):
        ff.build_record_args("o.mp4", duration=0, platform="win32")
    with pytest.raises(ScreenKitError):
        ff.build_record_args("o.mp4", duration=-3, platform="win32")


def test_backend_override():
    args = ff.build_record_args("o.mp4", fps=15, backend="x11grab",
                                platform="win32")
    # explicit backend wins over the platform default
    assert args[args.index("-f") + 1] == "x11grab"
