"""Pure-PIL annotation primitives for screenshots.

Every function here takes a :class:`PIL.Image.Image` and returns a NEW image
(the input is never mutated), so operations compose cleanly and are trivially
unit-tested without a display or any external program.  Coordinates are pixels
in image space; ``box`` arguments are ``(x0, y0, x1, y1)`` and are normalised so
callers may pass the corners in either order.

Colours accept anything Pillow understands -- an ``(r, g, b)`` / ``(r, g, b, a)``
tuple or a CSS-style name/`#rrggbb` string.

Design notes
------------
* Drawing happens on an ``RGBA`` working copy so semi-transparent overlays
  (highlight, blur tint) composite correctly; the result is converted back to
  the input's mode when it started as ``RGB``/``L`` so file output stays simple.
* Text and step-number sizing fall back to Pillow's bundled bitmap font when no
  scalable TrueType face is available, so it works on a bare CI image.
"""

from __future__ import annotations

import math

from PIL import Image, ImageColor, ImageDraw, ImageFilter, ImageFont

from .errors import ScreenKitError

__all__ = [
    "arrow", "rect", "ellipse", "line", "text", "highlight",
    "blur_region", "crop", "numbered_step",
]

DEFAULT_COLOR = "#ff3b30"   # QuickOpen "annotation red"
DEFAULT_WIDTH = 4


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _as_image(img):
    if not isinstance(img, Image.Image):
        raise ScreenKitError("Expected a PIL Image to annotate.")
    return img


def _norm_box(box):
    """Return an integer ``(x0, y0, x1, y1)`` with x0<=x1 and y0<=y1."""
    try:
        x0, y0, x1, y1 = box
    except Exception:
        raise ScreenKitError("Box must be (x0, y0, x1, y1).")
    x0, x1 = sorted((int(round(x0)), int(round(x1))))
    y0, y1 = sorted((int(round(y0)), int(round(y1))))
    return x0, y0, x1, y1


def _clamp_box(box, size):
    """Clamp a normalised box to the image bounds ``size`` = (w, h)."""
    x0, y0, x1, y1 = box
    w, h = size
    x0 = max(0, min(x0, w))
    x1 = max(0, min(x1, w))
    y0 = max(0, min(y0, h))
    y1 = max(0, min(y1, h))
    return x0, y0, x1, y1


def _color(value, default=DEFAULT_COLOR):
    """Normalise a colour to an ``(r, g, b, a)`` tuple."""
    if value is None:
        value = default
    if isinstance(value, (tuple, list)):
        vals = list(value)
        if len(vals) == 3:
            vals.append(255)
        if len(vals) != 4:
            raise ScreenKitError("Colour tuple must have 3 or 4 components.")
        return tuple(int(v) for v in vals)
    try:
        return ImageColor.getcolor(str(value), "RGBA")
    except Exception:
        raise ScreenKitError(f"Unrecognised colour: {value!r}")


def _work(img):
    """Return an RGBA working copy plus the original mode (for restore)."""
    return img.convert("RGBA"), img.mode


def _restore(work, mode):
    """Composite/convert an RGBA working image back to the original mode."""
    if mode == "RGBA":
        return work
    if mode in ("RGB", "L", "P"):
        bg = Image.new("RGBA", work.size, (255, 255, 255, 255))
        bg.alpha_composite(work)
        return bg.convert("RGB" if mode != "L" else "L")
    return work.convert(mode)


