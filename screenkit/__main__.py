"""Command-line interface for Screen Studio (``python -m screenkit``).

Subcommands
-----------
``shot``     capture the screen (full / region / a specific monitor) and save.
``record``   record the screen to MP4 or GIF (needs ffmpeg; live grab is
             Windows/gdigrab, best-effort x11grab on Linux, avfoundation on mac).
``annotate`` apply one annotation op to an existing image and save.

Every failure surfaces as a single clean line on stderr (the
:class:`ScreenKitError` message -- e.g. "no display" or "ffmpeg not found") and a
non-zero exit code; a raw traceback is never shown.
"""

from __future__ import annotations

import argparse
import sys

from .errors import ScreenKitError

PROG = "screenkit"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _parse_region(text):
    """Parse an ``x,y,w,h`` string into a 4-int tuple."""
    parts = [p.strip() for p in str(text).split(",")]
    if len(parts) != 4:
        raise ScreenKitError("--region must be 'x,y,w,h' (four numbers).")
    try:
        return tuple(int(p) for p in parts)
    except ValueError:
        raise ScreenKitError("--region values must be whole numbers.")


def _parse_xy(text):
    parts = [p.strip() for p in str(text).split(",")]
    if len(parts) != 2:
        raise ScreenKitError("Position must be 'x,y' (two numbers).")
    try:
        return tuple(int(p) for p in parts)
    except ValueError:
        raise ScreenKitError("Position values must be whole numbers.")


# ---------------------------------------------------------------------------
# subcommand handlers
# ---------------------------------------------------------------------------
def _cmd_shot(args):
    from . import capture
    if args.region:
        x, y, w, h = _parse_region(args.region)
        img = capture.capture_region(x, y, w, h)
    elif args.monitor is not None:
        img = capture.capture_monitor(args.monitor)
    else:  # default / --full
        img = capture.capture_full()
    out = args.out or "screenshot.png"
    capture.save_image(img, out, fmt=args.format, quality=args.quality)
    print(f"Saved screenshot -> {out} ({img.width}x{img.height})")
    return 0


def _cmd_record(args):
    from . import ffmpeg
    region = _parse_region(args.region) if args.region else None
    out = args.out or ("recording.gif" if args.gif else "recording.mp4")
    if args.duration is None:
        # Without a duration a blocking CLI run would never return; require it.
        raise ScreenKitError(
            "Please pass --duration SECONDS for a CLI recording (the GUI "
            "offers interactive Start/Stop instead).")
    ffmpeg.record(out, fps=args.fps, region=region,
                  duration=args.duration, gif=args.gif)
    print(f"Saved recording -> {out}")
    return 0


def _cmd_annotate(args):
    from PIL import Image
    from . import annotate, capture
    try:
        img = Image.open(args.image)
        img.load()
    except Exception as exc:
        raise ScreenKitError(f"Could not open image {args.image!r}: {exc}")

    op = args.op
    color = args.color
    if op in ("rect", "box"):
        img = annotate.rect(img, _parse_region_box(args.box), color=color,
                            width=args.width)
    elif op == "ellipse":
        img = annotate.ellipse(img, _parse_region_box(args.box), color=color,
                               width=args.width)
    elif op == "highlight":
        img = annotate.highlight(img, _parse_region_box(args.box), color=color)
    elif op == "blur":
        img = annotate.blur_region(img, _parse_region_box(args.box))
    elif op == "crop":
        img = annotate.crop(img, _parse_region_box(args.box))
    elif op == "arrow":
        p0, p1 = _two_points(args.points)
        img = annotate.arrow(img, p0, p1, color=color, width=args.width)
    elif op == "line":
        p0, p1 = _two_points(args.points)
        img = annotate.line(img, p0, p1, color=color, width=args.width)
    elif op == "text":
        img = annotate.text(img, _parse_xy(args.at), args.text or "",
                            size=args.size, color=color)
    elif op == "number":
        img = annotate.numbered_step(img, _parse_xy(args.at), args.n,
                                     color=color)
    else:  # pragma: no cover - argparse restricts choices
        raise ScreenKitError(f"Unknown annotate op: {op}")

    out = args.out or _suffixed(args.image)
    capture.save_image(img, out, quality=args.quality)
    print(f"Annotated ({op}) -> {out}")
    return 0


