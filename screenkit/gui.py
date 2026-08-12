#!/usr/bin/env python3
r"""Screen Studio -- a pure-stdlib tkinter GUI on top of the ``screenkit`` API.

A single main window: a left sidebar of sections (Capture, Editor, Record,
Recent) and a main panel that swaps to the selected section.  Every operation
calls the tested core library (capture / annotate / ffmpeg) and long-running
work (ffmpeg recording) runs on a background thread so the UI stays responsive;
results are reported in a clear inline bar -- a path plus "Open folder" on
success, or the :class:`ScreenKitError` message (never a traceback) on failure.

Design goals baked in here (mirrors the QuickOpen house style):
  * pure standard-library tkinter/ttk -- NO third-party GUI deps.  Dark mode is
    a ttk-style + palette swap.  Region selection is a fullscreen translucent
    overlay you drag a rectangle on.
  * Importing this module does nothing.  Only :func:`main` builds a root window,
    and it degrades gracefully (prints a message, returns 0) with no display.
  * Frozen-exe safe: bundled assets (icon, ffmpeg) are resolved via
    ``sys._MEIPASS`` / the exe directory when ``sys.frozen`` is set.
  * If ffmpeg is missing, a banner says so but capture + annotation still work.

100% AI-built, open source, published on QuickOpen (quickopen.ai).
"""

from __future__ import annotations

import os
import sys
import threading

# tkinter is imported lazily inside main()/build_app so importing this module
# never fails on a headless CI box.

APP_NAME = "Screen Studio"
APP_VERSION = "1.0.0"
WINDOW_TITLE = "Screen Studio — by QuickOpen (quickopen.ai)"
PROJECT_URL = "https://quickopen.ai/projects/screen-studio"

IMAGE_TYPES = [
    ("Images", "*.png *.jpg *.jpeg *.bmp *.tif *.tiff *.gif *.webp"),
    ("All files", "*.*"),
]

# (section_id, label) -- id maps to a _panel_<id> method.
SECTIONS = [
    ("capture", "Capture"),
    ("editor", "Editor"),
    ("record", "Record"),
    ("recent", "Recent"),
]

SECTION_DESCRIPTIONS = {
    "capture": "Grab the full screen, a monitor, or a dragged region. "
               "The shot opens in the Editor.",
    "editor": "Annotate the captured image — arrow, box, ellipse, line, text, "
              "highlight, blur, crop and numbered steps — then Save or Copy.",
    "record": "Record the screen (full or a region) to MP4 or GIF with a chosen "
              "frame-rate. Live recording needs ffmpeg and runs on Windows.",
    "recent": "Files you have saved in this app.",
}

PALETTES = {
    "light": {
        "bg": "#f5f7fa", "surface": "#ffffff", "text": "#141820",
        "muted": "#5b6472", "primary": "#2f5fe0", "primary_hi": "#2450c8",
        "entry": "#ffffff", "border": "#d5dae2", "sel": "#2f5fe0",
        "sel_fg": "#ffffff", "trough": "#e2e7ef", "ok": "#1f7a3d",
        "err": "#c0392b", "warn": "#b06b00", "canvas": "#e9edf3",
    },
    "dark": {
        "bg": "#0f1115", "surface": "#1a1e24", "text": "#f1f3f7",
        "muted": "#9aa4b2", "primary": "#5b86f7", "primary_hi": "#7098ff",
        "entry": "#1a1e24", "border": "#2a2f38", "sel": "#5b86f7",
        "sel_fg": "#0f1115", "trough": "#2a2f38", "ok": "#5bd68a",
        "err": "#ff6b5e", "warn": "#f0b64a", "canvas": "#12151a",
    },
}


# ---------------------------------------------------------------------------
# Asset / frozen handling
# ---------------------------------------------------------------------------
def asset_path(name):
    """Locate a bundled asset from source OR a PyInstaller one-file build."""
    roots = []
    if getattr(sys, "frozen", False):
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            roots.append(meipass)
        roots.append(os.path.dirname(os.path.abspath(sys.executable)))
    else:
        here = os.path.dirname(os.path.abspath(__file__))
        roots += [here, os.path.dirname(here), os.getcwd()]
    for root in roots:
        candidate = os.path.join(root, name)
        if os.path.exists(candidate):
            return candidate
    return None


def open_in_file_manager(path):
    """Best-effort 'reveal in file manager', guarded on every platform."""
    try:
        folder = path if os.path.isdir(path) else os.path.dirname(os.path.abspath(path))
        if hasattr(os, "startfile"):
            os.startfile(folder)  # noqa: S606 - intended
        elif sys.platform == "darwin":
            import subprocess
            subprocess.Popen(["open", folder])
        else:
            import subprocess
            subprocess.Popen(["xdg-open", folder])
        return True
    except Exception:
        return False


def open_with_default_app(path):
    """Open a file/URL with the OS default application, guarded."""
    try:
        if hasattr(os, "startfile"):
            os.startfile(path)  # noqa: S606
        elif sys.platform == "darwin":
            import subprocess
            subprocess.Popen(["open", path])
        else:
            import subprocess
            subprocess.Popen(["xdg-open", path])
        return True
    except Exception:
        return False


