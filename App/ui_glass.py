"""
Acoustic glassmorphic UI kit for Tkinter.

Tk has no CSS border-radius / backdrop-filter — we paint frosted rounded
surfaces with PIL + Canvas and swap button faces on hover.
"""

from __future__ import annotations

import math
import tkinter as tk
from pathlib import Path
from typing import Callable, Optional

try:
    from PIL import Image, ImageDraw, ImageFilter, ImageTk
except ImportError:  # pragma: no cover
    Image = None  # type: ignore
    ImageDraw = None  # type: ignore
    ImageFilter = None  # type: ignore
    ImageTk = None  # type: ignore

# Brand
TEAL = (0, 128, 128)
TEAL_HI = (0, 180, 180)
TEAL_DEEP = (0, 90, 90)
WHITE = (245, 252, 252)
MUTED = (150, 190, 190)

# Glass fills (RGBA) — translucency over dark field
GLASS_FILL = (18, 52, 56, 165)
GLASS_FILL_HI = (28, 72, 78, 195)
GLASS_SEL = (12, 80, 84, 210)
GLASS_BORDER = (0, 160, 160, 120)
GLASS_BORDER_HI = (0, 220, 220, 200)
CARD_SHADOW = (0, 0, 0, 90)

G = {
    "bg": "#071214",
    "bg_deep": "#040a0c",
    "panel": "#0c1c1f",
    "text": "#eaf8f8",
    "muted": "#8fb8b8",
    "accent": "#008080",
    "accent_hi": "#00b4b4",
    "danger": "#d06060",
    "ok": "#008080",
    "white": "#f5fcfc",
}


def _pil_ready() -> bool:
    return Image is not None and ImageDraw is not None and ImageTk is not None


def vertical_gradient(w: int, h: int, top=(4, 14, 16), bottom=(8, 28, 32)) -> "Image.Image":
    img = Image.new("RGB", (max(1, w), max(1, h)))
    px = img.load()
    for y in range(h):
        t = y / max(h - 1, 1)
        r = int(top[0] + (bottom[0] - top[0]) * t)
        g = int(top[1] + (bottom[1] - top[1]) * t)
        b = int(top[2] + (bottom[2] - top[2]) * t)
        for x in range(w):
            # subtle left/right vignette
            edge = min(x, w - 1 - x) / max(w * 0.35, 1)
            e = min(1.0, edge)
            rf = int(r * (0.75 + 0.25 * e))
            gf = int(g * (0.75 + 0.25 * e))
            bf = int(b * (0.78 + 0.22 * e))
            # micro teal flecks for depth
            if (x + y * 3) % 97 == 0:
                rf = min(255, rf + 12)
                gf = min(255, gf + 18)
                bf = min(255, bf + 18)
            px[x, y] = (rf, gf, bf)
    return img


