#!/usr/bin/env python3
r"""Screen Studio -- an Aura (QuickOpen design system) GUI on top of ``screenkit``.

A single Aura window: a sidebar of sections (Capture, Annotate, Record,
Recent, About) and a swappable main panel.  Every operation calls the tested
core library (capture / annotate / ffmpeg) and long-running work (ffmpeg
recording) runs on a background thread so the UI stays responsive; results are
reported in the Aura status bar -- a path plus "Open folder" on success, or the
:class:`ScreenKitError` message (never a traceback) on failure.

Design goals baked in here (mirrors the QuickOpen house style):
  * built on the vendored ``screenkit/aura.py`` design system, which layers the
    quickopen.ai look (deep space + light) over CustomTkinter.  Runtime deps:
    ``customtkinter`` (+ ``darkdetect``) — declared in requirements.txt; the
    PyInstaller build adds ``--collect-all customtkinter``.
  * Importing this module does nothing.  Only :func:`main` builds a root
    window, and it degrades gracefully (prints a message, returns 0) with no
    display or with customtkinter missing.
  * Frozen-exe safe: bundled assets (icon, ffmpeg) are resolved via
    ``sys._MEIPASS`` / the exe directory when ``sys.frozen`` is set.
  * If ffmpeg is missing, the Record section says so but capture + annotation
    still work; on a headless box the panels still build with a friendly note.

100% AI-built, open source, published on QuickOpen (quickopen.ai).
"""

from __future__ import annotations

import os
import sys
import threading

# tkinter/customtkinter are imported lazily inside main()/build_app so merely
# importing this module (e.g. during packaging or on a headless CI box) never
# fails.

APP_NAME = "Screen Studio"
APP_VERSION = "1.1.0"
WINDOW_TITLE = "Screen Studio — by QuickOpen (quickopen.ai)"
PROJECT_URL = "https://quickopen.ai/projects/screen-studio"
ACCENT = "#5b86f7"      # publish/specs/screen-studio.json accent

IMAGE_TYPES = [
    ("Images", "*.png *.jpg *.jpeg *.bmp *.tif *.tiff *.gif *.webp"),
    ("All files", "*.*"),
]