def _parse_region_box(text):
    """A box for annotate is x,y,w,h -> (x0,y0,x1,y1)."""
    if not text:
        raise ScreenKitError("This op needs --box 'x,y,w,h'.")
    x, y, w, h = _parse_region(text)
    return (x, y, x + w, y + h)


def _two_points(text):
    if not text:
        raise ScreenKitError("This op needs --points 'x0,y0,x1,y1'.")
    vals = _parse_region(text)
    return (vals[0], vals[1]), (vals[2], vals[3])


def _suffixed(path):
    import os
    base, ext = os.path.splitext(path)
    return f"{base}_annotated{ext or '.png'}"


# ---------------------------------------------------------------------------
# parser
# ---------------------------------------------------------------------------
def build_parser():
    p = argparse.ArgumentParser(
        prog=PROG,
        description="Screen Studio -- offline screenshots, annotation and "
                    "screen recording (by QuickOpen, quickopen.ai).")
    p.add_argument("--version", action="version",
                   version="%(prog)s 1.0.0")
    sub = p.add_subparsers(dest="command")

    # shot
    ps = sub.add_parser("shot", help="capture the screen and save an image")
    g = ps.add_mutually_exclusive_group()
    g.add_argument("--full", action="store_true",
                   help="capture the whole virtual screen (default)")
    g.add_argument("--region", metavar="x,y,w,h",
                   help="capture a rectangle")
    g.add_argument("--monitor", type=int, metavar="N",
                   help="capture monitor N (1-based; 0 = all)")
    ps.add_argument("--out", "-o", help="output path (default screenshot.png)")
    ps.add_argument("--format", help="override output format (png/jpg/...)")
    ps.add_argument("--quality", type=int, default=92,
                    help="JPEG/WebP quality (default 92)")
    ps.set_defaults(func=_cmd_shot)

    # record
    pr = sub.add_parser("record", help="record the screen to MP4 or GIF")
    pr.add_argument("--out", "-o", help="output path (default recording.mp4)")
    pr.add_argument("--fps", type=int, default=15, help="frames/sec (default 15)")
    pr.add_argument("--region", metavar="x,y,w,h",
                    help="record a rectangle instead of the full desktop")
    pr.add_argument("--duration", "-t", type=float, metavar="SECONDS",
                    help="stop after SECONDS (required on the CLI)")
    pr.add_argument("--gif", action="store_true",
                    help="produce an animated GIF instead of MP4")
    pr.set_defaults(func=_cmd_record)

    # annotate
    pa = sub.add_parser("annotate", help="apply one annotation op to an image")
    pa.add_argument("image", help="input image path")
    pa.add_argument("op", choices=["arrow", "rect", "box", "ellipse", "line",
                                   "text", "highlight", "blur", "crop",
                                   "number"],
                    help="the annotation to apply")
    pa.add_argument("--box", metavar="x,y,w,h",
                    help="rectangle for rect/ellipse/highlight/blur/crop")
    pa.add_argument("--points", metavar="x0,y0,x1,y1",
                    help="endpoints for arrow/line")
    pa.add_argument("--at", metavar="x,y", help="position for text/number")
    pa.add_argument("--text", help="the string for the 'text' op")
    pa.add_argument("--n", type=int, default=1, help="the number for 'number'")
    pa.add_argument("--size", type=int, default=24, help="font size for text")
    pa.add_argument("--width", type=int, default=4, help="stroke width")
    pa.add_argument("--color", default="#ff3b30", help="colour (name or #hex)")
    pa.add_argument("--out", "-o", help="output path (default *_annotated)")
    pa.add_argument("--quality", type=int, default=92, help="JPEG/WebP quality")
    pa.set_defaults(func=_cmd_annotate)

    return p


def main(argv=None):
    """CLI entry point. Returns a process exit code (0 on success)."""
    argv = list(sys.argv[1:] if argv is None else argv)
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "command", None):
        parser.print_help()
        return 0
    try:
        return args.func(args) or 0
    except ScreenKitError as exc:
        print(f"{PROG}: error: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:  # pragma: no cover
        print(f"{PROG}: interrupted", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
