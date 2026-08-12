"""Tests for the pure-PIL annotation ops (no display, no ffmpeg)."""

import pytest
from PIL import Image

from screenkit import annotate as ann
from screenkit.errors import ScreenKitError


def base_image(w=80, h=60, color=(200, 200, 200)):
    return Image.new("RGB", (w, h), color)


def pixels_changed(before, after, box=None):
    """Count differing pixels (optionally only inside a box)."""
    b = before.convert("RGB")
    a = after.convert("RGB")
    if box:
        b = b.crop(box)
        a = a.crop(box)
    return sum(1 for pb, pa in zip(b.getdata(), a.getdata()) if pb != pa)


# ---- size preservation + change detection ---------------------------------
def test_rect_keeps_size_and_draws():
    img = base_image()
    out = ann.rect(img, (10, 10, 60, 40), color="#ff0000", width=3)
    assert isinstance(out, Image.Image)
    assert out.size == img.size
    assert pixels_changed(img, out) > 0


def test_ellipse_changes_pixels():
    img = base_image()
    out = ann.ellipse(img, (5, 5, 70, 50), width=3)
    assert out.size == img.size
    assert pixels_changed(img, out) > 0


def test_line_changes_pixels():
    img = base_image()
    out = ann.line(img, (0, 0), (79, 59), width=3)
    assert out.size == img.size
    assert pixels_changed(img, out) > 0


def test_arrow_draws_head_and_shaft():
    img = base_image()
    out = ann.arrow(img, (10, 30), (70, 30), color="#0000ff", width=4)
    assert out.size == img.size
    # arrowhead adds pixels near the tip
    assert pixels_changed(img, out, box=(60, 20, 80, 40)) > 0


def test_text_draws_and_keeps_size():
    img = base_image()
    out = ann.text(img, (5, 5), "Hi", size=24, color="#000000")
    assert out.size == img.size
    assert pixels_changed(img, out) > 0


def test_text_with_background_box():
    img = base_image()
    out = ann.text(img, (5, 5), "Hi", size=20, color="#ffffff", bg="#000000")
    assert pixels_changed(img, out) > 0


def test_highlight_is_translucent_and_scoped():
    img = base_image(color=(255, 255, 255))
    box = (20, 20, 60, 40)
    out = ann.highlight(img, box, color="#ffff00", alpha=120)
    assert out.size == img.size
    # inside changed, a corner well outside is untouched
    assert pixels_changed(img, out, box=box) > 0
    assert pixels_changed(img, out, box=(0, 0, 5, 5)) == 0


def test_numbered_step():
    img = base_image()
    out = ann.numbered_step(img, (40, 30), 3, radius=15)
    assert out.size == img.size
    assert pixels_changed(img, out, box=(25, 15, 55, 45)) > 0


# ---- blur -----------------------------------------------------------------
def test_blur_region_changes_only_region():
    # a sharp black/white split so a blur clearly alters the boundary
    img = Image.new("RGB", (80, 60), (255, 255, 255))
    for y in range(60):
        for x in range(40, 80):
            img.putpixel((x, y), (0, 0, 0))
    box = (30, 10, 50, 50)
    out = ann.blur_region(img, box, radius=6)
    assert out.size == img.size
    assert pixels_changed(img, out, box=box) > 0
    # far-left, entirely inside the white field, is unchanged
    assert pixels_changed(img, out, box=(0, 0, 10, 60)) == 0


def test_blur_zero_area_raises():
    img = base_image()
    with pytest.raises(ScreenKitError):
        ann.blur_region(img, (10, 10, 10, 10))


# ---- crop -----------------------------------------------------------------
def test_crop_returns_expected_size():
    img = base_image(100, 80)
    out = ann.crop(img, (10, 20, 60, 50))
    assert out.size == (50, 30)


def test_crop_orders_corners():
    img = base_image(100, 80)
    out = ann.crop(img, (60, 50, 10, 20))  # reversed corners
    assert out.size == (50, 30)


def test_crop_clamped_to_bounds():
    img = base_image(40, 40)
    out = ann.crop(img, (-10, -10, 30, 30))
    assert out.size == (30, 30)


def test_crop_zero_area_raises():
    img = base_image()
    with pytest.raises(ScreenKitError):
        ann.crop(img, (5, 5, 5, 40))


# ---- input validation -----------------------------------------------------
def test_non_image_raises():
    with pytest.raises(ScreenKitError):
        ann.rect("not an image", (0, 0, 1, 1))


def test_bad_color_raises():
    img = base_image()
    with pytest.raises(ScreenKitError):
        ann.rect(img, (0, 0, 10, 10), color="not-a-color")


def test_color_tuple_accepted():
    img = base_image()
    out = ann.rect(img, (2, 2, 20, 20), color=(255, 0, 0), width=2)
    assert pixels_changed(img, out) > 0


def test_rgba_image_preserved_as_rgba():
    img = Image.new("RGBA", (40, 40), (10, 10, 10, 255))
    out = ann.rect(img, (2, 2, 20, 20), color=(255, 0, 0, 255))
    assert out.mode == "RGBA"
    assert out.size == img.size
