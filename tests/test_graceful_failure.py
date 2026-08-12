"""Tests for graceful degradation: no display, no ffmpeg (simulated)."""

import builtins

import pytest

from screenkit import capture as cap
from screenkit import ffmpeg as ff
from screenkit.errors import ScreenKitError


# ---- ffmpeg missing -------------------------------------------------------
def test_ffmpeg_path_raises_when_missing(monkeypatch):
    monkeypatch.setattr(ff, "find_ffmpeg", lambda: None)
    with pytest.raises(ScreenKitError) as ei:
        ff.ffmpeg_path()
    assert "ffmpeg" in str(ei.value).lower()


def test_record_raises_when_ffmpeg_missing(monkeypatch):
    monkeypatch.setattr(ff, "find_ffmpeg", lambda: None)
    with pytest.raises(ScreenKitError):
        ff.record("out.mp4", duration=1)


def test_recorder_start_raises_when_ffmpeg_missing(monkeypatch):
    monkeypatch.setattr(ff, "find_ffmpeg", lambda: None)
    rec = ff.Recorder("out.mp4")
    with pytest.raises(ScreenKitError):
        rec.start()


def test_recorder_stop_without_start_raises():
    rec = ff.Recorder("out.mp4")
    with pytest.raises(ScreenKitError):
        rec.stop()


# ---- capture with no display ----------------------------------------------
def _kill_mss(monkeypatch):
    """Simulate a headless host where creating an mss instance fails."""
    class _Boom:
        def mss(self):
            raise RuntimeError("no display")
    monkeypatch.setattr(cap, "_import_mss", lambda: _Boom())


def test_capture_full_raises_headless(monkeypatch):
    _kill_mss(monkeypatch)
    with pytest.raises(ScreenKitError) as ei:
        cap.capture_full()
    assert "display" in str(ei.value).lower()


def test_capture_region_raises_headless(monkeypatch):
    _kill_mss(monkeypatch)
    with pytest.raises(ScreenKitError):
        cap.capture_region(0, 0, 10, 10)


def test_capture_monitor_raises_headless(monkeypatch):
    _kill_mss(monkeypatch)
    with pytest.raises(ScreenKitError):
        cap.capture_monitor(1)


def test_list_monitors_raises_headless(monkeypatch):
    _kill_mss(monkeypatch)
    with pytest.raises(ScreenKitError):
        cap.list_monitors()


def test_capture_available_false_headless(monkeypatch):
    _kill_mss(monkeypatch)
    assert cap.capture_available() is False


def test_capture_region_rejects_bad_size():
    with pytest.raises(ScreenKitError):
        cap.capture_region(0, 0, 0, 100)


def test_import_mss_missing(monkeypatch):
    """When mss itself can't be imported, a clear error is raised."""
    real_import = builtins.__import__

    def fake_import(name, *a, **k):
        if name == "mss":
            raise ImportError("no module named mss")
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    with pytest.raises(ScreenKitError):
        cap._import_mss()


# ---- save/clipboard guards ------------------------------------------------
def test_save_image_rejects_non_image():
    with pytest.raises(ScreenKitError):
        cap.save_image("not an image", "x.png")


def test_to_clipboard_noop_off_windows(monkeypatch):
    from PIL import Image
    monkeypatch.setattr(cap.os, "name", "posix")
    assert cap.to_clipboard(Image.new("RGB", (4, 4))) is False