def rounded_rect(
    size: tuple[int, int],
    radius: int,
    fill: tuple,
    border: tuple | None = None,
    border_width: int = 1,
    shadow: bool = True,
    blur: int = 6,
) -> "Image.Image":
    w, h = size
    w, h = max(w, 4), max(h, 4)
    pad = blur + 4 if shadow else 2
    canvas = Image.new("RGBA", (w + pad * 2, h + pad * 2), (0, 0, 0, 0))
    draw = ImageDraw.Draw(canvas)
    box = [pad, pad, pad + w - 1, pad + h - 1]
    r = max(2, min(radius, w // 2, h // 2))

    if shadow:
        sh = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
        sd = ImageDraw.Draw(sh)
        sd.rounded_rectangle(
            [pad + 2, pad + 3, pad + w + 1, pad + h + 2],
            radius=r,
            fill=CARD_SHADOW,
        )
        sh = sh.filter(ImageFilter.GaussianBlur(blur))
        canvas = Image.alpha_composite(canvas, sh)
        draw = ImageDraw.Draw(canvas)

    # soft inner glow (glass highlight)
    draw.rounded_rectangle(box, radius=r, fill=fill)
    # top sheen
    sheen = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    sd = ImageDraw.Draw(sheen)
    sd.rounded_rectangle(
        [box[0] + 1, box[1] + 1, box[2] - 1, box[1] + max(8, h // 3)],
        radius=max(2, r - 2),
        fill=(255, 255, 255, 28),
    )
    sheen = sheen.filter(ImageFilter.GaussianBlur(3))
    canvas = Image.alpha_composite(canvas, sheen)
    draw = ImageDraw.Draw(canvas)

    if border:
        draw.rounded_rectangle(box, radius=r, outline=border, width=border_width)
    return canvas


def button_face(
    w: int,
    h: int,
    radius: int,
    *,
    mode: str = "normal",  # normal | hover | active | disabled
    kind: str = "primary",  # primary | secondary | danger | ghost
) -> "Image.Image":
    if kind == "primary":
        fills = {
            "normal": (*TEAL, 210),
            "hover": (*TEAL_HI, 235),
            "active": (*TEAL_DEEP, 240),
            "disabled": (40, 70, 70, 120),
        }
        borders = {
            "normal": (120, 220, 220, 160),
            "hover": (180, 255, 255, 220),
            "active": (80, 180, 180, 180),
            "disabled": (60, 90, 90, 80),
        }
    elif kind == "danger":
        fills = {
            "normal": (160, 60, 60, 210),
            "hover": (200, 80, 80, 235),
            "active": (120, 40, 40, 240),
            "disabled": (70, 40, 40, 120),
        }
        borders = {
            "normal": (240, 140, 140, 160),
            "hover": (255, 180, 180, 220),
            "active": (200, 100, 100, 180),
            "disabled": (90, 50, 50, 80),
        }
    elif kind == "ghost":
        fills = {
            "normal": (20, 48, 52, 100),
            "hover": (0, 100, 100, 160),
            "active": (0, 70, 70, 190),
            "disabled": (20, 30, 32, 80),
        }
        borders = {
            "normal": (0, 128, 128, 100),
            "hover": (0, 200, 200, 200),
            "active": (0, 160, 160, 180),
            "disabled": (50, 70, 70, 60),
        }
    else:  # secondary
        fills = {
            "normal": GLASS_FILL,
            "hover": GLASS_FILL_HI,
            "active": GLASS_SEL,
            "disabled": (20, 30, 32, 90),
        }
        borders = {
            "normal": GLASS_BORDER,
            "hover": GLASS_BORDER_HI,
            "active": (*TEAL, 180),
            "disabled": (50, 70, 70, 70),
        }
    return rounded_rect(
        (w, h),
        radius,
        fills.get(mode, fills["normal"]),
        borders.get(mode, borders["normal"]),
        border_width=2 if mode == "hover" else 1,
        shadow=True,
        blur=5,
    )


class GradientBackground(tk.Canvas):
    """Full-window vertical gradient wallpaper."""

    def __init__(self, master, **kw):
        super().__init__(master, highlightthickness=0, bd=0, **kw)
        self._photo = None
        self.bind("<Configure>", self._redraw, add="+")

    def _redraw(self, event=None):
        if not _pil_ready():
            self.configure(bg=G["bg"])
            return
        w = max(self.winfo_width(), 2)
        h = max(self.winfo_height(), 2)
        if w < 4 or h < 4:
            return
        img = vertical_gradient(w, h, top=(3, 12, 14), bottom=(10, 32, 36))
        # soft teal orb (glass light source)
        orb = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        draw = ImageDraw.Draw(orb)
        ox, oy = int(w * 0.7), int(h * 0.12)
        r = max(80, w // 5)
        draw.ellipse([ox - r, oy - r, ox + r, oy + r], fill=(0, 128, 128, 40))
        ox2, oy2 = int(w * 0.15), int(h * 0.75)
        r2 = max(60, w // 6)
        draw.ellipse([ox2 - r2, oy2 - r2, ox2 + r2, oy2 + r2], fill=(0, 90, 90, 28))
        orb = orb.filter(ImageFilter.GaussianBlur(40))
        base = img.convert("RGBA")
        composed = Image.alpha_composite(base, orb).convert("RGB")
        self._photo = ImageTk.PhotoImage(composed)
        self.delete("all")
        self.create_image(0, 0, image=self._photo, anchor="nw")
        # Canvas.lower is tag-based; lower whole widget under siblings
        try:
            self.tk.call("lower", self._w)
        except Exception:
            pass


class GlassPanel(tk.Frame):
    """
    Frosted glass card with rounded corners painted as a background image.
    Children sit in `.body` with matching solid-ish fill (no see-through of child contents needed).
    """

    def __init__(
        self,
        master,
        *,
        radius: int = 18,
        padx: int = 14,
        pady: int = 12,
        fill=GLASS_FILL,
        border=GLASS_BORDER,
        **kw,
    ):
        super().__init__(master, bg=G["bg"], highlightthickness=0, bd=0, **kw)
        self._radius = radius
        self._fill = fill
        self._border = border
        self._photo = None
        self._bg = tk.Label(self, bd=0, highlightthickness=0, bg=G["bg"])
        self._bg.place(x=0, y=0, relwidth=1, relheight=1)
        self.body = tk.Frame(self, bg=self._approx_fill(), highlightthickness=0, bd=0)
        self.body.pack(fill="both", expand=True, padx=padx, pady=pady)
        self.bind("<Configure>", self._paint, add="+")
        self._paint()

    def _approx_fill(self) -> str:
        r, g, b, a = self._fill
        # approximate blend onto #071214
        br, bgc, bb = 7, 18, 20
        t = a / 255.0
        rr = int(br * (1 - t) + r * t)
        gg = int(bgc * (1 - t) + g * t)
        bb = int(bb * (1 - t) + b * t)
        return f"#{rr:02x}{gg:02x}{bb:02x}"

    def _paint(self, _e=None):
        if not _pil_ready():
            self.configure(bg=self._approx_fill())
            self.body.configure(bg=self._approx_fill())
            return
        w = max(self.winfo_width(), 20)
        h = max(self.winfo_height(), 20)
        # room for shadow padding in image — draw then crop labeling
        img = rounded_rect((w, h), self._radius, self._fill, self._border, 1, True, 6)
        # scale to widget size
        img = img.resize((w, h), Image.Resampling.LANCZOS)
        self._photo = ImageTk.PhotoImage(img)
        self._bg.configure(image=self._photo)
        # body bg matches avg glass so children blend
        approx = self._approx_fill()
        self.body.configure(bg=approx)
        for child in self.body.winfo_children():
            try:
                if isinstance(child, (tk.Frame, tk.Label)) and not isinstance(child, GlassButton):
                    if str(child.cget("bg")) in (G["bg"], "", "SystemButtonFace", approx):
                        child.configure(bg=approx)
            except Exception:
                pass


class GlassButton(tk.Canvas):
    """Rounded glass CTA with hover / press feedback."""

    def __init__(
        self,
        master,
        text: str,
        command: Callable | None = None,
        *,
        kind: str = "primary",
        width: int = 140,
        height: int = 40,
        radius: int = 14,
        font=("Segoe UI", 10, "bold"),
        state: str = "normal",
        **kw,
    ):
        super().__init__(
            master,
            width=width,
            height=height,
            highlightthickness=0,
            bd=0,
            cursor="hand2",
            **kw,
        )
        if "bg" not in kw:
            try:
                super().configure(bg=G["bg"])
            except Exception:
                pass
        self._text = text
        self._command = command
        self._kind = kind
        self._radius = radius
        self._font = font
        self._state = state
        self._mode = "normal" if state == "normal" else "disabled"
        self._faces: dict[str, object] = {}
        self._img_id = None
        self._text_id = None
        self.bind("<Enter>", self._on_enter, add="+")
        self.bind("<Leave>", self._on_leave, add="+")
        self.bind("<ButtonPress-1>", self._on_press, add="+")
        self.bind("<ButtonRelease-1>", self._on_release, add="+")
        self.bind("<Configure>", lambda e: self._rebuild(), add="+")
        self._rebuild()

    def configure(self, cnf=None, **kw):  # type: ignore[override]
        if cnf is not None:
            if isinstance(cnf, dict):
                kw = {**cnf, **kw}
            else:
                return super().configure(cnf)
        if "text" in kw:
            self._text = kw.pop("text")
        if "command" in kw:
            self._command = kw.pop("command")
        if "state" in kw:
            self._state = kw.pop("state")
            self._mode = "disabled" if self._state == "disabled" else "normal"
            kw["cursor"] = "" if self._state == "disabled" else "hand2"
        if kw:
            super().configure(**kw)
        self._rebuild()
        return None

    config = configure

    def cget(self, key):  # type: ignore[override]
        if key == "state":
            return self._state
        if key == "text":
            return self._text
        return super().cget(key)

    def _rebuild(self):
        w = max(int(self.winfo_reqwidth() or self["width"] or 120), 40)
        h = max(int(self.winfo_reqheight() or self["height"] or 36), 28)
        try:
            w = max(self.winfo_width(), w)
            h = max(self.winfo_height(), h)
        except Exception:
            pass
        if w < 20:
            w = int(self["width"] or 120)
        if h < 16:
            h = int(self["height"] or 36)

        self.delete("all")
        if _pil_ready():
            for mode in ("normal", "hover", "active", "disabled"):
                face = button_face(w, h, self._radius, mode=mode, kind=self._kind)
                # crop shadow margin roughly: face already includes pad; resize to canvas
                face = face.resize((w, h), Image.Resampling.LANCZOS)
                self._faces[mode] = ImageTk.PhotoImage(face)
            mode = self._mode if self._mode in self._faces else "normal"
            self._img_id = self.create_image(0, 0, image=self._faces[mode], anchor="nw")
        else:
            self.configure(bg=G["accent"])
        fill = G["white"] if self._kind in ("primary", "danger") else G["text"]
        if self._state == "disabled":
            fill = G["muted"]
        self._text_id = self.create_text(
            w // 2,
            h // 2,
            text=self._text,
            fill=fill,
            font=self._font,
        )

    def _set_mode(self, mode: str):
        if self._state == "disabled":
            mode = "disabled"
        self._mode = mode
        if _pil_ready() and mode in self._faces and self._img_id:
            self.itemconfigure(self._img_id, image=self._faces[mode])

    def _on_enter(self, _e=None):
        if self._state != "disabled":
            self._set_mode("hover")

    def _on_leave(self, _e=None):
        self._set_mode("disabled" if self._state == "disabled" else "normal")

    def _on_press(self, _e=None):
        if self._state != "disabled":
            self._set_mode("active")

    def _on_release(self, _e=None):
        if self._state == "disabled":
            return
        self._set_mode("hover")
        if self._command:
            self._command()


class GlassTabBar(tk.Frame):
    """Pill / rounded tab strip with hover and selected glass states."""

    def __init__(
        self,
        master,
        tabs: list[tuple[str, str]],
        on_select: Callable[[str], None],
        **kw,
    ):
        super().__init__(master, bg=G["bg"], highlightthickness=0, **kw)
        self._on_select = on_select
        self._tabs = tabs  # (id, label)
        self._selected = tabs[0][0] if tabs else ""
        self._btns: dict[str, GlassButton] = {}
        self._build()

    def _build(self):
        for child in self.winfo_children():
            child.destroy()
        self._btns.clear()
        rail = GlassPanel(self, radius=16, padx=8, pady=8, fill=(12, 40, 44, 160))
        rail.pack(fill="x")
        row = tk.Frame(rail.body, bg=rail.body.cget("bg"))
        row.pack(fill="x")
        for tid, label in self._tabs:
            kind = "primary" if tid == self._selected else "ghost"
            try:
                pbg = row.cget("bg")
            except Exception:
                pbg = G["bg"]
            btn = GlassButton(
                row,
                f"  {label}  ",
                command=lambda t=tid: self.select(t),
                kind=kind,
                width=max(90, 12 * len(label) + 36),
                height=36,
                radius=12,
                font=("Segoe UI", 10, "bold"),
                bg=pbg,
            )
            btn.pack(side="left", padx=4, pady=2)
            self._btns[tid] = btn

    def select(self, tab_id: str):
        changed = tab_id != self._selected
        self._selected = tab_id
        if changed:
            for tid, btn in self._btns.items():
                btn._kind = "primary" if tid == tab_id else "ghost"
                btn._rebuild()
        self._on_select(tab_id)

    @property
    def selected(self) -> str:
        return self._selected


class GlassDialog(tk.Toplevel):
    """Rounded translucent-style modal dialog (custom messagebox)."""

    def __init__(
        self,
        master,
        title: str,
        message: str,
        *,
        kind: str = "info",  # info | error | yesno | actions
        width: int = 460,
        height: int = 260,
        actions: list[tuple[str, str]] | None = None,
        # actions: list of (result_key, button_label); used when kind == "actions"
    ):
        super().__init__(master)
        self.result = False
        self.action_result: str | None = None
        self.title(title)
        self.configure(bg=G["bg_deep"])
        self.resizable(False, False)
        self.transient(master)
        self.grab_set()
        try:
            self.attributes("-topmost", True)
        except Exception:
            pass

        # center
        self.update_idletasks()
        sw = self.winfo_screenwidth()
        sh = self.winfo_screenheight()
        x = (sw - width) // 2
        y = (sh - height) // 3
        self.geometry(f"{width}x{height}+{x}+{y}")

        bg = GradientBackground(self)
        bg.place(x=0, y=0, relwidth=1, relheight=1)

        panel = GlassPanel(self, radius=22, padx=22, pady=18)
        panel.place(relx=0.5, rely=0.5, anchor="center", width=width - 40, height=height - 40)

        accent = G["accent"] if kind != "error" else G["danger"]
        tk.Label(
            panel.body,
            text=title,
            font=("Segoe UI", 14, "bold"),
            bg=panel.body.cget("bg"),
            fg=accent,
        ).pack(anchor="w", pady=(0, 8))
        tk.Label(
            panel.body,
            text=message,
            font=("Segoe UI", 10),
            bg=panel.body.cget("bg"),
            fg=G["text"],
            wraplength=width - 100,
            justify="left",
        ).pack(anchor="w", fill="both", expand=True)

        btns = tk.Frame(panel.body, bg=panel.body.cget("bg"))
        btns.pack(fill="x", pady=(12, 0))
        if kind == "yesno":
            GlassButton(
                btns, "  Cancel  ", command=self._no, kind="ghost", width=110, height=36
            ).pack(side="right", padx=4)
            GlassButton(
                btns, "  Yes  ", command=self._yes, kind="primary", width=110, height=36
            ).pack(side="right", padx=4)
        elif kind == "actions" and actions:
            # Primary first action on the right (typical UX: confirm last)
            for i, (key, label) in enumerate(reversed(actions)):
                kind_btn = "primary" if i == 0 else "ghost"
                GlassButton(
                    btns,
                    f"  {label}  ",
                    command=lambda k=key: self._action(k),
                    kind=kind_btn,
                    width=max(110, min(160, 18 + 8 * len(label))),
                    height=36,
                ).pack(side="right", padx=4)
        else:
            GlassButton(
                btns, "  OK  ", command=self._ok, kind="primary", width=110, height=36
            ).pack(side="right")

        self.protocol(
            "WM_DELETE_WINDOW",
            self._no if kind in ("yesno", "actions") else self._ok,
        )
        self.bind(
            "<Escape>",
            lambda e: (
                self._no()
                if kind == "yesno"
                else (self._action("cancel") if kind == "actions" else self._ok())
            ),
        )
        self.bind(
            "<Return>",
            lambda e: (
                self._yes()
                if kind == "yesno"
                else (
                    self._action(actions[0][0])
                    if kind == "actions" and actions
                    else self._ok()
                )
            ),
        )

    def _ok(self):
        self.result = True
        self.destroy()

    def _yes(self):
        self.result = True
        self.destroy()

    def _no(self):
        self.result = False
        self.destroy()

    def _action(self, key: str):
        self.action_result = str(key)
        self.result = key not in ("cancel", "no", "")
        self.destroy()


def glass_info(master, title: str, message: str):
    d = GlassDialog(master, title, message, kind="info")
    master.wait_window(d)
    return d.result


def glass_error(master, title: str, message: str):
    d = GlassDialog(master, title, message, kind="error")
    master.wait_window(d)
    return d.result


def glass_yesno(master, title: str, message: str) -> bool:
    d = GlassDialog(master, title, message, kind="yesno")
    master.wait_window(d)
    return bool(d.result)


def glass_actions(
    master,
    title: str,
    message: str,
    actions: list[tuple[str, str]],
    *,
    width: int = 520,
    height: int = 300,
) -> str:
    """
    Multi-button modal. actions = [(key, label), ...].
    Returns key, or 'cancel' if closed without choice.
    """
    d = GlassDialog(
        master,
        title,
        message,
        kind="actions",
        width=width,
        height=height,
        actions=actions,
    )
    master.wait_window(d)
    return str(d.action_result or "cancel")


def glass_warn(master, title: str, message: str):
    """Warning uses info chrome; same transparent modal treatment."""
    return glass_info(master, title, message)


def rounded_image_card(
    thumb: "Image.Image",
    *,
    size: int = 160,
    radius: int = 16,
    selected: bool = False,
    is_main: bool = False,
) -> "Image.Image":
    """Photo with rounded clip + glass border for selection highlight."""
    thumb = thumb.convert("RGBA")
    thumb.thumbnail((size, size), Image.Resampling.LANCZOS)
    w, h = thumb.size
    # pad square
    sq = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    sq.paste(thumb, ((size - w) // 2, (size - h) // 2))
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, size - 1, size - 1], radius=radius, fill=255)
    sq.putalpha(mask)

    border_col = (
        (*TEAL_HI, 255) if is_main else ((*TEAL, 230) if selected else (80, 110, 110, 140))
    )
    bw = 4 if is_main else (3 if selected else 1)
    out = rounded_rect(
        (size + 16, size + 16),
        radius + 4,
        (12, 36, 40, 180) if selected or is_main else (10, 28, 32, 120),
        border_col,
        bw,
        True,
        8,
    )
    # paste photo inset
    out.paste(sq, (8, 8), sq)
    return out
