"""GUI tests for the 1.1.0 Aura layout-language rework.

The App tests need a display (run the suite under ``xvfb-run``) and are
skipped headless, mirroring the house pattern.  SCREENSTUDIO_HOME keeps the
config hermetic.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from screenkit import guiconfig  # noqa: E402


def test_theme_defaults_to_system(tmp_path, monkeypatch):
    monkeypatch.setenv("SCREENSTUDIO_HOME", str(tmp_path))
    assert guiconfig.get_theme() == "system"
    guiconfig.set_theme("dark")
    assert guiconfig.get_theme() == "dark"
    guiconfig.set_theme("system")
    assert guiconfig.get_theme() == "system"
    guiconfig.set_theme("bogus")
    assert guiconfig.get_theme() == "system"


def _display():
    return bool(os.environ.get("DISPLAY")) and os.name != "nt"


needs_display = pytest.mark.skipif(not _display(),
                                   reason="needs a display (xvfb-run)")


@pytest.fixture()
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("SCREENSTUDIO_HOME", str(tmp_path))
    guiconfig.set_theme("dark")     # deterministic; no OS follow in tests
    from screenkit import gui
    App = gui.build_app()
    a = App()
    a.update()
    yield a
    try:
        a._on_close()
    except Exception:
        pass


@needs_display
def test_sections_registered(app):
    assert set(app._sections) >= {"capture", "annotate", "record",
                                  "recent", "about"}


@needs_display
def test_annotate_empty_state_toggles(app):
    from PIL import Image
    app.show("annotate")
    app.update()
    app._redraw_canvas()
    assert app._empty_annotate.winfo_manager() == "place"
    app._edit_image = Image.new("RGB", (60, 40), "white")
    app._redraw_canvas()
    app.update()
    assert app._empty_annotate.winfo_manager() == ""
    # undo stack empty -> undo is a safe no-op
    app._undo()


@needs_display
def test_theme_flip_smoke(app):
    app.set_theme("light")
    app.update()
    app.set_theme("dark")
    app.update()
    assert app.theme == "dark"


@needs_display
def test_record_controls_exist(app):
    app.show("record")
    app.update()
    assert app._rec_start_btn is not None
    assert "disabled" in app._rec_stop_btn.state()