SECTION_DESCRIPTIONS = {
    "capture": "Grab the full screen, a monitor, or a dragged region. "
               "The shot opens in Annotate.",
    "annotate": "Annotate the captured image — arrow, box, ellipse, line, "
                "text, highlight, blur, crop and numbered steps — then Save "
                "or Copy.",
    "record": "Record the screen (full or a region) to MP4 or GIF with a "
              "chosen frame-rate. Live recording needs ffmpeg and runs on "
              "Windows.",
    "recent": "Files you have saved in this app.",
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
# The app (built lazily; tkinter/customtkinter imported only inside build_app)
# ---------------------------------------------------------------------------
def build_app():
    """Construct and return the App class bound to live GUI imports.

    Kept inside a function so this module imports cleanly without a display
    (and without customtkinter installed).
    """
    import tkinter as tk
    from tkinter import filedialog, ttk
    import customtkinter as ctk

    from PIL import Image, ImageTk

    from . import annotate as ann
    from . import aura
    from . import capture as cap
    from . import ffmpeg as ff
    from . import guiconfig
    from .errors import ScreenKitError

    # annotate tools shown in the segmented control: (id, label)
    EDITOR_TOOLS = [
        ("arrow", "Arrow"), ("rect", "Box"), ("ellipse", "Ellipse"),
        ("line", "Line"), ("text", "Text"), ("highlight", "Highlight"),
        ("blur", "Blur"), ("crop", "Crop"), ("number", "Step"),
    ]
    TOOL_BY_LABEL = {label: tid for tid, label in EDITOR_TOOLS}
    LABEL_BY_TOOL = {tid: label for tid, label in EDITOR_TOOLS}
    DANGER = (aura.TOKENS["light"]["danger"], aura.TOKENS["dark"]["danger"])

    class App(aura.AuraApp):
        def __init__(self):
            super().__init__(
                title=WINDOW_TITLE, app_name=APP_NAME, accent=ACCENT,
                theme=guiconfig.get_theme(),
                icon_png=asset_path("screen-studio.png"), version=APP_VERSION,
                tagline="offline capture",
                on_theme_change=guiconfig.set_theme,
                size=(1120, 720), min_size=(920, 600))

            self._busy = False
            self._img_refs_gui = []       # NOT _img_refs (owned by AuraApp)
            self._history = []
            self._last_output_dir = None

            # annotate state
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

            # "Open folder" lives in the Aura status bar's action host and is
            # packed only when a success carries an output path.
            self._openfolder_btn = aura.AuraButton(
                self.statusbar.actions, "Open folder", kind="secondary",
                height=30, command=self._open_last_folder)

            self.add_section("capture", "Capture", "⊞", self._build_capture)
            self.add_section("annotate", "Annotate", "✎", self._build_annotate)
            self.add_section("record", "Record", "◉", self._build_record)
            self.add_section("recent", "Recent", "◷", self._build_recent)
            self.add_section("about", "About", "ℹ", self._build_about)
            self.show("capture")
            self.set_status("Ready")
            self.protocol("WM_DELETE_WINDOW", self._on_close)

        # ---- assets / icon
        def _set_icon(self):
            try:
                ico = asset_path("screen-studio.ico")
                if ico and os.name == "nt":
                    self.iconbitmap(ico)
                    return
            except Exception:
                pass
            try:
                png = asset_path("screen-studio.png")
                if png:
                    img = tk.PhotoImage(file=png)
                    self._img_refs_gui.append(img)
                    self.iconphoto(True, img)
            except Exception:
                pass  # icon is cosmetic

        # ---- theming: raw canvas text must be re-tinted on theme flip
        def set_theme(self, theme, _system=False):
            super().set_theme(theme, _system=_system)
            self._redraw_canvas()

        # ---- section switching (recent list refreshes on show)
        def show(self, sid):
            super().show(sid)
            if sid == "recent":
                self._reload_recent()

        # ---- menu (☰ dropdown) + keyboard baseline (§7/§9)
        def _build_menu(self):
            bar = tk.Menu(self)
            filem = tk.Menu(bar, tearoff=0)
            filem.add_command(label="New capture (region)…",
                              accelerator="Ctrl+N",
                              command=self._capture_region)
            filem.add_command(label="Open image…", accelerator="Ctrl+O",
                              command=self._open_image)
            filem.add_command(label="Save annotated image…",
                              accelerator="Ctrl+S", command=self._save_edit)
            filem.add_separator()
            filem.add_command(label="Settings…", accelerator="Ctrl+,",
                              command=self._open_settings)
            filem.add_command(label="Exit", command=self._on_close)
            bar.add_cascade(label="File", menu=filem)

            editm = tk.Menu(bar, tearoff=0)
            editm.add_command(label="Undo annotation", accelerator="Ctrl+Z",
                              command=self._undo)
            editm.add_command(label="Copy annotated image",
                              command=self._copy_edit)
            bar.add_cascade(label="Edit", menu=editm)

            viewm = tk.Menu(bar, tearoff=0)
            viewm.add_command(label="Toggle sidebar", accelerator="Ctrl+\\",
                              command=self.toggle_sidebar)
            viewm.add_command(
                label="Toggle dark mode",
                command=lambda: self.set_theme(
                    "light" if self.theme == "dark" else "dark"))
            bar.add_cascade(label="View", menu=viewm)

            helpm = tk.Menu(bar, tearoff=0)
            helpm.add_command(label="About", command=lambda: self.show("about"))
            helpm.add_command(label="Project page (quickopen.ai)",
                              command=lambda: open_with_default_app(PROJECT_URL))
            bar.add_cascade(label="Help", menu=helpm)
            self.configure(menu=bar)
            self.bind_all("<Control-n>", lambda e: self._capture_region())
            self.bind_all("<Control-o>", lambda e: self._open_image())
            self.bind_all("<Control-s>", lambda e: self._save_edit())
            self.bind_all("<Control-z>", lambda e: self._undo())
            self.bind_all("<Control-comma>",
                          lambda e: (self._open_settings(), "break")[1])
            self.bind_all("<F5>", lambda e: self._reload_recent())

        # ---- Settings dialog (Ctrl+,)
        def _open_settings(self):
            dlg = aura.Dialog(self, title="Settings", size=(460, 260))
            aura.SectionLabel(dlg.body, "Appearance").pack(anchor="w",
                                                           pady=(0, 2))
            trow = ctk.CTkFrame(dlg.body, fg_color="transparent")
            trow.pack(anchor="w", pady=(4, 0))
            aura.Caption(trow, "Theme").pack(side="left", padx=(0, 10))
            cur = guiconfig.get_theme()
            th = aura.AuraOption(trow, values=["System", "Light", "Dark"],
                                 width=110, height=30,
                                 command=self._set_theme_pref)
            th.set(cur.capitalize() if cur in ("light", "dark") else "System")
            th.pack(side="left")
            aura.Caption(dlg.body,
                         "System follows the OS Aura Dark/Light live.").pack(
                anchor="w", pady=(6, 0))
            aura.Caption(dlg.body,
                         "Captures and recordings stay on this device — "
                         "nothing is ever uploaded.").pack(
                anchor="w", pady=(14, 0))
            dlg.add_button("Close")

        def _set_theme_pref(self, choice):
            pref = str(choice).lower()
            if pref == "system":
                guiconfig.set_theme("system")
                self._follow_system = True
                if getattr(self, "_sys_listener", None) is None:
                    self._start_system_listener()
                self.set_theme(aura._system_theme(), _system=True)
            elif pref in ("light", "dark"):
                self.set_theme(pref)     # persists via on_theme_change

        # ---- background op runner (threading + self.after marshalling)
        def _bg(self, work, on_ok, button=None, busy="Working…"):
            if self._busy:
                self.set_error("Please wait — an operation is already running.")
                return
            self._busy = True
            if button is not None:
                try:
                    button.state(["disabled"])
                except Exception:
                    pass
            self.set_status(busy, kind="working")
            self._hide_openfolder()

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
                    self.set_error(err)
                    return
                self.set_status("Done", kind="ok")
                try:
                    on_ok(res)
                except Exception as ex:
                    self.set_error(f"Post-processing error: {ex}")

            threading.Thread(target=run, daemon=True).start()

        # ---- status-bar helpers
        def _hide_openfolder(self):
            try:
                self._openfolder_btn.pack_forget()
            except Exception:
                pass

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
                self._openfolder_btn.pack(side="left")
            self.set_success(message)

        def _open_last_folder(self):
            if self._last_output_dir:
                open_in_file_manager(self._last_output_dir)

        # =====================================================================
        # CAPTURE
        # =====================================================================
        def _build_capture(self, frame):
            tb = aura.Toolbar(frame)
            tb.pack(fill="x", pady=(0, 10))
            tb.add_button("＋ Capture region", self._capture_region,
                          kind="primary")
            tb.add_button("Full screen", self._capture_full)
            tb.add_button("Window (best-effort)", self._capture_window)

            aura.Caption(frame,
                         "Region opens a translucent full-screen overlay — "
                         "drag a rectangle to grab it (Ctrl+N). Every "
                         "capture opens in Annotate.",
                         wraplength=760, justify="left").pack(
                anchor="w", pady=(0, 12))

            mon = aura.Card(frame, title="Capture one monitor")
            mon.pack(fill="x", pady=(14, 0))
            row = ctk.CTkFrame(mon.body, fg_color="transparent")
            row.pack(fill="x")
            ctk.CTkLabel(row, text="Monitor", font=aura.font(),
                         width=70, anchor="w").pack(side="left")
            self._mon_var = tk.StringVar(value="all")
            self._mon_combo = aura.AuraCombo(row, variable=self._mon_var,
                                             state="readonly", width=240,
                                             values=["all"])
            self._mon_combo.pack(side="left")
            aura.AuraButton(row, "Capture monitor", kind="secondary",
                            command=self._capture_monitor).pack(
                side="left", padx=8)
            aura.AuraButton(row, "Refresh", kind="ghost",
                            command=self._reload_monitors).pack(side="left")
            self._cap_note = aura.Caption(mon.body, "", wraplength=720,
                                          justify="left")
            self._cap_note.pack(anchor="w", pady=(10, 0))

            self.after(80, self._reload_monitors)

        def _reload_monitors(self):
            try:
                mons = cap.list_monitors()
            except ScreenKitError:
                self._mon_combo.configure(values=["all"])
                self._cap_note.configure(
                    text="Screen capture is unavailable in this session "
                         "(no display was found) — the buttons above need a "
                         "desktop session.")
                return
            vals = ["all"]
            for m in mons:
                if m["index"] == 0:
                    continue
                vals.append(f"{m['index']}: {m['width']}×{m['height']} "
                            f"@({m['left']},{m['top']})")
            self._mon_combo.configure(values=vals)
            n = max(0, len(vals) - 1) or 1
            self._cap_note.configure(
                text=f"{n} monitor(s) detected. \"all\" grabs the combined "
                     "virtual screen.")

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
                self.set_error(str(ex))
            except Exception as ex:
                self.set_error(f"Could not open the region overlay: {ex}")

        def _region_selected(self, region):
            if not region:
                self.set_status("Ready")
                return
            x, y, w, h = region
            if w < 2 or h < 2:
                self.set_error("Region too small — drag a larger rectangle.")
                return
            self._bg(lambda: cap.capture_region(x, y, w, h),
                     lambda img: self._open_in_editor(
                         img, f"Region {w}×{h} captured."))

        # =====================================================================
        # ANNOTATE
        # =====================================================================
        def _build_annotate(self, frame):
            tb = aura.Toolbar(frame)
            tb.pack(fill="x", pady=(0, 10))
            tb.add_button("＋ New capture", self._capture_region,
                          kind="primary")
            tb.add_button("Open…", self._open_image)
            tb.add_separator()
            tb.add_button("Undo", self._undo, kind="ghost")
            save_btn = aura.AuraButton(tb, "Save…", kind="secondary",
                                       height=32, command=self._save_edit)
            tb.add_right(save_btn)
            copy_btn = aura.AuraButton(tb, "Copy", kind="ghost",
                                       height=32, command=self._copy_edit)
            tb.add_right(copy_btn)

            self._tool_var = tk.StringVar(value="arrow")
            self._tool_seg = aura.SegmentedControl(
                frame, values=[label for _tid, label in EDITOR_TOOLS],
                dynamic_resizing=True,
                command=lambda label: self._set_tool(
                    TOOL_BY_LABEL.get(label, "arrow")))
            self._tool_seg.pack(anchor="w")

            opts = ctk.CTkFrame(frame, fg_color="transparent")
            opts.pack(fill="x", pady=(10, 4))
            ctk.CTkLabel(opts, text="Colour", font=aura.font()).pack(side="left")
            self._color_var = tk.StringVar(value="#ff3b30")
            aura.AuraCombo(opts, variable=self._color_var, width=110, values=[
                "#ff3b30", "#ffcc00", "#34c759", "#0a84ff", "#af52de",
                "#ffffff", "#000000"]).pack(side="left", padx=(6, 0))
            ctk.CTkLabel(opts, text="Width", font=aura.font()).pack(
                side="left", padx=(14, 6))
            self._width_var = tk.StringVar(value="4")
            ttk.Spinbox(opts, from_=1, to=40, width=4,
                        textvariable=self._width_var).pack(side="left")
            ctk.CTkLabel(opts, text="Text", font=aura.font()).pack(
                side="left", padx=(14, 6))
            self._text_entry = aura.AuraEntry(opts, placeholder="Label", width=150)
            self._text_entry.pack(side="left")
            ctk.CTkLabel(opts, text="Size", font=aura.font()).pack(
                side="left", padx=(14, 6))
            self._tsize_var = tk.StringVar(value="24")
            ttk.Spinbox(opts, from_=8, to=200, width=4,
                        textvariable=self._tsize_var).pack(side="left")
            ctk.CTkLabel(opts, text="Step #", font=aura.font()).pack(
                side="left", padx=(14, 6))
            self._num_var = tk.StringVar(value="1")
            ttk.Spinbox(opts, from_=1, to=999, width=4,
                        textvariable=self._num_var).pack(side="left")

            body = ctk.CTkFrame(frame, fg_color="transparent")
            body.pack(fill="both", expand=True, pady=(8, 0))
            self._canvas = tk.Canvas(body, bd=0)
            self._canvas.pack(fill="both", expand=True)
            aura.track(self._canvas, "canvas")
            self._canvas.bind("<Button-1>", self._on_canvas_press)
            self._canvas.bind("<B1-Motion>", self._on_canvas_drag)
            self._canvas.bind("<ButtonRelease-1>", self._on_canvas_release)
            self._canvas.bind("<Configure>", lambda e: self._redraw_canvas())
            self._empty_annotate = aura.EmptyState(
                body, title="No image yet",
                caption="Capture the screen or open an image — every "
                        "capture lands here, ready to annotate.",
                action_text="＋ New capture", action=self._capture_region,
                image=(asset_path("assets/annotate-empty-light.png"),
                       asset_path("assets/annotate-empty-dark.png")))
            self._set_tool("arrow")

        def _set_tool(self, tid):
            self._tool_var.set(tid)
            try:
                self._tool_seg.set(LABEL_BY_TOOL.get(tid, "Arrow"))
            except Exception:
                pass

        def _open_in_editor(self, img, message):
            self._edit_image = img.convert("RGB")
            self._edit_undo = []
            self.report_success(message)
            self.show("annotate")
            self._redraw_canvas()

        def _redraw_canvas(self):
            canvas = getattr(self, "_canvas", None)
            if canvas is None:
                return
            try:
                if not canvas.winfo_exists():
                    return
            except Exception:
                return
            canvas.delete("all")
            empty = getattr(self, "_empty_annotate", None)
            if self._edit_image is None:
                if empty is not None:
                    empty.place(x=0, y=0, relwidth=1, relheight=1)
                    empty.lift()
                return
            if empty is not None:
                empty.place_forget()
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
                    new = ann.text(img, p0,
                                   self._text_entry.get().strip() or "Label",
                                   size=size, color=col)
                elif tool == "number":
                    n = int(self._num_var.get() or 1)
                    new = ann.numbered_step(img, p0, n, color=col)
                    self._num_var.set(str(n + 1))
                else:
                    return
            except ScreenKitError as ex:
                self.set_error(str(ex))
                return
            self._edit_undo.append(self._edit_image)
            self._edit_image = new
            self._redraw_canvas()
            self.set_status("Edited", kind="ok")

        def _undo(self):
            if self._edit_undo:
                self._edit_image = self._edit_undo.pop()
                self._redraw_canvas()
                self.set_status("Undone", kind="ok")

        def _open_image(self):
            path = filedialog.askopenfilename(title="Open image",
                                              filetypes=IMAGE_TYPES)
            if not path:
                return
            try:
                img = Image.open(path)
                img.load()
            except Exception as ex:
                self.set_error(f"Could not open image: {ex}")
                return
            self._open_in_editor(img, f"Opened {os.path.basename(path)}.")

        def _save_edit(self):
            if self._edit_image is None:
                self.set_error("Nothing to save yet — capture or open an "
                               "image first.")
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
                self.set_error("Nothing to copy yet — capture or open an "
                               "image first.")
                return
            ok = cap.to_clipboard(self._edit_image)
            if ok:
                self.report_success("Copied to clipboard.")
            else:
                self.set_error("Clipboard image copy is only available on "
                               "Windows. Use Save instead.")

        # =====================================================================
        # RECORD
        # =====================================================================
        def _build_record(self, frame):
            tb = aura.Toolbar(frame)
            tb.pack(fill="x", pady=(0, 10))
            self._rec_start_btn = tb.add_button("◉ Start recording",
                                                self._rec_start,
                                                kind="primary")
            self._rec_stop_btn = tb.add_button("Stop", self._rec_stop)
            self._rec_stop_btn.state(["disabled"])
            # live recording indicator: danger token, flips with the theme
            self._rec_elapsed = ctk.CTkLabel(
                tb, text="", font=aura.font(15, "bold"), text_color=DANGER)
            tb.add(self._rec_elapsed)

            aura.Caption(frame, SECTION_DESCRIPTIONS["record"],
                         wraplength=760, justify="left").pack(
                anchor="w", pady=(0, 12))

            card = aura.Card(frame, title="Recording options")
            card.pack(fill="x")

            row = ctk.CTkFrame(card.body, fg_color="transparent")
            row.pack(fill="x", pady=(0, 8))
            ctk.CTkLabel(row, text="Area", font=aura.font(),
                         width=60, anchor="w").pack(side="left")
            self._rec_area = tk.StringVar(value="full")
            ctk.CTkRadioButton(row, text="Full screen", value="full",
                               variable=self._rec_area,
                               font=aura.font()).pack(side="left")
            ctk.CTkRadioButton(row, text="Region", value="region",
                               variable=self._rec_area,
                               font=aura.font()).pack(side="left", padx=8)
            aura.AuraButton(row, "Select region…", kind="secondary",
                            command=self._rec_select_region).pack(side="left")
            self._rec_region_lbl = aura.Caption(row, "")
            self._rec_region_lbl.pack(side="left", padx=10)

            row2 = ctk.CTkFrame(card.body, fg_color="transparent")
            row2.pack(fill="x", pady=(0, 10))
            ctk.CTkLabel(row2, text="FPS", font=aura.font(),
                         width=60, anchor="w").pack(side="left")
            self._fps_var = tk.StringVar(value="15")
            ttk.Spinbox(row2, from_=1, to=60, width=5,
                        textvariable=self._fps_var).pack(side="left")
            ctk.CTkLabel(row2, text="Format", font=aura.font()).pack(
                side="left", padx=(16, 6))
            self._fmt_var = tk.StringVar(value="MP4")
            aura.AuraCombo(row2, variable=self._fmt_var, state="readonly",
                           width=90, values=["MP4", "GIF"]).pack(side="left")

            note = ("Live recording shells out to FFmpeg (gdigrab on "
                    "Windows). Stop finalises the file. On non-Windows "
                    "desktops recording is best-effort "
                    "(x11grab/avfoundation).")
            if not self._ffmpeg:
                note = ("FFmpeg was not found, so recording is disabled "
                        "here. Add ffmpeg to enable it. ") + note
            aura.Caption(card.body, note, wraplength=720,
                         justify="left").pack(anchor="w", pady=(10, 0))

        def _rec_select_region(self):
            try:
                RegionOverlay(self, on_done=self._rec_region_done)
            except Exception as ex:
                self.set_error(f"Could not open the region overlay: {ex}")

        def _rec_region_done(self, region):
            if region and region[2] >= 2 and region[3] >= 2:
                self._rec_region = region
                self._rec_area.set("region")
                self._rec_region_lbl.configure(
                    text=f"{region[2]}×{region[3]} @({region[0]},{region[1]})")

        def _rec_start(self):
            self._ffmpeg = ff.find_ffmpeg()
            if not self._ffmpeg:
                self.set_error("FFmpeg not found — recording is unavailable.")
                return
            if self._recorder is not None:
                self.set_error("A recording is already in progress.")
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
                    self.set_error("Select a region first.")
                    return
                region = self._rec_region
            try:
                fps = int(self._fps_var.get())
            except ValueError:
                self.set_error("FPS must be a whole number.")
                return
            rec = ff.Recorder(path, fps=fps, region=region, gif=gif)

            def do_start():
                rec.start()
                return rec

            def started(_r):
                self._recorder = rec
                self._rec_start_btn.state(["disabled"])
                self._rec_stop_btn.state(["!disabled"])
                self.set_status("Recording…", kind="working")
                self._tick_elapsed()
            self._bg(do_start, started, button=self._rec_start_btn,
                     busy="Starting…")

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
            self.set_status("Finalising…", kind="working")
            self._rec_stop_btn.state(["disabled"])

            def do_stop():
                return rec.stop()

            def stopped(path):
                self._recorder = None
                self._rec_start_btn.state(["!disabled"])
                self._rec_elapsed.configure(text="")
                self.report_success(f"Saved recording → {path}", [path])
            # run stop off-thread; clear _recorder now so the tick stops cleanly
            self._recorder = None
            self._rec_elapsed.configure(text="")
            self._bg(do_stop, stopped)

        # =====================================================================
        # RECENT
        # =====================================================================
        def _build_recent(self, frame):
            aura.Caption(frame, SECTION_DESCRIPTIONS["recent"],
                         wraplength=760, justify="left").pack(
                anchor="w", pady=(0, 12))

            card = aura.Card(frame, title="Recently saved files")
            card.pack(fill="both", expand=True)
            box = ctk.CTkFrame(card.body, fg_color="transparent")
            box.pack(fill="both", expand=True)
            self._recent_lb = tk.Listbox(box, activestyle="none",
                                         exportselection=False)
            sb = ttk.Scrollbar(box, orient="vertical",
                               command=self._recent_lb.yview)
            self._recent_lb.configure(yscrollcommand=sb.set)
            sb.pack(side="right", fill="y")
            self._recent_lb.pack(side="left", fill="both", expand=True)
            aura.track(self._recent_lb, "listbox")
            self._recent_lb.bind("<Double-Button-1>",
                                 lambda e: self._recent_open(False))

            btns = ctk.CTkFrame(card.body, fg_color="transparent")
            btns.pack(fill="x", pady=(10, 0))
            aura.AuraButton(btns, "Open", kind="secondary",
                            command=lambda: self._recent_open(False)).pack(
                side="left")
            aura.AuraButton(btns, "Open in Annotate", kind="secondary",
                            command=self._recent_edit).pack(
                side="left", padx=8)
            aura.AuraButton(btns, "Open folder", kind="secondary",
                            command=lambda: self._recent_open(True)).pack(
                side="left")
            aura.AuraButton(btns, "Clear list", kind="ghost",
                            command=self._recent_clear).pack(
                side="left", padx=8)
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
                self.set_error("Select an existing file first.")
                return
            (open_in_file_manager if reveal else open_with_default_app)(path)

        def _recent_edit(self):
            path = self._recent_selected()
            if not path:
                self.set_error("Select an existing image file first.")
                return
            try:
                img = Image.open(path)
                img.load()
            except Exception as ex:
                self.set_error(f"Not an editable image: {ex}")
                return
            self._open_in_editor(img, f"Opened {os.path.basename(path)}.")

        def _recent_clear(self):
            guiconfig.clear_recent()
            self._reload_recent()

        # =====================================================================
        # ABOUT
        # =====================================================================
        def _build_about(self, frame):
            card = aura.Card(frame, title="About Screen Studio")
            card.pack(fill="x")
            aura.Heading(card.body, APP_NAME).pack(anchor="w")
            aura.Caption(card.body, f"Version {APP_VERSION}").pack(
                anchor="w", pady=(0, 10))
            ctk.CTkLabel(
                card.body, font=aura.font(), justify="left", anchor="w",
                wraplength=520,
                text="A fast, fully-offline screenshot, annotation and "
                     "screen-recording tool.\n\n100% AI-built, open source, "
                     "published on QuickOpen. Nothing is uploaded "
                     "anywhere.").pack(anchor="w")
            aura.Caption(
                card.body,
                "Licensed under Apache-2.0. Built on mss, Pillow and "
                "CustomTkinter (MIT); screen recording uses an external "
                "FFmpeg (LGPL/GPL).", wraplength=520,
                justify="left").pack(anchor="w", pady=(10, 4))
            aura.AuraButton(card.body, "Project page: quickopen.ai",
                            kind="ghost",
                            command=lambda: open_with_default_app(
                                PROJECT_URL)).pack(anchor="w", pady=(6, 0))

        # ---- shutdown
        def _on_close(self):
            try:
                if self._recorder is not None:
                    self._recorder.cancel()
            except Exception:
                pass
            self.destroy()

    # -----------------------------------------------------------------------
    # Fullscreen translucent region-selection overlay (deliberately raw tk —
    # it is a temporary borderless overlay, not themed chrome)
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
                fill="#ffffff", font=aura.font(14))
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
                x0, y0, x1, y1, outline=ACCENT, width=2)

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
    With no display (e.g. a server), without Pillow's Tk bridge, or without
    customtkinter installed, it prints a friendly note and returns 0 instead
    of raising.
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
    except ImportError as exc:
        print(f"{APP_NAME}: the GUI needs the 'customtkinter' package "
              f"({exc}). Install it with:  pip install customtkinter")
        return 0
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