def _load_font(size):
    """Best-effort scalable font; falls back to the bundled bitmap font."""
    size = max(6, int(size))
    for name in ("DejaVuSans.ttf", "Arial.ttf", "arial.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except Exception:
            continue
    try:
        return ImageFont.load_default(size)  # Pillow >= 10.1 accepts a size
    except Exception:
        return ImageFont.load_default()


# ---------------------------------------------------------------------------
# shape primitives
# ---------------------------------------------------------------------------
def rect(img, box, color=DEFAULT_COLOR, width=DEFAULT_WIDTH, fill=None):
    """Draw a rectangle outline (optionally filled) and return a new image."""
    img = _as_image(img)
    work, mode = _work(img)
    draw = ImageDraw.Draw(work)
    draw.rectangle(_norm_box(box), outline=_color(color),
                   width=max(1, int(width)),
                   fill=_color(fill) if fill is not None else None)
    return _restore(work, mode)


def ellipse(img, box, color=DEFAULT_COLOR, width=DEFAULT_WIDTH, fill=None):
    """Draw an ellipse outline (optionally filled) and return a new image."""
    img = _as_image(img)
    work, mode = _work(img)
    draw = ImageDraw.Draw(work)
    draw.ellipse(_norm_box(box), outline=_color(color),
                 width=max(1, int(width)),
                 fill=_color(fill) if fill is not None else None)
    return _restore(work, mode)


def line(img, xy0, xy1, color=DEFAULT_COLOR, width=DEFAULT_WIDTH):
    """Draw a straight line between two points and return a new image."""
    img = _as_image(img)
    work, mode = _work(img)
    draw = ImageDraw.Draw(work)
    p0 = (int(round(xy0[0])), int(round(xy0[1])))
    p1 = (int(round(xy1[0])), int(round(xy1[1])))
    draw.line([p0, p1], fill=_color(color), width=max(1, int(width)))
    return _restore(work, mode)


def arrow(img, xy0, xy1, color=DEFAULT_COLOR, width=DEFAULT_WIDTH,
          head=None):
    """Draw an arrow from ``xy0`` to ``xy1`` (arrowhead at ``xy1``).

    The arrowhead is a filled triangle whose length scales with ``width``
    unless an explicit ``head`` length (pixels) is given.
    """
    img = _as_image(img)
    work, mode = _work(img)
    draw = ImageDraw.Draw(work)
    col = _color(color)
    w = max(1, int(width))
    x0, y0 = float(xy0[0]), float(xy0[1])
    x1, y1 = float(xy1[0]), float(xy1[1])
    draw.line([(x0, y0), (x1, y1)], fill=col, width=w)

    ang = math.atan2(y1 - y0, x1 - x0)
    hlen = float(head) if head else max(12.0, w * 4.0)
    spread = math.radians(28)
    left = (x1 - hlen * math.cos(ang - spread),
            y1 - hlen * math.sin(ang - spread))
    right = (x1 - hlen * math.cos(ang + spread),
             y1 - hlen * math.sin(ang + spread))
    draw.polygon([(x1, y1), left, right], fill=col)
    return _restore(work, mode)


def text(img, xy, string, size=24, color=DEFAULT_COLOR, bg=None):
    """Draw ``string`` with its top-left at ``xy`` and return a new image.

    ``size`` is the font size in points; ``bg`` (optional) draws a padded
    background box behind the text for legibility over busy screenshots.
    """
    img = _as_image(img)
    if string is None:
        string = ""
    work, mode = _work(img)
    draw = ImageDraw.Draw(work)
    font = _load_font(size)
    x, y = int(round(xy[0])), int(round(xy[1]))
    if bg is not None:
        try:
            l, t, r, b = draw.textbbox((x, y), str(string), font=font)
            pad = max(2, int(size * 0.2))
            draw.rectangle((l - pad, t - pad, r + pad, b + pad), fill=_color(bg))
        except Exception:
            pass
    draw.text((x, y), str(string), fill=_color(color), font=font)
    return _restore(work, mode)


def highlight(img, box, color="#ffeb3b", alpha=96):
    """Overlay a translucent colour rectangle (marker-pen highlight).

    ``alpha`` is 0-255 (default ~38% opacity).  Returns a new image.
    """
    img = _as_image(img)
    box = _clamp_box(_norm_box(box), img.size)
    work, mode = _work(img)
    r, g, b = _color(color)[:3]
    a = max(0, min(255, int(alpha)))
    overlay = Image.new("RGBA", work.size, (0, 0, 0, 0))
    ImageDraw.Draw(overlay).rectangle(box, fill=(r, g, b, a))
    work = Image.alpha_composite(work, overlay)
    return _restore(work, mode)


def blur_region(img, box, radius=8):
    """Gaussian-blur just the pixels inside ``box`` (e.g. to redact).

    Returns a new image; the region is clamped to the image bounds.
    """
    img = _as_image(img)
    box = _clamp_box(_norm_box(box), img.size)
    x0, y0, x1, y1 = box
    if x1 <= x0 or y1 <= y0:
        raise ScreenKitError("Blur region has zero area.")
    work, mode = _work(img)
    region = work.crop(box)
    blurred = region.filter(ImageFilter.GaussianBlur(max(1, int(radius))))
    work.paste(blurred, box)
    return _restore(work, mode)


def crop(img, box):
    """Return a new image cropped to ``box`` (order-independent corners)."""
    img = _as_image(img)
    box = _clamp_box(_norm_box(box), img.size)
    x0, y0, x1, y1 = box
    if x1 <= x0 or y1 <= y0:
        raise ScreenKitError("Crop region has zero area.")
    return img.crop(box)


def numbered_step(img, xy, n, radius=18, color=DEFAULT_COLOR,
                  text_color="#ffffff"):
    """Draw a filled circle centred at ``xy`` with the number ``n`` inside.

    Handy for "step 1, step 2, ..." call-outs.  Returns a new image.
    """
    img = _as_image(img)
    work, mode = _work(img)
    draw = ImageDraw.Draw(work)
    cx, cy = int(round(xy[0])), int(round(xy[1]))
    r = max(6, int(radius))
    draw.ellipse((cx - r, cy - r, cx + r, cy + r), fill=_color(color))
    label = str(n)
    font = _load_font(int(r * 1.2))
    try:
        l, t, rr, bb = draw.textbbox((0, 0), label, font=font)
        tw, th = rr - l, bb - t
        draw.text((cx - tw / 2 - l, cy - th / 2 - t), label,
                  fill=_color(text_color), font=font)
    except Exception:
        draw.text((cx - r / 2, cy - r / 2), label, fill=_color(text_color),
                  font=font)
    return _restore(work, mode)
