"""Screen Studio -- ``screenkit`` core library.

A small, dependency-light toolkit for screenshots, annotation and screen
recording, published on QuickOpen (quickopen.ai).

The package is deliberately layered so the pure, testable parts never touch a
display or an external binary:

  * :mod:`screenkit.errors`   -- the single :class:`ScreenKitError` type.
  * :mod:`screenkit.annotate` -- pure-PIL drawing (arrows, boxes, text, blur...).
  * :mod:`screenkit.ffmpeg`   -- ffmpeg discovery + pure argument builders; the
    live recorder shells out to an EXTERNAL ffmpeg (LGPL/GPL) binary.
  * :mod:`screenkit.capture`  -- screen grab via ``mss`` (guarded so headless
    hosts raise a clear ``ScreenKitError`` instead of crashing).
  * :mod:`screenkit.gui`      -- a tkinter desktop app (import is side-effect
    free; only :func:`screenkit.gui.main` builds a window).

Importing :mod:`screenkit` pulls in nothing heavier than :mod:`errors`, so it is
safe on a headless CI box.  100% AI-built, open source, Apache-2.0.
"""

from __future__ import annotations

from .errors import ScreenKitError

__all__ = ["ScreenKitError", "__version__"]

__version__ = "1.1.1"