# ---------------------------------------------------------------------------
# The app (built lazily; tkinter imported only inside build_app/main)
# ---------------------------------------------------------------------------
def build_app():
    """Construct and return the App class bound to a live tkinter import."""
    import tkinter as tk
    from tkinter import ttk, filedialog

    from PIL import Image, ImageTk

    from . import annotate as ann
    from . import capture as cap
    from . import ffmpeg as ff
    from . import guiconfig
    from .errors import ScreenKitError

    FONT = "Segoe UI"

    # editor tools: (id, label)
    EDITOR_TOOLS = [
        ("arrow", "➤ Arrow"), ("rect", "▭ Box"), ("ellipse", "◯ Ellipse"),
        ("line", "／ Line"), ("text", "T Text"), ("highlight", "▤ Highlight"),
        ("blur", "░ Blur"), ("crop", "⌗ Crop"), ("number", "① Number"),
    ]

    class App(tk.Tk):
        def __init__(self):
            super().__init__()
            self.title(WINDOW_TITLE)
            self.geometry("1120x720")
            self.minsize(920, 600)

            self.theme = guiconfig.get_theme()
            self._busy = False
            self._panels = {}
            self._current = None
            self._tracked = []
            self._img_refs = []
            self._history = []
            self._last_output_dir = None

            # editor state
            self._edit_image = None       # base PIL image (annotated in place)
            self._edit_undo = []          # stack of prior PIL images
            self._canvas_photo = None
            self._canvas_scale = 1.0
            self._drag_start = None

            # record state
            self._recorder = None
            self._rec_region = None
            self._rec_timer = None

            self._ffmpeg = ff.find_ffmpeg()

            self._set_icon()
            self._build_menu()
            self._build_layout()
            self._apply_theme()
            self.protocol("WM_DELETE_WINDOW", self._on_close)
            self.after(50, self._select_first)

        # ---- assets / icon
        def _set_icon(self):
            try:
                ico = asset_path("screen-studio.ico")
                if ico:
                    self.iconbitmap(ico)
                    return
            except Exception:
                pass
            try:
                png = asset_path("screen-studio.png")
                if png:
                    img = tk.PhotoImage(file=png)
                    self._img_refs.append(img)
                    self.iconphoto(True, img)
            except Exception:
                pass  # icon is cosmetic

        # ---- theming
        def track(self, widget, role):
            self._tracked.append((widget, role))

        def _pal(self):
            return PALETTES[self.theme]

        def _apply_theme(self):
            p = self._pal()
            style = ttk.Style(self)
            try:
                style.theme_use("clam")
            except Exception:
                pass
            self.configure(bg=p["bg"])
            style.configure(".", background=p["bg"], foreground=p["text"],
                            fieldbackground=p["entry"], bordercolor=p["border"],
                            font=(FONT, 10))
            style.configure("TFrame", background=p["bg"])
            style.configure("Sidebar.TFrame", background=p["surface"])
            style.configure("Card.TFrame", background=p["surface"])
            style.configure("TLabel", background=p["bg"], foreground=p["text"])
            style.configure("Muted.TLabel", background=p["bg"], foreground=p["muted"])
            style.configure("Header.TLabel", background=p["bg"], foreground=p["text"],
                            font=(FONT, 15, "bold"))
            style.configure("Sub.TLabel", background=p["bg"], foreground=p["muted"],
                            font=(FONT, 10))
            style.configure("Brand.TLabel", background=p["surface"],
                            foreground=p["text"], font=(FONT, 12, "bold"))
            style.configure("Ok.TLabel", background=p["bg"], foreground=p["ok"])
            style.configure("Err.TLabel", background=p["bg"], foreground=p["err"])
            style.configure("Warn.TLabel", background=p["surface"], foreground=p["warn"])
            style.configure("Status.TLabel", background=p["surface"],
                            foreground=p["muted"])
            style.configure("Banner.TLabel", background=p["surface"],
                            foreground=p["warn"], font=(FONT, 10, "bold"))
            style.configure("TButton", background=p["surface"], foreground=p["text"],
                            bordercolor=p["border"], focuscolor=p["surface"],
                            padding=(10, 5))
            style.map("TButton",
                      background=[("active", p["trough"]), ("disabled", p["bg"])],
                      foreground=[("disabled", p["muted"])])
            style.configure("Accent.TButton", background=p["primary"],
                            foreground="#ffffff", padding=(12, 6))
            style.map("Accent.TButton",
                      background=[("active", p["primary_hi"]),
                                  ("disabled", p["border"])],
                      foreground=[("disabled", p["muted"])])
            style.configure("Tool.TButton", background=p["surface"],
                            foreground=p["text"], padding=(8, 6))
            style.map("Tool.TButton", background=[("active", p["trough"])])
            style.configure("ToolActive.TButton", background=p["primary"],
                            foreground="#ffffff", padding=(8, 6))
            style.configure("Toggle.TButton", background=p["surface"],
                            foreground=p["text"], padding=(8, 4))
            style.configure("Nav.TButton", background=p["surface"],
                            foreground=p["text"], padding=(12, 10), anchor="w",
                            font=(FONT, 11))
            style.map("Nav.TButton", background=[("active", p["trough"])])
            style.configure("NavActive.TButton", background=p["primary"],
                            foreground="#ffffff", padding=(12, 10), anchor="w",
                            font=(FONT, 11, "bold"))
            for name in ("TEntry", "TSpinbox"):
                style.configure(name, fieldbackground=p["entry"], foreground=p["text"],
                                insertcolor=p["text"], bordercolor=p["border"])
            style.configure("TCombobox", fieldbackground=p["entry"],
                            foreground=p["text"], background=p["surface"],
                            arrowcolor=p["text"])
            style.map("TCombobox", fieldbackground=[("readonly", p["entry"])],
                      foreground=[("readonly", p["text"])])
            style.configure("TRadiobutton", background=p["bg"], foreground=p["text"])
            style.map("TRadiobutton", background=[("active", p["bg"])])
            style.configure("TCheckbutton", background=p["bg"], foreground=p["text"])
            style.map("TCheckbutton", background=[("active", p["bg"])])
            style.configure("TLabelframe", background=p["bg"], foreground=p["text"],
                            bordercolor=p["border"])
            style.configure("TLabelframe.Label", background=p["bg"],
                            foreground=p["muted"])
            style.configure("TScale", background=p["bg"], troughcolor=p["trough"])
            style.configure("Horizontal.TScale", background=p["bg"],
                            troughcolor=p["trough"])
            style.configure("TScrollbar", background=p["surface"],
                            troughcolor=p["bg"], bordercolor=p["border"],
                            arrowcolor=p["text"])
            style.configure("TSeparator", background=p["border"])

            for widget, role in list(self._tracked):
                try:
                    if role == "listbox":
                        widget.configure(bg=p["surface"], fg=p["text"],
                                         selectbackground=p["primary"],
                                         selectforeground=p["sel_fg"],
                                         highlightthickness=1,
                                         highlightbackground=p["border"],
                                         borderwidth=0)
                    elif role == "canvas":
                        widget.configure(bg=p["canvas"], highlightthickness=1,
                                         highlightbackground=p["border"])
                except Exception:
                    pass
            # re-style active nav buttons + banner
            self._refresh_nav_styles()
            if hasattr(self, "_banner"):
                self._update_ffmpeg_banner()

        def toggle_theme(self):
            self.theme = "dark" if self.theme == "light" else "light"
            guiconfig.set_theme(self.theme)
            self._apply_theme()
            self._theme_btn.configure(
                text="☀ Light mode" if self.theme == "dark" else "🌙 Dark mode")
            self._redraw_canvas()

        # ---- menu
        def _build_menu(self):
            bar = tk.Menu(self)
            filem = tk.Menu(bar, tearoff=0)
            filem.add_command(label="Open image…", accelerator="Ctrl+O",
                              command=self._open_image)
            filem.add_command(label="Save annotated image…",
                              accelerator="Ctrl+S", command=self._save_edit)
            filem.add_separator()
            filem.add_command(label="Exit", command=self._on_close)
            bar.add_cascade(label="File", menu=filem)

            viewm = tk.Menu(bar, tearoff=0)
            viewm.add_command(label="Toggle dark mode", command=self.toggle_theme)
            bar.add_cascade(label="View", menu=viewm)

            helpm = tk.Menu(bar, tearoff=0)
            helpm.add_command(label="About", command=self._about)
            helpm.add_command(label="Project page (quickopen.ai)",
                              command=lambda: open_with_default_app(PROJECT_URL))
            bar.add_cascade(label="Help", menu=helpm)
            self.configure(menu=bar)
            self.bind_all("<Control-o>", lambda e: self._open_image())
            self.bind_all("<Control-s>", lambda e: self._save_edit())

        # ---- layout
        def _build_layout(self):
            top = ttk.Frame(self, style="Sidebar.TFrame", padding=(12, 8))
            top.pack(fill="x", side="top")
            ttk.Label(top, text="Screen Studio", style="Brand.TLabel").pack(side="left")
            ttk.Label(top, style="Status.TLabel",
                      text="  offline · open source · by QuickOpen").pack(side="left")
            self._theme_btn = ttk.Button(
                top, style="Toggle.TButton", command=self.toggle_theme,
                text="☀ Light mode" if self.theme == "dark" else "🌙 Dark mode")
            self._theme_btn.pack(side="right")

            # ffmpeg-missing banner (only shown when ffmpeg not found)
            self._banner = ttk.Label(self, style="Banner.TLabel",
                                     anchor="w", padding=(12, 4))

            body = ttk.Frame(self, style="TFrame")
            body.pack(fill="both", expand=True)

            side = ttk.Frame(body, style="Sidebar.TFrame", width=190)
            side.pack(side="left", fill="y")
            side.pack_propagate(False)
            self._nav_btns = {}
            for sid, label in SECTIONS:
                b = ttk.Button(side, text="  " + label, style="Nav.TButton",
                               command=lambda s=sid: self._show_section(s))
                b.pack(fill="x", padx=6, pady=(6, 0))
                self._nav_btns[sid] = b

            main = ttk.Frame(body, style="TFrame", padding=(16, 12))
            main.pack(side="left", fill="both", expand=True)

            head = ttk.Frame(main, style="TFrame")
            head.pack(fill="x")
            self.title_lbl = ttk.Label(head, text="Capture", style="Header.TLabel")
            self.title_lbl.pack(anchor="w")
            self.desc_lbl = ttk.Label(head, text="", style="Sub.TLabel",
                                      wraplength=760, justify="left")
            self.desc_lbl.pack(anchor="w", pady=(2, 8))
            ttk.Separator(main).pack(fill="x")

            self.container = ttk.Frame(main, style="TFrame")
            self.container.pack(fill="both", expand=True, pady=(10, 8))

            bar = ttk.Frame(self, style="Sidebar.TFrame", padding=(12, 6))
            bar.pack(fill="x", side="bottom")
            self.status_lbl = ttk.Label(bar, text="Ready", style="Status.TLabel",
                                        width=14, anchor="w")
            self.status_lbl.pack(side="left")
            self.openfolder_btn = ttk.Button(bar, text="Open folder",
                                             command=self._open_last_folder)
            self.result_lbl = ttk.Label(bar, text="", style="Status.TLabel",
                                        anchor="w", wraplength=720, justify="left")
            self.result_lbl.pack(side="left", fill="x", expand=True, padx=8)

            self._update_ffmpeg_banner()

        def _update_ffmpeg_banner(self):
            self._ffmpeg = ff.find_ffmpeg()
            if self._ffmpeg:
                self._banner.pack_forget()
            else:
                self._banner.configure(
                    text="⚠ FFmpeg not found — screen recording is disabled. "
                         "Capture and annotation still work. Put ffmpeg beside "
                         "the app or on your PATH to enable recording.")
                self._banner.pack(fill="x", side="top", after=self.children_top())

        def children_top(self):
            # the top brand bar is the first packed child; banner sits under it
            return self.pack_slaves()[0]

        def _refresh_nav_styles(self):
            if not hasattr(self, "_nav_btns"):
                return
            for sid, btn in self._nav_btns.items():
                active = (self._current == sid)
                btn.configure(style="NavActive.TButton" if active else "Nav.TButton")

        def _select_first(self):
            self._show_section("capture")

        def _show_section(self, sid):
            if self._current is not None and self._current in self._panels:
                self._panels[self._current].pack_forget()
            panel = self._panels.get(sid)
            if panel is None:
                panel = ttk.Frame(self.container, style="TFrame")
                builder = getattr(self, "_panel_" + sid, None)
                if builder:
                    builder(panel)
                else:
                    ttk.Label(panel, text="Not implemented.").pack()
                self._panels[sid] = panel
                self._apply_theme()
            panel.pack(fill="both", expand=True)
            self._current = sid
            self.title_lbl.configure(
                text=dict(SECTIONS).get(sid, sid))
            self.desc_lbl.configure(text=SECTION_DESCRIPTIONS.get(sid, ""))
            self._refresh_nav_styles()
            self._clear_result()
            if sid == "recent":
                self._reload_recent()

        # ---- background op runner
        def _bg(self, work, on_ok, button=None, busy="Working…"):
            if self._busy:
                self._set_status("busy")
                self._show_error("Please wait — an operation is already running.")
                return
            self._busy = True
            if button is not None:
                try:
                    button.state(["disabled"])
                except Exception:
                    pass
            self._set_status(busy, kind="working")
            self._clear_result(keep_status=True)

            def run():
                try:
                    res, err = work(), None
                except ScreenKitError as ex:
                    res, err = None, str(ex)
                except Exception as ex:
                    res, err = None, f"Unexpected error: {ex}"
                self.after(0, lambda: finish(res, err))

            def finish(res, err):
                self._busy = False
                if button is not None:
                    try:
                        button.state(["!disabled"])
                    except Exception:
                        pass
                if err is not None:
                    self._set_status("error", kind="err")
                    self._show_error(err)
                    return
                self._set_status("done", kind="ok")
                try:
                    on_ok(res)
                except Exception as ex:
                    self._show_error(f"Post-processing error: {ex}")

            threading.Thread(target=run, daemon=True).start()

        # ---- result bar helpers
        def _set_status(self, text, kind="idle"):
            p = self._pal()
            color = {"working": p["primary"], "ok": p["ok"], "err": p["err"]}.get(
                kind, p["muted"])
            self.status_lbl.configure(text=text, foreground=color)

        def _clear_result(self, keep_status=False):
            self.result_lbl.configure(text="")
            self.openfolder_btn.pack_forget()
            if not keep_status:
                self._set_status("Ready")

        def _show_error(self, message):
            self.result_lbl.configure(text="✕ " + message,
                                      foreground=self._pal()["err"])
            self.openfolder_btn.pack_forget()

        def report_success(self, message, outputs=None):
            outputs = outputs or []
            for o in outputs:
                if o and not os.path.isdir(o):
                    self._history.append(o)
                    guiconfig.add_recent(o)
            if outputs:
                first = outputs[0]
                self._last_output_dir = (
                    first if os.path.isdir(first)
                    else os.path.dirname(os.path.abspath(first)))
                self.openfolder_btn.pack(side="right")
            self.result_lbl.configure(text="✓ " + message,
                                      foreground=self._pal()["ok"])
            self._set_status("done", kind="ok")

        def _open_last_folder(self):
            if self._last_output_dir:
                open_in_file_manager(self._last_output_dir)

        # =====================================================================
        # CAPTURE
        # =====================================================================
        def _panel_capture(self, parent):
            card = ttk.Frame(parent, style="TFrame")
            card.pack(fill="x")
            ttk.Label(card, text="Take a screenshot:", style="TLabel").pack(anchor="w")

            btns = ttk.Frame(parent, style="TFrame")
            btns.pack(fill="x", pady=10)
            ttk.Button(btns, text="🖥  Full screen", style="Accent.TButton",
                       command=self._capture_full).pack(side="left")
            ttk.Button(btns, text="🗔  Window (best-effort)",
                       command=self._capture_window).pack(side="left", padx=6)
            ttk.Button(btns, text="▭  Region…",
                       command=self._capture_region).pack(side="left")

            mon = ttk.Frame(parent, style="TFrame")
            mon.pack(fill="x", pady=(4, 8))
            ttk.Label(mon, text="Monitor", width=10, anchor="w").pack(side="left")
            self._mon_var = tk.StringVar(value="all")
            self._mon_combo = ttk.Combobox(mon, textvariable=self._mon_var,
                                           state="readonly", width=28,
                                           values=["all"])
            self._mon_combo.pack(side="left")
            ttk.Button(mon, text="Capture monitor",
                       command=self._capture_monitor).pack(side="left", padx=6)
            ttk.Button(mon, text="Refresh",
                       command=self._reload_monitors).pack(side="left")

            ttk.Label(parent, style="Muted.TLabel", wraplength=740, justify="left",
                      text="Region opens a translucent full-screen overlay — drag "
                           "a rectangle to grab it. Every capture opens in the "
                           "Editor for annotation.").pack(anchor="w", pady=(6, 0))
            self.after(80, self._reload_monitors)

        def _reload_monitors(self):
            try:
                mons = cap.list_monitors()
            except ScreenKitError:
                self._mon_combo.configure(values=["all"])
                return
            vals = ["all"]
            for m in mons:
                if m["index"] == 0:
                    continue
                vals.append(f"{m['index']}: {m['width']}×{m['height']} "
                            f"@({m['left']},{m['top']})")
            self._mon_combo.configure(values=vals)

        def _capture_full(self):
            self._bg(cap.capture_full,
                     lambda img: self._open_in_editor(img, "Full screen captured."))

        def _capture_monitor(self):
            val = self._mon_var.get()
            idx = 0 if val == "all" else int(val.split(":")[0])
            self._bg(lambda: cap.capture_monitor(idx),
                     lambda img: self._open_in_editor(
                         img, f"Monitor {idx} captured."))

        def _capture_window(self):
            # No portable window-geometry API without extra deps; on Windows we
            # best-effort the foreground window rect, else fall back to full.
            def work():
                rect = _foreground_window_rect()
                if rect:
                    x, y, w, h = rect
                    return cap.capture_region(x, y, w, h)
                return cap.capture_full()
            self._bg(work, lambda img: self._open_in_editor(
                img, "Window captured (best-effort)."))

        def _capture_region(self):
            try:
                RegionOverlay(self, on_done=self._region_selected)
            except ScreenKitError as ex:
                self._show_error(str(ex))
            except Exception as ex:
                self._show_error(f"Could not open the region overlay: {ex}")

        def _region_selected(self, region):
            if not region:
                self._set_status("Ready")
                return
            x, y, w, h = region
            if w < 2 or h < 2:
                self._show_error("Region too small — drag a larger rectangle.")
                return
            self._bg(lambda: cap.capture_region(x, y, w, h),
                     lambda img: self._open_in_editor(
                         img, f"Region {w}×{h} captured."))

        # =====================================================================
        # EDITOR
        # =====================================================================
        def _panel_editor(self, parent):
            toolbar = ttk.Frame(parent, style="TFrame")
            toolbar.pack(fill="x")
            self._tool_var = tk.StringVar(value="arrow")
            self._tool_btns = {}
            for tid, label in EDITOR_TOOLS:
                b = ttk.Button(toolbar, text=label, style="Tool.TButton",
                               command=lambda t=tid: self._set_tool(t))
                b.pack(side="left", padx=(0, 4), pady=2)
                self._tool_btns[tid] = b

            opts = ttk.Frame(parent, style="TFrame")
            opts.pack(fill="x", pady=(6, 4))
            ttk.Label(opts, text="Colour", width=7, anchor="w").pack(side="left")
            self._color_var = tk.StringVar(value="#ff3b30")
            self._color_combo = ttk.Combobox(
                opts, textvariable=self._color_var, width=10, values=[
                    "#ff3b30", "#ffcc00", "#34c759", "#0a84ff", "#af52de",
                    "#ffffff", "#000000"])
            self._color_combo.pack(side="left")
            ttk.Label(opts, text="Width").pack(side="left", padx=(12, 4))
            self._width_var = tk.StringVar(value="4")
            ttk.Spinbox(opts, from_=1, to=40, width=4,
                        textvariable=self._width_var).pack(side="left")
            ttk.Label(opts, text="Text").pack(side="left", padx=(12, 4))
            self._text_var = tk.StringVar(value="Label")
            ttk.Entry(opts, textvariable=self._text_var, width=16).pack(side="left")
            ttk.Label(opts, text="Size").pack(side="left", padx=(12, 4))
            self._tsize_var = tk.StringVar(value="24")
            ttk.Spinbox(opts, from_=8, to=200, width=4,
                        textvariable=self._tsize_var).pack(side="left")
            ttk.Label(opts, text="Step #").pack(side="left", padx=(12, 4))
            self._num_var = tk.StringVar(value="1")
            ttk.Spinbox(opts, from_=1, to=999, width=4,
                        textvariable=self._num_var).pack(side="left")

            actions = ttk.Frame(parent, style="TFrame")
            actions.pack(fill="x", pady=(2, 6))
            ttk.Button(actions, text="↶ Undo", command=self._undo).pack(side="left")
            ttk.Button(actions, text="Open image…",
                       command=self._open_image).pack(side="left", padx=6)
            ttk.Button(actions, text="💾 Save…", style="Accent.TButton",
                       command=self._save_edit).pack(side="left")
            ttk.Button(actions, text="⧉ Copy",
                       command=self._copy_edit).pack(side="left", padx=6)

            body = ttk.Frame(parent, style="TFrame")
            body.pack(fill="both", expand=True)
            self._canvas = tk.Canvas(body, highlightthickness=1, bd=0)
            self._canvas.pack(fill="both", expand=True)
            self.track(self._canvas, "canvas")
            self._canvas.bind("<Button-1>", self._on_canvas_press)
            self._canvas.bind("<B1-Motion>", self._on_canvas_drag)
            self._canvas.bind("<ButtonRelease-1>", self._on_canvas_release)
            self._canvas.bind("<Configure>", lambda e: self._redraw_canvas())
            self._set_tool("arrow")

        def _set_tool(self, tid):
            self._tool_var.set(tid)
            for t, b in getattr(self, "_tool_btns", {}).items():
                b.configure(style="ToolActive.TButton" if t == tid
                            else "Tool.TButton")

        def _open_in_editor(self, img, message):
            self._edit_image = img.convert("RGB")
            self._edit_undo = []
            self.report_success(message)
            self._show_section("editor")
            self._redraw_canvas()

        def _redraw_canvas(self):
            canvas = getattr(self, "_canvas", None)
            if canvas is None:
                return
            canvas.delete("all")
            if self._edit_image is None:
                canvas.create_text(
                    canvas.winfo_width() // 2 or 200,
                    canvas.winfo_height() // 2 or 120,
                    text="No image yet — capture from the Capture tab\n"
                         "or open an image (Ctrl+O).",
                    fill=self._pal()["muted"], justify="center", font=(FONT, 11))
                return
            cw = max(50, canvas.winfo_width())
            ch = max(50, canvas.winfo_height())
            iw, ih = self._edit_image.size
            scale = min(cw / iw, ch / ih, 1.0)
            self._canvas_scale = scale
            disp = self._edit_image
            if scale < 1.0:
                disp = self._edit_image.resize(
                    (max(1, int(iw * scale)), max(1, int(ih * scale))),
                    Image.LANCZOS)
            self._canvas_photo = ImageTk.PhotoImage(disp)
            self._canvas_ox = (cw - disp.size[0]) // 2
            self._canvas_oy = (ch - disp.size[1]) // 2
            canvas.create_image(self._canvas_ox, self._canvas_oy, anchor="nw",
                                image=self._canvas_photo)

        def _canvas_to_image(self, cx, cy):
            """Map canvas coords to image-pixel coords."""
            ox = getattr(self, "_canvas_ox", 0)
            oy = getattr(self, "_canvas_oy", 0)
            s = self._canvas_scale or 1.0
            return (cx - ox) / s, (cy - oy) / s

        def _on_canvas_press(self, e):
            if self._edit_image is None:
                return
            self._drag_start = (e.x, e.y)
            self._drag_rect_id = None

        def _on_canvas_drag(self, e):
            if self._edit_image is None or self._drag_start is None:
                return
            x0, y0 = self._drag_start
            if getattr(self, "_drag_rect_id", None):
                self._canvas.delete(self._drag_rect_id)
            tool = self._tool_var.get()
            col = self._color_var.get() or "#ff3b30"
            if tool in ("arrow", "line"):
                self._drag_rect_id = self._canvas.create_line(
                    x0, y0, e.x, e.y, fill=col, width=2)
            elif tool == "ellipse":
                self._drag_rect_id = self._canvas.create_oval(
                    x0, y0, e.x, e.y, outline=col, width=2)
            else:
                self._drag_rect_id = self._canvas.create_rectangle(
                    x0, y0, e.x, e.y, outline=col, width=2, dash=(4, 2))

        def _on_canvas_release(self, e):
            if self._edit_image is None or self._drag_start is None:
                return
            if getattr(self, "_drag_rect_id", None):
                self._canvas.delete(self._drag_rect_id)
                self._drag_rect_id = None
            x0, y0 = self._drag_start
            self._drag_start = None
            p0 = self._canvas_to_image(x0, y0)
            p1 = self._canvas_to_image(e.x, e.y)
            self._apply_tool(p0, p1)

        def _apply_tool(self, p0, p1):
            tool = self._tool_var.get()
            col = self._color_var.get() or "#ff3b30"
            try:
                width = max(1, int(self._width_var.get()))
            except ValueError:
                width = 4
            box = (p0[0], p0[1], p1[0], p1[1])
            img = self._edit_image
            try:
                if tool == "arrow":
                    new = ann.arrow(img, p0, p1, color=col, width=width)
                elif tool == "line":
                    new = ann.line(img, p0, p1, color=col, width=width)
                elif tool == "rect":
                    new = ann.rect(img, box, color=col, width=width)
                elif tool == "ellipse":
                    new = ann.ellipse(img, box, color=col, width=width)
                elif tool == "highlight":
                    new = ann.highlight(img, box, color=col)
                elif tool == "blur":
                    new = ann.blur_region(img, box)
                elif tool == "crop":
                    new = ann.crop(img, box)
                elif tool == "text":
                    size = int(self._tsize_var.get() or 24)
                    new = ann.text(img, p0, self._text_var.get(), size=size,
                                   color=col)
                elif tool == "number":
                    n = int(self._num_var.get() or 1)
                    new = ann.numbered_step(img, p0, n, color=col)
                    self._num_var.set(str(n + 1))
                else:
                    return
            except ScreenKitError as ex:
                self._show_error(str(ex))
                return
            self._edit_undo.append(self._edit_image)
            self._edit_image = new
            self._redraw_canvas()
            self._set_status("edited", kind="ok")

        def _undo(self):
            if self._edit_undo:
                self._edit_image = self._edit_undo.pop()
                self._redraw_canvas()
                self._set_status("undone", kind="ok")

        def _open_image(self):
            path = filedialog.askopenfilename(title="Open image",
                                              filetypes=IMAGE_TYPES)
            if not path:
                return
            try:
                img = Image.open(path)
                img.load()
            except Exception as ex:
                self._show_error(f"Could not open image: {ex}")
                return
            self._open_in_editor(img, f"Opened {os.path.basename(path)}.")

        def _save_edit(self):
            if self._edit_image is None:
                self._show_error("Nothing to save yet — capture or open an image.")
                return
            path = filedialog.asksaveasfilename(
                title="Save image", defaultextension=".png",
                filetypes=[("PNG", "*.png"), ("JPEG", "*.jpg"),
                           ("All files", "*.*")])
            if not path:
                return
            img = self._edit_image
            self._bg(lambda: cap.save_image(img, path),
                     lambda p: self.report_success(
                         f"Saved {img.width}×{img.height} → {p}", [p]))

        def _copy_edit(self):
            if self._edit_image is None:
                self._show_error("Nothing to copy yet.")
                return
            ok = cap.to_clipboard(self._edit_image)
            if ok:
                self.report_success("Copied to clipboard.")
            else:
                self._show_error("Clipboard image copy is only available on "
                                 "Windows. Use Save instead.")

        # =====================================================================
        # RECORD
        # =====================================================================
        def _panel_record(self, parent):
            row = ttk.Frame(parent, style="TFrame")
            row.pack(fill="x", pady=(2, 6))
            ttk.Label(row, text="Area", width=8, anchor="w").pack(side="left")
            self._rec_area = tk.StringVar(value="full")
            ttk.Radiobutton(row, text="Full screen", value="full",
                            variable=self._rec_area).pack(side="left")
            ttk.Radiobutton(row, text="Region", value="region",
                            variable=self._rec_area).pack(side="left", padx=6)
            ttk.Button(row, text="Select region…",
                       command=self._rec_select_region).pack(side="left")
            self._rec_region_lbl = ttk.Label(row, style="Muted.TLabel", text="")
            self._rec_region_lbl.pack(side="left", padx=8)

            row2 = ttk.Frame(parent, style="TFrame")
            row2.pack(fill="x", pady=6)
            ttk.Label(row2, text="FPS", width=8, anchor="w").pack(side="left")
            self._fps_var = tk.StringVar(value="15")
            ttk.Spinbox(row2, from_=1, to=60, width=5,
                        textvariable=self._fps_var).pack(side="left")
            ttk.Label(row2, text="Format").pack(side="left", padx=(16, 4))
            self._fmt_var = tk.StringVar(value="MP4")
            ttk.Combobox(row2, textvariable=self._fmt_var, state="readonly",
                         width=6, values=["MP4", "GIF"]).pack(side="left")

            row3 = ttk.Frame(parent, style="TFrame")
            row3.pack(fill="x", pady=6)
            self._rec_start_btn = ttk.Button(
                row3, text="⏺ Start recording", style="Accent.TButton",
                command=self._rec_start)
            self._rec_start_btn.pack(side="left")
            self._rec_stop_btn = ttk.Button(row3, text="⏹ Stop",
                                            command=self._rec_stop)
            self._rec_stop_btn.pack(side="left", padx=6)
            self._rec_stop_btn.state(["disabled"])
            self._rec_elapsed = ttk.Label(row3, style="Header.TLabel", text="")
            self._rec_elapsed.pack(side="left", padx=12)

            note = ("Live recording shells out to FFmpeg (gdigrab on Windows). "
                    "Stop finalises the file. On non-Windows desktops recording "
                    "is best-effort (x11grab/avfoundation).")
            if not self._ffmpeg:
                note = ("FFmpeg was not found, so recording is disabled here. "
                        "Add ffmpeg to enable it. ") + note
            ttk.Label(parent, style="Muted.TLabel", wraplength=740,
                      justify="left", text=note).pack(anchor="w", pady=(8, 0))

        def _rec_select_region(self):
            try:
                RegionOverlay(self, on_done=self._rec_region_done)
            except Exception as ex:
                self._show_error(f"Could not open the region overlay: {ex}")

        def _rec_region_done(self, region):
            if region and region[2] >= 2 and region[3] >= 2:
                self._rec_region = region
                self._rec_area.set("region")
                self._rec_region_lbl.configure(
                    text=f"{region[2]}×{region[3]} @({region[0]},{region[1]})")

        def _rec_start(self):
            if not self._ffmpeg:
                self._show_error("FFmpeg not found — recording is unavailable.")
                return
            if self._recorder is not None:
                self._show_error("A recording is already in progress.")
                return
            gif = self._fmt_var.get() == "GIF"
            ext = ".gif" if gif else ".mp4"
            path = filedialog.asksaveasfilename(
                title="Save recording", defaultextension=ext,
                filetypes=[("GIF", "*.gif")] if gif else [("MP4 video", "*.mp4")])
            if not path:
                return
            region = None
            if self._rec_area.get() == "region":
                if not self._rec_region:
                    self._show_error("Select a region first.")
                    return
                region = self._rec_region
            try:
                fps = int(self._fps_var.get())
            except ValueError:
                self._show_error("FPS must be a whole number.")
                return
            rec = ff.Recorder(path, fps=fps, region=region, gif=gif)

            def do_start():
                rec.start()
                return rec
            self._set_status("starting…", kind="working")

            def started(_r):
                self._recorder = rec
                self._rec_start_btn.state(["disabled"])
                self._rec_stop_btn.state(["!disabled"])
                self._set_status("recording", kind="working")
                self._tick_elapsed()
            self._bg(do_start, started, button=self._rec_start_btn,
                     busy="starting…")

        def _tick_elapsed(self):
            if self._recorder is None:
                self._rec_elapsed.configure(text="")
                return
            secs = int(self._recorder.elapsed())
            self._rec_elapsed.configure(
                text=f"● {secs // 60:02d}:{secs % 60:02d}")
            self._rec_timer = self.after(500, self._tick_elapsed)

        def _rec_stop(self):
            rec = self._recorder
            if rec is None:
                return
            if self._rec_timer:
                self.after_cancel(self._rec_timer)
                self._rec_timer = None
            self._set_status("finalising…", kind="working")
            self._rec_stop_btn.state(["disabled"])

            def do_stop():
                return rec.stop()

            def stopped(path):
                self._recorder = None
                self._rec_start_btn.state(["!disabled"])
                self._rec_elapsed.configure(text="")
                self.report_success(f"Saved recording → {path}", [path])
            # run stop off-thread but keep _recorder set so tick stops cleanly
            self._recorder = None
            self._bg(do_stop, stopped)

        # =====================================================================
        # RECENT
        # =====================================================================
        def _panel_recent(self, parent):
            ttk.Label(parent, text="Recently saved files:",
                      style="TLabel").pack(anchor="w")
            box = ttk.Frame(parent, style="TFrame")
            box.pack(fill="both", expand=True, pady=6)
            self._recent_lb = tk.Listbox(box, activestyle="none")
            sb = ttk.Scrollbar(box, orient="vertical",
                               command=self._recent_lb.yview)
            self._recent_lb.configure(yscrollcommand=sb.set)
            sb.pack(side="right", fill="y")
            self._recent_lb.pack(side="left", fill="both", expand=True)
            self.track(self._recent_lb, "listbox")
            self._recent_lb.bind("<Double-Button-1>",
                                 lambda e: self._recent_open(False))

            btns = ttk.Frame(parent, style="TFrame")
            btns.pack(fill="x")
            ttk.Button(btns, text="Open",
                       command=lambda: self._recent_open(False)).pack(side="left")
            ttk.Button(btns, text="Open in editor",
                       command=self._recent_edit).pack(side="left", padx=6)
            ttk.Button(btns, text="Open folder",
                       command=lambda: self._recent_open(True)).pack(side="left")
            ttk.Button(btns, text="Clear list",
                       command=self._recent_clear).pack(side="left", padx=6)
            self._reload_recent()

        def _reload_recent(self):
            lb = getattr(self, "_recent_lb", None)
            if lb is None:
                return
            lb.delete(0, "end")
            items = guiconfig.get_recent()
            if not items:
                lb.insert("end", "(nothing saved yet)")
                return
            for p in items:
                lb.insert("end", p if os.path.exists(p) else p + "  (missing)")

        def _recent_selected(self):
            lb = self._recent_lb
            sel = lb.curselection()
            if not sel:
                return None
            path = lb.get(sel[0]).replace("  (missing)", "")
            return path if os.path.exists(path) else None

        def _recent_open(self, reveal):
            path = self._recent_selected()
            if not path:
                self._show_error("Select an existing file.")
                return
            (open_in_file_manager if reveal else open_with_default_app)(path)

        def _recent_edit(self):
            path = self._recent_selected()
            if not path:
                self._show_error("Select an existing image file.")
                return
            try:
                img = Image.open(path)
                img.load()
            except Exception as ex:
                self._show_error(f"Not an editable image: {ex}")
                return
            self._open_in_editor(img, f"Opened {os.path.basename(path)}.")

        def _recent_clear(self):
            guiconfig.clear_recent()
            self._reload_recent()

        # ---- About
        def _about(self):
            win = tk.Toplevel(self)
            win.title("About Screen Studio")
            win.configure(bg=self._pal()["bg"])
            win.resizable(False, False)
            frm = ttk.Frame(win, style="TFrame", padding=18)
            frm.pack(fill="both", expand=True)
            ttk.Label(frm, text="Screen Studio", style="Header.TLabel").pack(anchor="w")
            ttk.Label(frm, text=f"Version {APP_VERSION}",
                      style="Sub.TLabel").pack(anchor="w", pady=(0, 8))
            ttk.Label(frm, style="TLabel", justify="left", wraplength=380,
                      text="A fast, fully-offline screenshot, annotation and "
                           "screen-recording tool.\n\n100% AI-built, open source, "
                           "published on QuickOpen. Nothing is uploaded "
                           "anywhere.").pack(anchor="w")
            ttk.Label(frm, style="Sub.TLabel", justify="left", wraplength=380,
                      text="Apache-2.0. Built on mss and Pillow; screen "
                           "recording uses an external FFmpeg (LGPL/GPL).").pack(
                anchor="w", pady=(8, 4))
            link = ttk.Label(frm, text="Project page: quickopen.ai",
                             style="Ok.TLabel", cursor="hand2")
            link.pack(anchor="w", pady=(4, 10))
            link.bind("<Button-1>", lambda e: open_with_default_app(PROJECT_URL))
            ttk.Button(frm, text="Close", command=win.destroy).pack(anchor="e")
            win.transient(self)
            win.grab_set()

        # ---- shutdown
        def _on_close(self):
            try:
                if self._recorder is not None:
                    self._recorder.cancel()
            except Exception:
                pass
            self.destroy()

    # -----------------------------------------------------------------------
    # Fullscreen translucent region-selection overlay
    # -----------------------------------------------------------------------
    class RegionOverlay(tk.Toplevel):
        """A borderless, translucent, fullscreen window to drag a rectangle.

        Calls ``on_done((x, y, w, h))`` in SCREEN coordinates, or
        ``on_done(None)`` if cancelled (Esc / empty drag).
        """

        def __init__(self, master, on_done):
            super().__init__(master)
            self.on_done = on_done
            self._start = None
            self._rect = None
            self.overrideredirect(True)
            try:
                self.attributes("-fullscreen", True)
            except Exception:
                self.geometry(f"{self.winfo_screenwidth()}x"
                              f"{self.winfo_screenheight()}+0+0")
            try:
                self.attributes("-alpha", 0.28)
                self.attributes("-topmost", True)
            except Exception:
                pass
            self.configure(cursor="crosshair", bg="#101418")
            self._canvas = tk.Canvas(self, bg="#101418", highlightthickness=0,
                                     cursor="crosshair")
            self._canvas.pack(fill="both", expand=True)
            self._canvas.create_text(
                self.winfo_screenwidth() // 2, 40,
                text="Drag to select a region · Esc to cancel",
                fill="#ffffff", font=("Segoe UI", 14))
            self._canvas.bind("<Button-1>", self._press)
            self._canvas.bind("<B1-Motion>", self._drag)
            self._canvas.bind("<ButtonRelease-1>", self._release)
            self.bind("<Escape>", lambda e: self._finish(None))
            self.focus_force()
            self.grab_set()

        def _press(self, e):
            self._start = (e.x_root, e.y_root)
            if self._rect:
                self._canvas.delete(self._rect)

        def _drag(self, e):
            if not self._start:
                return
            if self._rect:
                self._canvas.delete(self._rect)
            x0 = self._start[0] - self.winfo_rootx()
            y0 = self._start[1] - self.winfo_rooty()
            x1 = e.x_root - self.winfo_rootx()
            y1 = e.y_root - self.winfo_rooty()
            self._rect = self._canvas.create_rectangle(
                x0, y0, x1, y1, outline="#5b86f7", width=2)

        def _release(self, e):
            if not self._start:
                self._finish(None)
                return
            x0, y0 = self._start
            x1, y1 = e.x_root, e.y_root
            x, y = min(x0, x1), min(y0, y1)
            w, h = abs(x1 - x0), abs(y1 - y0)
            self._finish((int(x), int(y), int(w), int(h)))

        def _finish(self, region):
            try:
                self.grab_release()
            except Exception:
                pass
            self.destroy()
            try:
                self.on_done(region)
            except Exception:
                pass

    # App methods reference ``RegionOverlay`` as a free variable and close over
    # this local class automatically (it is a local of ``build_app``), so no
    # extra wiring is needed here.
    App._RegionOverlay = RegionOverlay
    return App


