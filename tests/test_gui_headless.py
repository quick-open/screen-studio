"""The GUI must import cleanly and its main() must return 0 with no display."""

import os

from screenkit import gui


def test_import_has_no_side_effects():
    # merely importing must not require a display; the module exposes main()
    assert hasattr(gui, "main")
    assert callable(gui.main)


def test_asset_path_returns_none_for_missing():
    assert gui.asset_path("definitely-not-a-real-asset.xyz") is None


def test_main_headless_returns_zero(monkeypatch):
    # ensure no display is visible to tkinter, then main() should return 0
    monkeypatch.delenv("DISPLAY", raising=False)
    rc = gui.main()
    assert rc == 0
