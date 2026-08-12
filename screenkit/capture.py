"""Screen capture via ``mss`` -- guarded for headless hosts.

``mss`` is imported lazily and every grab is wrapped so that a machine with no
display (a CI box, a headless server) raises a clear :class:`ScreenKitError`
("no display / screen capture is not available here") instead of crashing with a
backend-specific exception.  Importing this module therefore never requires a
display, and the test-suite can exercise the failure path without one.

All capture functions return a :class:`PIL.Image.Image` in ``RGB`` mode.
``save_image`` writes one to disk; ``to_clipboard`` copies one to the Windows
clipboard (a guarded no-op elsewhere).
"""

from __future__ import annotations

import os

from .errors import ScreenKitError

__all__ = [
    "list_monitors", "capture_full", "capture_region", "capture_monitor",
    "save_image", "to_clipboard", "capture_available",
]

_NO_DISPLAY_HINT = (
    "Screen capture is not available here (no display was found). "
    "Screen Studio captures the screen on a desktop session; on a headless "
    "server there is nothing to grab.")


# ---------------------------------------------------------------------------
# mss access (lazy + guarded)
# ---------------------------------------------------------------------------
def _import_mss():
    try:
        import mss  # noqa: WPS433 (intentional lazy import)
        return mss
    except Exception as exc:
        raise ScreenKitError(
            f"The screen-capture backend (mss) is unavailable: {exc}")


def _sct():
    """Return a live ``mss`` instance or raise a friendly ScreenKitError."""
    mss = _import_mss()
    try:
        return mss.mss()
    except Exception as exc:
        # mss raises ScreenShotError / XError on headless X, etc.
        raise ScreenKitError(f"{_NO_DISPLAY_HINT} ({exc})")


def capture_available():
    """Return ``True`` if the screen can be grabbed here (never raises)."""
    try:
        with _sct() as sct:
            _ = sct.monitors
        return True
    except Exception:
        return False


def _to_image(shot):
    """Convert an ``mss`` screenshot to a PIL RGB Image."""
    from PIL import Image
    return Image.frombytes("RGB", (shot.width, shot.height),
                           shot.rgb)


# ---------------------------------------------------------------------------
# monitors
# ---------------------------------------------------------------------------
def list_monitors():
    """Return a list of monitor dicts ``{index, left, top, width, height}``.

    Index 0 is the "all monitors" virtual screen (mss convention); 1..N are the
    individual displays.  Raises :class:`ScreenKitError` on a headless host.
    """
    with _sct() as sct:
        out = []
        for i, mon in enumerate(sct.monitors):
            out.append({
                "index": i,
                "left": mon.get("left", 0),
                "top": mon.get("top", 0),
                "width": mon.get("width", 0),
                "height": mon.get("height", 0),
            })
        return out


# ---------------------------------------------------------------------------
# captures
# ---------------------------------------------------------------------------
def capture_full():
    """Capture the entire virtual screen (all monitors) as a PIL Image."""
    with _sct() as sct:
        try:
            shot = sct.grab(sct.monitors[0])
        except Exception as exc:
            raise ScreenKitError(f"{_NO_DISPLAY_HINT} ({exc})")
        return _to_image(shot)


def capture_monitor(index=1):
    """Capture a single monitor by index (1-based; 0 = whole virtual screen)."""
    with _sct() as sct:
        mons = sct.monitors
        if index < 0 or index >= len(mons):
            raise ScreenKitError(
                f"Monitor {index} does not exist (have {len(mons) - 1} "
                f"display(s); use 1..{len(mons) - 1}, or 0 for all).")
        try:
            shot = sct.grab(mons[index])
        except Exception as exc:
            raise ScreenKitError(f"{_NO_DISPLAY_HINT} ({exc})")
        return _to_image(shot)


def capture_region(x, y, w, h):
    """Capture a rectangle at ``(x, y)`` of size ``w`` x ``h`` (screen coords)."""
    if w <= 0 or h <= 0:
        raise ScreenKitError("Region width and height must be positive.")
    region = {"left": int(x), "top": int(y),
              "width": int(w), "height": int(h)}
    with _sct() as sct:
        try:
            shot = sct.grab(region)
        except Exception as exc:
            raise ScreenKitError(f"{_NO_DISPLAY_HINT} ({exc})")
        return _to_image(shot)


# ---------------------------------------------------------------------------
# output
# ---------------------------------------------------------------------------
def save_image(img, path, fmt=None, quality=92):
    """Save a PIL image to ``path``; returns the path.

    ``fmt`` overrides the format inferred from the extension.  ``quality``
    applies to JPEG/WebP.  Raises :class:`ScreenKitError` on any write error.
    """
    from PIL import Image
    if not isinstance(img, Image.Image):
        raise ScreenKitError("save_image expects a PIL Image.")
    if not path:
        raise ScreenKitError("An output path is required.")
    ext = (fmt or os.path.splitext(path)[1].lstrip(".") or "png").lower()
    pil_fmt = {"jpg": "JPEG", "jpeg": "JPEG", "png": "PNG",
               "bmp": "BMP", "gif": "GIF", "tif": "TIFF", "tiff": "TIFF",
               "webp": "WEBP"}.get(ext, ext.upper())
    save_img = img
    params = {}
    if pil_fmt in ("JPEG", "WEBP"):
        params["quality"] = int(quality)
        if pil_fmt == "JPEG" and img.mode in ("RGBA", "P", "LA"):
            save_img = img.convert("RGB")
    try:
        directory = os.path.dirname(os.path.abspath(path))
        if directory:
            os.makedirs(directory, exist_ok=True)
        save_img.save(path, format=pil_fmt, **params)
    except Exception as exc:
        raise ScreenKitError(f"Could not save image to {path}: {exc}")
    return path


def to_clipboard(img):
    """Copy a PIL image to the clipboard.

    Implemented on Windows (via the ``win32clipboard`` API as DIB); on other
    platforms it is a guarded no-op returning ``False`` so callers can inform
    the user.  Returns ``True`` when the image was placed on the clipboard.
    """
    from PIL import Image
    if not isinstance(img, Image.Image):
        raise ScreenKitError("to_clipboard expects a PIL Image.")
    if os.name != "nt":
        return False  # clipboard image copy is Windows-only here
    return _windows_clipboard(img)


def _windows_clipboard(img):  # pragma: no cover - Windows-only path
    """Place *img* on the Windows clipboard as a DIB. Returns bool."""
    try:
        import io
        import win32clipboard  # type: ignore
        output = io.BytesIO()
        img.convert("RGB").save(output, "BMP")
        data = output.getvalue()[14:]  # strip the 14-byte BMP file header
        output.close()
        win32clipboard.OpenClipboard()
        try:
            win32clipboard.EmptyClipboard()
            win32clipboard.SetClipboardData(win32clipboard.CF_DIB, data)
        finally:
            win32clipboard.CloseClipboard()
        return True
    except Exception:
        return False