def _foreground_window_rect():
    """Best-effort foreground-window rectangle on Windows; else ``None``."""
    if os.name != "nt":
        return None
    try:  # pragma: no cover - Windows only
        import ctypes
        from ctypes import wintypes
        user32 = ctypes.windll.user32
        hwnd = user32.GetForegroundWindow()
        rect = wintypes.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(rect))
        w = rect.right - rect.left
        h = rect.bottom - rect.top
        if w > 0 and h > 0:
            return (rect.left, rect.top, w, h)
    except Exception:
        return None
    return None


def main():
    """Entry point: build the root window and run.  Degrades on headless hosts.

    Importing this module does nothing; only this function creates a Tk root.
    With no display it prints a friendly note and returns 0 instead of raising.
    """
    try:
        import tkinter as tk
    except Exception as exc:
        print(f"{APP_NAME}: a graphical environment with tkinter is required "
              f"to run the GUI ({exc}).")
        return 0

    try:
        from PIL import ImageTk  # noqa: F401 - ensure the Tk image bridge exists
    except Exception as exc:
        print(f"{APP_NAME}: Pillow's ImageTk is required for the GUI ({exc}).")
        return 0

    try:
        App = build_app()
        app = App()
    except tk.TclError as exc:
        print(f"{APP_NAME}: no graphical display available — cannot start the "
              f"GUI here ({exc}). This app is intended for the desktop.")
        return 0
    except Exception as exc:
        print(f"{APP_NAME}: could not start the GUI ({exc}).")
        return 1

    app.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
