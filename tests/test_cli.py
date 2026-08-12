"""Tests for the CLI (argument parsing + annotate op) — no display, no ffmpeg."""

import pytest
from PIL import Image

from screenkit import __main__ as cli
from screenkit.errors import ScreenKitError


def test_no_command_prints_help(capsys):
    assert cli.main([]) == 0
    out = capsys.readouterr().out
    assert "shot" in out and "record" in out and "annotate" in out


def test_shot_help_exits_zero():
    with pytest.raises(SystemExit) as ei:
        cli.main(["shot", "--help"])
    assert ei.value.code == 0


def test_annotate_op_on_temp_image(tmp_path):
    src = tmp_path / "in.png"
    Image.new("RGB", (60, 40), (220, 220, 220)).save(src)
    out = tmp_path / "out.png"
    rc = cli.main(["annotate", str(src), "rect", "--box", "5,5,40,20",
                   "--out", str(out)])
    assert rc == 0
    assert out.exists()
    assert Image.open(out).size == (60, 40)


def test_annotate_crop_changes_size(tmp_path):
    src = tmp_path / "in.png"
    Image.new("RGB", (100, 80), (10, 20, 30)).save(src)
    out = tmp_path / "c.png"
    rc = cli.main(["annotate", str(src), "crop", "--box", "10,10,50,40",
                   "--out", str(out)])
    assert rc == 0
    assert Image.open(out).size == (50, 40)


def test_annotate_arrow_points(tmp_path):
    src = tmp_path / "in.png"
    Image.new("RGB", (60, 60), (200, 200, 200)).save(src)
    out = tmp_path / "a.png"
    rc = cli.main(["annotate", str(src), "arrow", "--points", "5,5,50,50",
                   "--out", str(out)])
    assert rc == 0
    assert out.exists()


def test_annotate_missing_box_errors(tmp_path, capsys):
    src = tmp_path / "in.png"
    Image.new("RGB", (30, 30)).save(src)
    rc = cli.main(["annotate", str(src), "rect"])
    assert rc == 2
    assert "error" in capsys.readouterr().err.lower()


def test_record_without_duration_errors(monkeypatch, capsys):
    # even with a fake ffmpeg present, the CLI insists on --duration
    from screenkit import ffmpeg as ff
    monkeypatch.setattr(ff, "find_ffmpeg", lambda: "/usr/bin/ffmpeg")
    rc = cli.main(["record", "--out", "x.mp4"])
    assert rc == 2
    assert "duration" in capsys.readouterr().err.lower()


def test_shot_headless_clean_error(monkeypatch, capsys):
    from screenkit import capture as cap

    def boom():
        raise ScreenKitError("no display here")
    monkeypatch.setattr(cap, "capture_full", boom)
    rc = cli.main(["shot", "--full"])
    assert rc == 2
    assert "error" in capsys.readouterr().err.lower()


def test_region_parse_validation(tmp_path, capsys):
    src = tmp_path / "in.png"
    Image.new("RGB", (30, 30)).save(src)
    rc = cli.main(["annotate", str(src), "rect", "--box", "1,2,3"])
    assert rc == 2


def test_version_flag():
    with pytest.raises(SystemExit) as ei:
        cli.main(["--version"])
    assert ei.value.code == 0
