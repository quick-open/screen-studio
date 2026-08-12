"""FFmpeg discovery + recording.

FFmpeg is an EXTERNAL program (LGPL/GPL) that Screen Studio invokes as a
subprocess -- it is never linked into or bundled by this code.  On a packaged
Windows build the ``ffmpeg.exe`` binary ships alongside ``ScreenStudio.exe``;
from source it is found on ``PATH`` or in a few common locations.

The argument builders (:func:`build_record_args`, :func:`build_gif_args`) are
pure functions that return a list of strings -- no I/O, no subprocess -- so they
are fully unit-tested cross-platform.  :func:`record` / :class:`Recorder` run
them.

Live screen capture uses the platform's ffmpeg screen grabber:
  * Windows  -> ``gdigrab``  (fully supported / the shipping target)
  * Linux    -> ``x11grab``  (best-effort; requires an X display + ffmpeg)
  * macOS    -> ``avfoundation`` (best-effort)
Only Windows is a supported product surface; the others exist so the builders
and CLI are exercisable everywhere.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time

from .errors import ScreenKitError

__all__ = [
    "ffmpeg_path", "find_ffmpeg", "grab_backend",
    "build_record_args", "build_gif_args", "record", "Recorder",
]

FFMPEG_NAMES = ("ffmpeg.exe", "ffmpeg") if os.name == "nt" else ("ffmpeg",)


# ---------------------------------------------------------------------------
# discovery
# ---------------------------------------------------------------------------
def _candidate_dirs():
    """Directories to search for a bundled ffmpeg, most-specific first."""
    dirs = []
    if getattr(sys, "frozen", False):
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            dirs.append(meipass)
        dirs.append(os.path.dirname(os.path.abspath(sys.executable)))
    else:
        here = os.path.dirname(os.path.abspath(__file__))
        dirs += [here, os.path.dirname(here), os.getcwd()]
    # common install locations (Windows + *nix)
    if os.name == "nt":
        for env in ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA"):
            base = os.environ.get(env)
            if base:
                dirs.append(os.path.join(base, "ffmpeg", "bin"))
    else:
        dirs += ["/usr/bin", "/usr/local/bin", "/opt/homebrew/bin",
                 "/snap/bin"]
    return dirs


def find_ffmpeg():
    """Return an absolute path to an ffmpeg binary, or ``None`` if not found.

    Search order: next to a frozen exe / the app dir, then ``PATH``, then a set
    of common install directories.  Never raises.
    """
    # 1) bundled / app directory (checked before PATH so a shipped copy wins)
    for d in _candidate_dirs():
        for name in FFMPEG_NAMES:
            cand = os.path.join(d, name)
            if os.path.isfile(cand) and os.access(cand, os.X_OK):
                return os.path.abspath(cand)
    # 2) PATH
    for name in FFMPEG_NAMES:
        found = shutil.which(name)
        if found:
            return os.path.abspath(found)
    return None


def ffmpeg_path():
    """Return the ffmpeg path or raise a clear :class:`ScreenKitError`."""
    path = find_ffmpeg()
    if not path:
        raise ScreenKitError(
            "FFmpeg was not found. Screen recording needs an ffmpeg binary "
            "next to the app or on your PATH. Download it from "
            "https://ffmpeg.org and place ffmpeg"
            + (".exe" if os.name == "nt" else "")
            + " beside the program (or add it to PATH).")
    return path


def grab_backend(platform=None):
    """Return the ffmpeg screen-grab input format for a platform.

    ``platform`` defaults to ``sys.platform``; kept as a parameter so the arg
    builders can be tested for every OS from one machine.
    """
    plat = platform if platform is not None else sys.platform
    if plat.startswith("win"):
        return "gdigrab"
    if plat == "darwin":
        return "avfoundation"
    return "x11grab"


# ---------------------------------------------------------------------------
# pure argument builders  (no subprocess, no I/O -- unit tested)
# ---------------------------------------------------------------------------
def _grab_input_args(backend, fps, region):
    """Input-side args (``-f ... -i ...``) for the given grab backend.

    ``region`` is ``(x, y, w, h)`` or ``None`` for the whole desktop.
    """
    fps = str(int(fps))
    args = ["-f", backend, "-framerate", fps]
    if backend == "gdigrab":
        if region:
            x, y, w, h = (int(v) for v in region)
            args += ["-offset_x", str(x), "-offset_y", str(y),
                     "-video_size", f"{w}x{h}"]
        args += ["-i", "desktop"]
    elif backend == "x11grab":
        if region:
            x, y, w, h = (int(v) for v in region)
            args += ["-video_size", f"{w}x{h}",
                     "-i", f"{os.environ.get('DISPLAY', ':0.0')}+{x},{y}"]
        else:
            args += ["-i", os.environ.get("DISPLAY", ":0.0")]
    elif backend == "avfoundation":
        # macOS: capture the main screen device (index 1 by convention).
        if region:
            x, y, w, h = (int(v) for v in region)
            args += ["-video_size", f"{w}x{h}"]
        args += ["-i", "1:none"]
    else:  # pragma: no cover - defensive
        raise ScreenKitError(f"Unknown grab backend: {backend}")
    return args


def build_record_args(out, fps=15, region=None, duration=None, gif=False,
                      backend=None, platform=None):
    """Build the full ffmpeg argument list for a screen recording.

    Parameters
    ----------
    out : str        output file path.
    fps : int        frames per second (input + output rate).
    region : tuple   ``(x, y, w, h)`` to capture, or ``None`` for full desktop.
    duration : float optional cap in seconds (``-t``).
    gif : bool       when ``True`` produce an animated GIF (delegates to
                     :func:`build_gif_args`), otherwise an H.264 MP4.
    backend : str    override the grab backend (else derived from ``platform``).
    platform : str   override ``sys.platform`` for cross-platform testing.

    Returns a ``list[str]`` beginning with ``ffmpeg`` (the caller substitutes
    the resolved binary path).  Pure -- performs no I/O.
    """
    if gif:
        return build_gif_args(out, fps=fps, region=region, duration=duration,
                              backend=backend, platform=platform)
    if not out:
        raise ScreenKitError("An output path is required.")
    backend = backend or grab_backend(platform)
    args = ["ffmpeg", "-y"]
    args += _grab_input_args(backend, fps, region)
    if duration is not None:
        args += ["-t", str(_dur(duration))]
    args += [
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-pix_fmt", "yuv420p",
        "-movflags", "+faststart",
        "-r", str(int(fps)),
        out,
    ]
    return args


def build_gif_args(out, fps=12, region=None, duration=None, scale=None,
                   backend=None, platform=None):
    """Build ffmpeg args for a palette-based (high-quality) GIF capture.

    Uses a single-pass ``palettegen``/``paletteuse`` filter graph so colours
    stay clean; ``scale`` (width in px, height auto) optionally downscales.
    Pure -- returns a ``list[str]`` and performs no I/O.
    """
    if not out:
        raise ScreenKitError("An output path is required.")
    backend = backend or grab_backend(platform)
    args = ["ffmpeg", "-y"]
    args += _grab_input_args(backend, fps, region)
    if duration is not None:
        args += ["-t", str(_dur(duration))]
    scale_part = f"scale={int(scale)}:-1:flags=lanczos," if scale else ""
    vf = (f"fps={int(fps)},{scale_part}"
          "split[s0][s1];[s0]palettegen[p];[s1][p]paletteuse")
    args += ["-vf", vf, "-loop", "0", out]
    return args


def _dur(duration):
    try:
        d = float(duration)
    except Exception:
        raise ScreenKitError("Duration must be a number of seconds.")
    if d <= 0:
        raise ScreenKitError("Duration must be positive.")
    return round(d, 3)


# ---------------------------------------------------------------------------
# running ffmpeg
# ---------------------------------------------------------------------------
def _popen_kwargs():
    """Suppress a console window on Windows; harmless elsewhere."""
    kwargs = {"stdin": subprocess.PIPE,
              "stdout": subprocess.PIPE,
              "stderr": subprocess.PIPE}
    if os.name == "nt":
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    return kwargs


def record(out, fps=15, region=None, duration=None, gif=False):
    """Record the screen to ``out`` (blocking).

    Requires an ffmpeg binary (raises :class:`ScreenKitError` if missing) and a
    working screen grabber for the current OS.  A ``duration`` is strongly
    recommended for the blocking form; without one, use :class:`Recorder`.
    Returns the output path on success.
    """
    binary = ffmpeg_path()
    args = build_record_args(out, fps=fps, region=region, duration=duration,
                             gif=gif)
    args[0] = binary
    try:
        proc = subprocess.run(args, stdin=subprocess.DEVNULL,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              **({"creationflags": getattr(
                                  subprocess, "CREATE_NO_WINDOW", 0)}
                                 if os.name == "nt" else {}))
    except FileNotFoundError:
        raise ScreenKitError("FFmpeg could not be launched.")
    except Exception as exc:  # pragma: no cover - environment dependent
        raise ScreenKitError(f"FFmpeg failed to start: {exc}")
    if proc.returncode != 0:
        tail = (proc.stderr or b"").decode("utf-8", "replace").strip()
        tail = tail.splitlines()[-1] if tail else "unknown error"
        raise ScreenKitError(f"FFmpeg recording failed: {tail}")
    if not os.path.exists(out):
        raise ScreenKitError("FFmpeg finished but produced no output file.")
    return out


class Recorder:
    """Start/stop screen recording without a fixed duration.

    Writes to a temporary file while running and renames it into place on a
    clean stop, so a half-written capture never clobbers a good file.  ``stop``
    signals ffmpeg to finalise the container gracefully (``q`` on stdin, then a
    terminate fallback).  Keep-it-simple: one recording at a time.
    """

    def __init__(self, out, fps=15, region=None, gif=False):
        self.out = out
        self.fps = fps
        self.region = region
        self.gif = gif
        self._proc = None
        self._tmp = None
        self._start = None

    @property
    def running(self):
        return self._proc is not None and self._proc.poll() is None

    def start(self):
        """Launch ffmpeg. Raises :class:`ScreenKitError` on any startup issue."""
        if self.running:
            raise ScreenKitError("A recording is already in progress.")
        binary = ffmpeg_path()
        base, ext = os.path.splitext(self.out)
        if not ext:
            ext = ".gif" if self.gif else ".mp4"
            self.out = base + ext
        self._tmp = f"{base}.recording{ext}"
        args = build_record_args(self._tmp, fps=self.fps, region=self.region,
                                 gif=self.gif)
        args[0] = binary
        try:
            self._proc = subprocess.Popen(args, **_popen_kwargs())
        except FileNotFoundError:
            raise ScreenKitError("FFmpeg could not be launched.")
        except Exception as exc:  # pragma: no cover
            raise ScreenKitError(f"FFmpeg failed to start: {exc}")
        # Fail fast if it dies immediately (e.g. no display / bad backend).
        time.sleep(0.3)
        if self._proc.poll() is not None:
            tail = (self._proc.stderr.read() or b"").decode("utf-8", "replace")
            last = tail.strip().splitlines()[-1] if tail.strip() else "unknown"
            self._proc = None
            raise ScreenKitError(f"FFmpeg could not start recording: {last}")
        self._start = time.time()
        return self

    def elapsed(self):
        return 0.0 if self._start is None else max(0.0, time.time() - self._start)

    def stop(self):
        """Finalise the recording and return the output path.

        Sends ``q`` so ffmpeg writes a valid trailer, then falls back to
        terminate/kill.  Renames the temp file into the requested output path.
        """
        if self._proc is None:
            raise ScreenKitError("No recording is in progress.")
        proc, self._proc = self._proc, None
        try:
            if proc.poll() is None:
                try:
                    proc.communicate(input=b"q", timeout=8)
                except Exception:
                    proc.terminate()
                    try:
                        proc.wait(timeout=5)
                    except Exception:
                        proc.kill()
        finally:
            pass
        if self._tmp and os.path.exists(self._tmp):
            try:
                if os.path.exists(self.out):
                    os.remove(self.out)
                os.replace(self._tmp, self.out)
            except Exception as exc:
                raise ScreenKitError(f"Could not finalise recording: {exc}")
        else:
            raise ScreenKitError("Recording stopped but no output was written.")
        return self.out

    def cancel(self):
        """Abort and delete any partial output (best-effort, never raises)."""
        proc, self._proc = self._proc, None
        try:
            if proc is not None and proc.poll() is None:
                proc.kill()
        except Exception:
            pass
        try:
            if self._tmp and os.path.exists(self._tmp):
                os.remove(self._tmp)
        except Exception:
            pass
