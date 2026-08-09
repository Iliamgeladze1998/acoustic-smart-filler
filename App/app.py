"""Acoustic Smart Filler — scrape → review → fill (never auto-saves)."""

from __future__ import annotations

import json
import re
import threading
import time
import tkinter as tk
import webbrowser
from pathlib import Path
from tkinter import scrolledtext, ttk
from urllib.error import URLError
from urllib.request import urlopen

from selenium import webdriver
from selenium.common.exceptions import WebDriverException
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service

from ai_generate import (
    generate_product_fields,
    match_category_options_strict,
    _is_condition_label,
    _title_indicates_used,
    _pick_used_condition_option,
)
from config_loader import ensure_config_exists, has_usable_api_key, load_config, save_config
from image_tools import (
    craft_image_search_query,
    download_thumbnail_preview,
    prepare_image_for_upload,
    search_product_images,
)
from image_upload import upload_images_to_product
from richtext_html import (
    apply_html_to_text_widget,
    configure_rich_text_tags,
    text_widget_to_html,
)
from page_scripts import (
    apply_product_fill,
    click_product_save,
    open_product_edit,
    scan_product_list_page,
    scan_product_page,
    verify_product_form_filled,
)
from ui_glass import (
    G as GLASS,
    GlassButton,
    GlassPanel,
    GlassTabBar,
    GradientBackground,
    glass_actions,
    glass_error,
    glass_info,
    glass_warn,
    glass_yesno,
    rounded_image_card,
)
from youtube_tools import is_youtube_search_url, resolve_youtube_watch_url

DEBUG_ADDRESS = "127.0.0.1:9222"
TARGET_HOST = "acoustic.ge"
TARGET_PATH = "/aco_st_admin.php"
TARGET_DISPATCH = "dispatch=products.update"
MAX_IMAGE_KB = 400

# Reuse one Selenium attach to debug Chrome (local) — avoids slow ChromeDriver relaunch every Scrape/Fill
_chrome_driver = None
_chrome_driver_lock = threading.Lock()

# OpenAI chat models for the settings dropdown
GPT_MODELS = [
    "gpt-4o-mini",
    "gpt-4o",
    "gpt-4.1-mini",
    "gpt-4.1",
    "gpt-4.1-nano",
    "gpt-5-mini",
    "gpt-5",
    "gpt-5.1",
    "gpt-5.2",
    "o4-mini",
    "o3-mini",
]

# Acoustic glass theme maps (string hex for legacy widgets)
LOGO_PATH = Path(__file__).resolve().parent / "assets" / "logo_acoustic.png"

C = {
    "bg": GLASS["bg"],
    "bg_deep": GLASS["bg_deep"],
    "bg_mid": "#0a1c1f",
    "bg_grad": "#0c1d20",
    "glass": "#143438",
    "glass_hi": "#1a454a",
    "glass_sel": "#186066",
    "card": "#143438",
    "card_border": "#2a6868",
    "glass_border": "#3d9090",
    "accent": GLASS["accent"],
    "accent_hi": GLASS["accent_hi"],
    "accent_dim": "#006666",
    "action": GLASS["accent"],
    "action_hi": GLASS["accent_hi"],
    "danger": GLASS["danger"],
    "danger_hi": "#e07070",
    "text": GLASS["text"],
    "muted": GLASS["muted"],
    "white": GLASS["white"],
    "image_bg": "#061416",
    "row_alt": "#0e282c",
    "ok": GLASS["ok"],
    "input_bg": "#0a1e22",
    "input_border": "#2f6262",
}

FONT = "Segoe UI"
FONT_UI = "Segoe UI"
FONT_MONO = "Consolas"


def chrome_debug_available(timeout: float = 1.2) -> bool:
    """True when debug Chrome is listening on 127.0.0.1:9222 (local only — not website network)."""
    try:
        with urlopen(f"http://{DEBUG_ADDRESS}/json/version", timeout=timeout) as response:
            json.load(response)
        return True
    except Exception:
        return False


def _driver_alive(driver) -> bool:
    if driver is None:
        return False
    try:
        _ = driver.window_handles
        return True
    except Exception:
        return False


def connect_to_chrome(*, force_new: bool = False, status_cb=None):
    """
    Attach Selenium to the already-running debug Chrome on 127.0.0.1:9222.

    This is local IPC, not acoustic.ge. Slowness is usually:
      1) First-time ChromeDriver download via Selenium Manager (needs internet once), or
      2) Chrome not running on port 9222 / debug profile not started.
    Subsequent calls reuse the same driver (fast).
    """
    global _chrome_driver

    def _status(msg: str) -> None:
        if status_cb:
            try:
                status_cb(msg)
            except Exception:
                pass

    with _chrome_driver_lock:
        if not force_new and _driver_alive(_chrome_driver):
            _status("Chrome already connected (local).")
            return _chrome_driver

        _status("Checking debug Chrome on 127.0.0.1:9222…")
        if not chrome_debug_available(timeout=1.5):
            # Brief retry — Chrome just starting via START.vbs
            ready = False
            for _ in range(8):
                time.sleep(0.4)
                if chrome_debug_available(timeout=1.0):
                    ready = True
                    break
            if not ready:
                raise RuntimeError(
                    "Chrome debug port 127.0.0.1:9222 is not open.\n\n"
                    "This is a LOCAL connection (not website network).\n"
                    "Run START.bat / START.vbs so the special debug Chrome window opens,\n"
                    "log into admin there, open the product, then Scrape again."
                )

        _status("Starting ChromeDriver (first time can take 30–90s if it downloads)…")
        t0 = time.perf_counter()
        options = Options()
        options.add_experimental_option("debuggerAddress", DEBUG_ADDRESS)
        # Faster attach to an already-loaded admin page
        options.page_load_strategy = "eager"
        try:
            service = Service()
            driver = webdriver.Chrome(service=service, options=options)
        except WebDriverException as exc:
            msg = str(exc)
            hint = (
                "Could not attach to Chrome.\n\n"
                "Local port 9222 is open, but ChromeDriver failed.\n"
                "Common causes:\n"
                " • First run: Selenium downloading a matching ChromeDriver (needs internet once)\n"
                " • Chrome was updated — run Scrape again after download finishes\n"
                " • Another program is using the same debug port\n\n"
                f"Detail: {msg[:400]}"
            )
            raise RuntimeError(hint) from exc
        except Exception as exc:
            raise RuntimeError(
                f"Could not start ChromeDriver: {exc}\n"
                "Internet is only needed the first time to fetch ChromeDriver."
            ) from exc

        try:
            driver.set_page_load_timeout(45)
            driver.set_script_timeout(180)
            # Touch the session so failures surface immediately
            _ = driver.current_url
        except Exception:
            pass

        elapsed = time.perf_counter() - t0
        _chrome_driver = driver
        if elapsed > 8:
            _status(
                f"Chrome attached in {elapsed:.0f}s (slow = ChromeDriver download/start, not the website)."
            )
        else:
            _status(f"Chrome attached in {elapsed:.1f}s.")
        return driver


def disconnect_chrome() -> None:
    """Drop cached session (driver only detaches; does not close the user Chrome window)."""
    global _chrome_driver
    with _chrome_driver_lock:
        if _chrome_driver is not None:
            try:
                _chrome_driver.quit()
            except Exception:
                pass
            _chrome_driver = None


def is_product_edit_url(url: str) -> bool:
    return (
        url.startswith(f"https://{TARGET_HOST}{TARGET_PATH}")
        or url.startswith(f"http://{TARGET_HOST}{TARGET_PATH}")
    ) and TARGET_DISPATCH in url


def find_product_tab(driver):
    original = driver.current_window_handle
    candidates = []
    for handle in driver.window_handles:
        driver.switch_to.window(handle)
        url = driver.current_url
        if is_product_edit_url(url):
            candidates.append((handle, url))

    if not candidates:
        driver.switch_to.window(original)
        return None

    for handle, url in candidates:
        if handle == original:
            driver.switch_to.window(handle)
            return url
    driver.switch_to.window(candidates[0][0])
    return candidates[0][1]


def _field_status(value) -> str:
    if isinstance(value, dict):
        if value.get("ok"):
            preview = (value.get("preview") or "").replace("\n", " ")
            if len(preview) > 50:
                preview = preview[:50] + "…"
            return f"OK — {preview}" if preview else "OK"
        reason = value.get("reason") or "failed"
        return f"not found / skipped ({reason})"
    return "OK" if value else "not found / skipped"


def summarize_results(result: dict, ai_notes: str = "", image_note: str = "") -> str:
    lines = []
    simple_keys = [
        ("product_name", "Product name"),
        ("price", "Price"),
        ("full_description", "Full description"),
        ("promo_text", "Promo text"),
        ("page_title", "Page title"),
        ("meta_description", "META description"),
        ("meta_keywords", "META keywords"),
        ("seo_name", "SEO name"),
    ]
    for key, label in simple_keys:
        lines.append(f"{label}: {_field_status(result.get(key))}")

    feats = result.get("features") or {}
    lines.append(
        f"Features/specs: {feats.get('matched', 0)}/{feats.get('attempted', 0)} matched "
        f"(fields on page: {feats.get('fields_on_page', '?')})"
    )
    if feats.get("note"):
        lines.append(f"  ({feats['note']})")
    cats = result.get("categories") or {}
    lines.append(
        f"Categories: {cats.get('matched', 0)}/{cats.get('attempted', 0)} matched"
    )
    if cats.get("note"):
        lines.append(f"  ({cats['note']})")
    vids = result.get("videos") or {}
    lines.append(
        f"Videos: {vids.get('matched', 0)}/{vids.get('attempted', 0)} filled "
        f"(fields on page: {vids.get('fields_on_page', '?')})"
    )
    if vids.get("note"):
        lines.append(f"  ({vids['note']})")
    if image_note:
        lines.append(f"\nImages:\n{image_note}")
    if ai_notes:
        lines.append(f"\nAI note: {ai_notes}")
    lines.append("\nThe app did NOT press Save. Review and save manually in CS-Cart.")
    return "\n".join(lines)


class ScrollableFrame(tk.Frame):
    """Canvas + scrollbar with mouse-wheel support (including over children)."""

    def __init__(self, parent, bg=None, **kwargs):
        super().__init__(parent, bg=bg or C["bg"], **kwargs)
        self.canvas = tk.Canvas(self, bg=bg or C["bg"], highlightthickness=0, bd=0)
        self.vsb = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.inner = tk.Frame(self.canvas, bg=bg or C["bg"])
        self._win = self.canvas.create_window((0, 0), window=self.inner, anchor="nw")

        self.inner.bind("<Configure>", self._on_inner_configure)
        self.canvas.bind("<Configure>", self._on_canvas_configure)
        self.canvas.configure(yscrollcommand=self.vsb.set)

        self.canvas.pack(side="left", fill="both", expand=True)
        self.vsb.pack(side="right", fill="y")

        self._bind_wheel(self.canvas)
        self._bind_wheel(self.inner)

    def _on_inner_configure(self, _event=None):
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def _on_canvas_configure(self, event):
        self.canvas.itemconfigure(self._win, width=event.width)

    def _on_wheel(self, event):
        # Windows / Mac
        if getattr(event, "delta", 0):
            self.canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
        # Linux
        elif getattr(event, "num", None) == 4:
            self.canvas.yview_scroll(-1, "units")
        elif getattr(event, "num", None) == 5:
            self.canvas.yview_scroll(1, "units")

    def _bind_wheel(self, widget):
        widget.bind("<MouseWheel>", self._on_wheel, add="+")
        widget.bind("<Button-4>", self._on_wheel, add="+")
        widget.bind("<Button-5>", self._on_wheel, add="+")

    def bind_tree(self, widget=None):
        """Call after building children so wheel works over nested widgets."""
        root = widget or self.inner
        self._bind_wheel(root)
        for child in root.winfo_children():
            self.bind_tree(child)

    def clear(self):
        for child in self.inner.winfo_children():
            child.destroy()


class AcousticSmartFiller(tk.Tk):
    def __init__(self):
        super().__init__()
        ensure_config_exists()
        self.config_data = load_config()
        self._busy = False
        self._scraped = False
        self._product_url = ""
        self._product_title = ""
        self._page_context: dict = {}
        self._ai_raw: dict = {}
        self._image_results: list[dict] = []
        self._image_vars: dict[str, tk.BooleanVar] = {}
        self._main_image_var = tk.StringVar(value="")
        self._thumb_refs: list = []
        self._img_card_widgets: dict[str, dict] = {}  # id -> {frame, badge, …}
        self._logo_photo = None
        self._hover_bind_ids: list = []
        self._cat_options: list[dict] = []  # scraped category options from page
        self._cat_vars: dict[str, tk.BooleanVar] = {}  # key -> BooleanVar
        self._cat_key_to_opt: dict[str, dict] = {}
        self._cat_checked: set[str] = set()  # persist selection across filter
        self._cat_expanded: set[str] = set()  # group keys expanded to show subcategories
        self._feature_rows: list[dict] = []  # widget state per scraped feature
        self._feature_filter_var = tk.StringVar(value="")
        self._logged_in_user: dict = {}  # name/email of admin for ავტორი

        # Bulk queue (import from products.manage → scrape → review → fill)
        self._bulk_jobs: list[dict] = []
        self._bulk_active_id: str | None = None  # product_id currently in review panels
        self._bulk_cancel = False
        self._bulk_category_cache: list | None = None  # optional reuse across batch

        self.title("Acoustic Smart Filler")
        self.geometry("980x820")
        self.minsize(860, 700)
        try:
            # Windows: open maximized; other platforms fall back full screen geometry
            self.state("zoomed")
        except Exception:
            try:
                self.attributes("-zoomed", True)
            except Exception:
                pass
        self.configure(bg=C["bg"])
        # Full-window glass field (gradient + soft teal orbs)
        self._wallpaper = GradientBackground(self, bg=C["bg"])
        self._wallpaper.place(x=0, y=0, relwidth=1, relheight=1)
        # Force UI font that includes Georgian glyphs for entries/comboboxes
        try:
            self.option_add("*Font", "{Segoe UI} 10")
            self.option_add("*Entry.Font", "{Segoe UI} 10")
            self.option_add("*Text.Font", "{Segoe UI} 10")
            self.option_add("*TCombobox*Listbox.font", "{Segoe UI} 10")
        except Exception:
            pass

        self._style_ttk()
        self._build_shell()
        self._build_settings_bar()
        self._build_review()
        self._build_footer()

        self._set_buttons_idle()
        self.status.set(
            "Single: open product → Scrape · Bulk: products list → Import → Scrape queue → Fill"
        )

    def _style_ttk(self):
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except Exception:
            pass
        style.configure("TNotebook", background=C["bg"], borderwidth=0)
        style.configure(
            "TNotebook.Tab",
            background=C["glass"],
            foreground=C["muted"],
            padding=[16, 9],
            font=(FONT_UI, 10),
            borderwidth=0,
        )
        style.map(
            "TNotebook.Tab",
            background=[("selected", C["accent"]), ("active", C["glass_hi"])],
            foreground=[("selected", C["white"]), ("active", C["text"])],
        )
        style.configure(
            "Vertical.TScrollbar",
            troughcolor=C["bg_deep"],
            background=C["accent_dim"],
            bordercolor=C["bg"],
            arrowcolor=C["white"],
        )
        style.map(
            "Vertical.TScrollbar",
            background=[("active", C["accent"]), ("pressed", C["accent_hi"])],
        )
        style.configure(
            "Horizontal.TProgressbar",
            troughcolor=C["bg_deep"],
            background=C["accent"],
            bordercolor=C["bg_mid"],
            lightcolor=C["accent_hi"],
            darkcolor=C["accent_dim"],
            thickness=12,
        )
        style.configure(
            "Treeview",
            background=C["glass"],
            fieldbackground=C["glass"],
            foreground=C["text"],
            rowheight=30,
            font=(FONT_UI, 10),
            borderwidth=0,
        )
        style.configure(
            "Treeview.Heading",
            background=C["bg_mid"],
            foreground=C["accent_hi"],
            font=(FONT_UI, 9, "bold"),
            relief="flat",
        )
        style.map(
            "Treeview",
            background=[("selected", C["accent"])],
            foreground=[("selected", C["white"])],
        )
        style.configure(
            "TCombobox",
            font=(FONT_UI, 10),
            fieldbackground=C["input_bg"],
            background=C["glass"],
            foreground=C["text"],
            arrowcolor=C["accent"],
        )
        style.map(
            "TCombobox",
            fieldbackground=[("readonly", C["input_bg"])],
            foreground=[("readonly", C["text"])],
            selectbackground=[("readonly", C["accent"])],
        )
        try:
            self.option_add("*TCombobox*Listbox.font", "{Segoe UI} 10")
            self.option_add("*TCombobox*Listbox.background", C["glass"])
            self.option_add("*TCombobox*Listbox.foreground", C["text"])
            self.option_add("*TCombobox*Listbox.selectBackground", C["accent"])
            self.option_add("*TCombobox*Listbox.selectForeground", C["white"])
        except Exception:
            pass

    def _bind_hover(
        self,
        widget: tk.Widget,
        *,
        rest_bg: str,
        hover_bg: str,
        rest_fg: str | None = None,
        hover_fg: str | None = None,
    ) -> None:
        """Glass-button / chip hover (Enter/Leave)."""
        rest_fg = rest_fg or C["white"]
        hover_fg = hover_fg or C["white"]

        def on_enter(_e=None, w=widget):
            try:
                if str(w.cget("state")) == "disabled":
                    return
                w.configure(bg=hover_bg, fg=hover_fg)
            except Exception:
                pass

        def on_leave(_e=None, w=widget):
            try:
                if str(w.cget("state")) == "disabled":
                    return
                w.configure(bg=rest_bg, fg=rest_fg)
            except Exception:
                pass

        widget.bind("<Enter>", on_enter, add="+")
        widget.bind("<Leave>", on_leave, add="+")

    def _glass_button(
        self,
        parent,
        text: str,
        command,
        *,
        kind: str = "primary",
        font=None,
        padx: int = 18,
        pady: int = 8,
        state: str = "normal",
        width: int | None = None,
        height: int | None = None,
    ) -> GlassButton:
        """Rounded glass CTA with real hover / press faces (PIL)."""
        gkind = kind if kind in ("primary", "secondary", "danger", "ghost") else "primary"
        clean = str(text).strip()
        # Width from label + padding; slightly wider for Georgian UI chrome
        est = width if width is not None else max(96, int(len(clean) * 8.4) + padx * 2 + 8)
        h = height if height is not None else (38 if pady <= 6 else 42)
        bg = C["bg"]
        try:
            bg = parent.cget("bg") or bg
        except Exception:
            pass
        btn = GlassButton(
            parent,
            clean,
            command,
            kind=gkind,
            width=est,
            height=h,
            radius=14,
            font=font or (FONT_UI, 10, "bold"),
            state=state,
            bg=bg,
        )
        return btn

    def _load_logo_photo(self, max_h: int = 56):
        if self._logo_photo is not None:
            return self._logo_photo
        if not LOGO_PATH.is_file():
            return None
        try:
            from PIL import Image, ImageTk

            im = Image.open(LOGO_PATH)
            if im.mode != "RGBA":
                im = im.convert("RGBA")
            w, h = im.size
            if h > max_h:
                nw = max(1, int(w * (max_h / h)))
                im = im.resize((nw, max_h), Image.Resampling.LANCZOS)
            self._logo_photo = ImageTk.PhotoImage(im)
            return self._logo_photo
        except Exception:
            return None

    def _build_shell(self):
        # Header glass card
        header_panel = GlassPanel(self, radius=20, padx=16, pady=12, fill=(14, 42, 46, 175))
        header_panel.pack(fill="x", padx=14, pady=(12, 6))
        header = header_panel.body
        hbg = header.cget("bg")

        brand = tk.Frame(header, bg=hbg)
        brand.pack(side="left")

        logo = self._load_logo_photo(58)
        if logo is not None:
            tk.Label(brand, image=logo, bg=hbg, bd=0).pack(side="left", padx=(0, 14))

        titles = tk.Frame(brand, bg=hbg)
        titles.pack(side="left", fill="y")
        tk.Label(
            titles,
            text="Smart Filler",
            font=(FONT, 20, "bold"),
            bg=hbg,
            fg=C["white"],
        ).pack(anchor="w")
        tk.Label(
            titles,
            text="Scrape  ·  review  ·  fill  —  never auto-saves",
            font=(FONT_UI, 9),
            bg=hbg,
            fg=C["muted"],
        ).pack(anchor="w")

        steps = tk.Frame(header, bg=hbg)
        steps.pack(side="right", padx=(12, 0))
        for i, label in enumerate(("1  Scrape", "2  Review", "3  Fill"), start=1):
            kind = "primary" if i == 1 else "ghost"
            chip = GlassButton(
                steps,
                label,
                command=None,
                kind=kind,
                width=108,
                height=34,
                radius=12,
                font=(FONT_UI, 9, "bold"),
                state="disabled" if i == 1 else "normal",
                bg=hbg,
            )
            # Decorative chips: primary looks active; others hover-only
            if i == 1:
                chip.configure(state="normal")
                chip._command = None
            chip.pack(side="left", padx=4)

        # Glass action strip
        bar_panel = GlassPanel(self, radius=18, padx=12, pady=10, fill=(18, 56, 60, 180))
        bar_panel.pack(fill="x", padx=14, pady=(0, 6))
        bar_inner = bar_panel.body
        bbg = bar_inner.cget("bg")

        left = tk.Frame(bar_inner, bg=bbg)
        left.pack(side="left")
        self.scrape_btn = self._glass_button(
            left,
            "Scrape",
            self.start_scrape,
            kind="primary",
            font=(FONT_UI, 12, "bold"),
            padx=24,
            pady=10,
            width=130,
            height=44,
        )
        self.scrape_btn.pack(side="left", padx=(0, 12))
        tk.Label(
            left,
            text="Title · AI texts · specs · categories · video · photos",
            font=(FONT_UI, 9),
            bg=bbg,
            fg=C["muted"],
        ).pack(side="left")

        right = tk.Frame(bar_inner, bg=bbg)
        right.pack(side="right")
        self.fill_btn = self._glass_button(
            right,
            "Fill page",
            self.start_fill,
            kind="primary",
            font=(FONT_UI, 12, "bold"),
            padx=24,
            pady=10,
            width=140,
            height=44,
            state="disabled",
        )
        self.fill_btn.pack(side="right")
        tk.Label(
            right,
            text="After you check everything  →",
            font=(FONT_UI, 9),
            bg=bbg,
            fg=C["muted"],
        ).pack(side="right", padx=(0, 12))

        # Product strip
        strip_panel = GlassPanel(self, radius=16, padx=14, pady=10, fill=(16, 48, 52, 160))
        strip_panel.pack(fill="x", padx=14, pady=(0, 6))
        strip_inner = strip_panel.body
        sbg = strip_inner.cget("bg")
        tk.Label(
            strip_inner,
            text="PRODUCT",
            font=(FONT_UI, 8, "bold"),
            bg=sbg,
            fg=C["accent_hi"],
        ).pack(side="left")
        self.title_var = tk.StringVar(value="— not scraped yet —")
        tk.Label(
            strip_inner,
            textvariable=self.title_var,
            font=(FONT, 13, "bold"),
            bg=sbg,
            fg=C["text"],
            wraplength=720,
            justify="left",
        ).pack(side="left", padx=12)

    def _build_settings_bar(self):
        s_panel = GlassPanel(self, radius=16, padx=12, pady=10, fill=(14, 44, 48, 165))
        s_panel.pack(fill="x", padx=14, pady=(0, 6))
        inner = s_panel.body
        ibg = inner.cget("bg")

        def _field_lbl(text, col):
            tk.Label(
                inner, text=text, font=(FONT_UI, 8, "bold"), bg=ibg, fg=C["accent_hi"]
            ).grid(row=0, column=col, sticky="w")

        def _entry(var, col, width=20, show=None):
            e = tk.Entry(
                inner,
                textvariable=var,
                width=width,
                font=(FONT_UI, 9),
                show=show or "",
                bg=C["input_bg"],
                fg=C["text"],
                insertbackground=C["accent_hi"],
                relief="flat",
                highlightthickness=1,
                highlightbackground=C["input_border"],
                highlightcolor=C["accent"],
            )
            e.grid(row=1, column=col, sticky="ew", padx=(0, 10), pady=(2, 0))
            return e

        _field_lbl("API key", 0)
        self.api_key_var = tk.StringVar(value=self.config_data.get("openai_api_key", ""))
        _entry(self.api_key_var, 0, width=36, show="•")

        _field_lbl("GPT model", 1)
        saved_model = str(self.config_data.get("openai_model") or "gpt-4o-mini").strip()
        model_values = list(GPT_MODELS)
        if saved_model and saved_model not in model_values:
            model_values.insert(0, saved_model)
        self.model_var = tk.StringVar(value=saved_model if saved_model else "gpt-4o-mini")
        self.model_combo = ttk.Combobox(
            inner,
            textvariable=self.model_var,
            values=model_values,
            state="readonly",
            width=18,
            font=(FONT_UI, 9),
        )
        self.model_combo.grid(row=1, column=1, sticky="ew", padx=(0, 10), pady=(2, 0))
        if self.model_var.get() not in model_values:
            self.model_var.set(model_values[0])

        _field_lbl("Language (ქართული)", 2)
        self.lang_var = tk.StringVar(value="Georgian")
        lang_entry = tk.Entry(
            inner,
            textvariable=self.lang_var,
            width=12,
            font=(FONT_UI, 9),
            state="readonly",
            readonlybackground=C["input_bg"],
            fg=C["muted"],
            relief="flat",
            highlightthickness=1,
            highlightbackground=C["input_border"],
        )
        lang_entry.grid(row=1, column=2, sticky="ew", padx=(0, 10), pady=(2, 0))

        _field_lbl("Optional notes", 3)
        self.notes_var = tk.StringVar(value="")
        _entry(self.notes_var, 3, width=28)

        save_btn = self._glass_button(
            inner, "Save settings", self.save_key_to_config, kind="secondary", padx=12, pady=5, height=34
        )
        save_btn.grid(row=1, column=4, padx=(4, 0), pady=(2, 0))

        for i in range(4):
            inner.columnconfigure(i, weight=1)

    def _build_review(self):
        wrap = tk.Frame(self, bg=C["bg"])
        wrap.pack(fill="both", expand=True, padx=14, pady=(0, 6))

        tab_defs = [
            ("text", "Texts"),
            ("images", "Images"),
            ("cats", "Categories"),
            ("specs", "მახასიათებლები"),
            ("video", "Video"),
            ("bulk", "Bulk"),
        ]
        self.tab_bar = GlassTabBar(wrap, tab_defs, on_select=self._on_main_tab)
        self.tab_bar.pack(fill="x", pady=(0, 8))

        self.tab_host = tk.Frame(wrap, bg=C["bg"])
        self.tab_host.pack(fill="both", expand=True)

        self.tab_text = tk.Frame(self.tab_host, bg=C["bg"])
        self.tab_images = tk.Frame(self.tab_host, bg=C["bg"])
        self.tab_cats = tk.Frame(self.tab_host, bg=C["bg"])
        self.tab_specs = tk.Frame(self.tab_host, bg=C["bg"])
        self.tab_video = tk.Frame(self.tab_host, bg=C["bg"])
        self.tab_bulk = tk.Frame(self.tab_host, bg=C["bg"])

        self._tab_frames = {
            "text": self.tab_text,
            "images": self.tab_images,
            "cats": self.tab_cats,
            "specs": self.tab_specs,
            "video": self.tab_video,
            "bulk": self.tab_bulk,
        }
        self._tab_id_by_frame = {id(f): tid for tid, f in self._tab_frames.items()}

        self._build_text_tab()
        self._build_images_tab()
        self._build_cats_tab()
        self._build_specs_tab()
        self._build_video_tab()
        self._build_bulk_tab()

        # Show first tab content
        self._on_main_tab("text")

    def _on_main_tab(self, tid: str):
        for frame in self._tab_frames.values():
            frame.pack_forget()
        frame = self._tab_frames.get(tid)
        if frame is not None:
            frame.pack(fill="both", expand=True)

    def _select_tab(self, frame_or_id):
        """Select main review tab by frame widget or tab id string."""
        if isinstance(frame_or_id, str):
            tid = frame_or_id
        else:
            tid = self._tab_id_by_frame.get(id(frame_or_id), "text")
        if getattr(self, "tab_bar", None) is not None:
            self.tab_bar.select(tid)
        else:
            self._on_main_tab(tid)

    def _card(self, parent) -> GlassPanel:
        """Frosted rounded glass card (pack the panel; put children on `.body`)."""
        return GlassPanel(parent, radius=18, padx=14, pady=12)

    def _glass_entry(self, parent, textvariable=None, **kwargs) -> tk.Entry:
        opts = dict(
            font=(FONT_UI, 10),
            bg=C["input_bg"],
            fg=C["text"],
            insertbackground=C["accent_hi"],
            relief="flat",
            highlightthickness=1,
            highlightbackground=C["input_border"],
            highlightcolor=C["accent"],
            selectbackground=C["accent"],
            selectforeground=C["white"],
        )
        opts.update(kwargs)
        if textvariable is not None:
            opts["textvariable"] = textvariable
        return tk.Entry(parent, **opts)

    def _lbl(self, parent, text: str):
        try:
            bg = parent.cget("bg")
        except Exception:
            bg = C["glass"]
        return tk.Label(
            parent,
            text=text,
            font=(FONT_UI, 9, "bold"),
            bg=bg,
            fg=C["accent_hi"],
        )

    def _build_text_tab(self):
        scroll = ScrollableFrame(self.tab_text, bg=C["bg"])
        scroll.pack(fill="both", expand=True)
        root = scroll.inner

        panel = self._card(root)
        panel.pack(fill="both", expand=True, padx=4, pady=8)
        card = panel.body

        self.text_widgets: dict[str, tk.Text] = {}
        self._rich_keys = {"full_description", "promo_text"}

        # ---- Full description (rich) ----
        self._lbl(card, "Full description (formatted — not raw HTML)").pack(
            anchor="w", pady=(8, 2)
        )
        self._build_rich_editor(card, "full_description", height=14)

        # ---- Promo (rich, shorter) ----
        self._lbl(card, "Promo text").pack(anchor="w", pady=(10, 2))
        self._build_rich_editor(card, "promo_text", height=4)

        # ---- plain SEO fields ----
        for key, label, height in (
            ("page_title", "Page title (SEO)", 2),
            ("meta_description", "META description", 3),
            ("meta_keywords", "META keywords", 2),
            ("seo_name", "SEO name / slug", 1),
        ):
            self._lbl(card, label).pack(anchor="w", pady=(8, 2))
            w = scrolledtext.ScrolledText(
                card,
                height=height,
                font=(FONT_UI, 10),
                wrap="word",
                bg=C["input_bg"],
                fg=C["text"],
                relief="flat",
                highlightthickness=1,
                highlightbackground=C["glass_border"],
                insertbackground=C["accent_hi"],
            )
            w.pack(fill="x", pady=(0, 4))
            self.text_widgets[key] = w

        row = tk.Frame(card, bg=card.cget("bg"))
        row.pack(fill="x", pady=(8, 0))
        self._lbl(row, "Product name").grid(row=0, column=0, sticky="w")
        self._lbl(row, "Price (leave blank = keep page)").grid(
            row=0, column=1, sticky="w", padx=(12, 0)
        )
        self.name_var = tk.StringVar()
        self.price_var = tk.StringVar()
        self._glass_entry(row, self.name_var, width=40).grid(
            row=1, column=0, sticky="ew", pady=2
        )
        self._glass_entry(row, self.price_var, width=16).grid(
            row=1, column=1, sticky="w", padx=(12, 0), pady=2
        )
        row.columnconfigure(0, weight=1)

        tk.Label(
            card,
            text="Description & promo show bold / lists like a document. Fill still sends proper HTML to CS-Cart.",
            font=(FONT_UI, 8),
            bg=card.cget("bg"),
            fg=C["muted"],
        ).pack(anchor="w", pady=(10, 0))

        scroll.bind_tree()

    def _build_rich_editor(self, parent, key: str, height: int = 12):
        wrap = tk.Frame(
            parent,
            bg=C["input_bg"],
            highlightbackground=C["glass_border"],
            highlightthickness=1,
        )
        wrap.pack(fill="x", pady=(0, 4))

        toolbar = tk.Frame(wrap, bg=C["glass_hi"])
        toolbar.pack(fill="x")

        def tool_btn(text, cmd):
            b = tk.Button(
                toolbar,
                text=text,
                command=cmd,
                font=(FONT_UI, 9, "bold"),
                bg=C["glass"],
                fg=C["text"],
                relief="flat",
                padx=10,
                pady=4,
                cursor="hand2",
                activebackground=C["accent"],
                activeforeground=C["white"],
                highlightthickness=1,
                highlightbackground=C["glass_border"],
            )
            b.pack(side="left", padx=2, pady=3)
            self._bind_hover(
                b,
                rest_bg=C["glass"],
                hover_bg=C["accent"],
                rest_fg=C["text"],
                hover_fg=C["white"],
            )

        body = tk.Frame(wrap, bg=C["input_bg"])
        body.pack(fill="both", expand=True)
        text = tk.Text(
            body,
            height=height,
            font=(FONT_UI, 11),
            wrap="word",
            bg=C["input_bg"],
            fg=C["text"],
            relief="flat",
            insertbackground=C["accent_hi"],
            undo=True,
            selectbackground=C["accent"],
            selectforeground=C["white"],
        )
        vsb = ttk.Scrollbar(body, orient="vertical", command=text.yview)
        text.configure(yscrollcommand=vsb.set)
        text.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")
        configure_rich_text_tags(text, FONT_UI)

        def toggle(tag):
            try:
                a = text.index("sel.first")
                b = text.index("sel.last")
            except tk.TclError:
                return
            if tag in text.tag_names("sel.first"):
                text.tag_remove(tag, a, b)
            else:
                text.tag_add(tag, a, b)

        def insert_bullet():
            line_start = text.index("insert linestart")
            line = text.get(line_start, f"{line_start} lineend")
            if line.lstrip().startswith("•"):
                return
            text.insert(line_start, "• ")

        tool_btn("B", lambda: toggle("bold"))
        tool_btn("I", lambda: toggle("italic"))
        tool_btn("U", lambda: toggle("underline"))
        tool_btn("• List", insert_bullet)
        tk.Label(
            toolbar,
            text="Select text → B / I / U",
            font=(FONT_UI, 8),
            bg=C["glass_hi"],
            fg=C["muted"],
        ).pack(side="right", padx=8)

        self.text_widgets[key] = text

    def _build_images_tab(self):
        head = tk.Frame(self.tab_images, bg=C["bg"])
        head.pack(fill="x", padx=8, pady=(10, 4))
        tk.Label(
            head,
            text="Click a photo to select · double-click to set Main · teal border = selected",
            font=(FONT_UI, 10),
            bg=C["bg"],
            fg=C["text"],
        ).pack(side="left")
        self.img_count_var = tk.StringVar(value="")
        tk.Label(
            head, textvariable=self.img_count_var, font=(FONT_UI, 9), bg=C["bg"], fg=C["muted"]
        ).pack(side="right")

        qrow = tk.Frame(self.tab_images, bg=C["bg"])
        qrow.pack(fill="x", padx=8, pady=4)
        tk.Label(
            qrow, text="Search query", font=(FONT_UI, 8, "bold"), bg=C["bg"], fg=C["accent_hi"]
        ).pack(side="left")
        self.image_query_var = tk.StringVar()
        tk.Entry(
            qrow,
            textvariable=self.image_query_var,
            font=(FONT_UI, 10),
            bg=C["input_bg"],
            fg=C["text"],
            insertbackground=C["accent_hi"],
            relief="flat",
            highlightthickness=1,
            highlightbackground=C["input_border"],
            highlightcolor=C["accent"],
        ).pack(side="left", fill="x", expand=True, padx=8)
        self._glass_button(
            qrow, "Refresh images", self.start_image_refresh, kind="secondary", padx=12, pady=5
        ).pack(side="left")

        self.img_scroll = ScrollableFrame(self.tab_images, bg=C["image_bg"])
        self.img_scroll.pack(fill="both", expand=True, padx=8, pady=(4, 10))
        self.img_inner = self.img_scroll.inner

        self.img_hint = tk.Label(
            self.img_inner,
            text="After Scrape, photo previews appear here.\n"
            "Click to select (highlight) · double-click = Main image",
            font=(FONT, 12),
            bg=C["image_bg"],
            fg=C["muted"],
            pady=48,
        )
        self.img_hint.grid(row=0, column=0, columnspan=4, sticky="n")
        self.img_scroll.bind_tree()

    def _build_cats_tab(self):
        head = tk.Frame(self.tab_cats, bg=C["bg"])
        head.pack(fill="x", padx=12, pady=(12, 4))
        tk.Label(
            head,
            text="კატეგორიები — check subcategories only · mains with selected subs stay open",
            font=(FONT_UI, 10),
            bg=C["bg"],
            fg=C["text"],
        ).pack(side="left")
        self.cat_count_var = tk.StringVar(value="")
        tk.Label(
            head, textvariable=self.cat_count_var, font=(FONT_UI, 9), bg=C["bg"], fg=C["muted"]
        ).pack(side="right")

        filt = tk.Frame(self.tab_cats, bg=C["bg"])
        filt.pack(fill="x", padx=12, pady=4)
        tk.Label(filt, text="Filter", font=(FONT_UI, 8), bg=C["bg"], fg=C["muted"]).pack(
            side="left"
        )
        self.cat_filter_var = tk.StringVar()
        ent = tk.Entry(
            filt,
            textvariable=self.cat_filter_var,
            font=(FONT_UI, 10),
            bg=C["input_bg"],
            fg=C["text"],
            insertbackground=C["accent_hi"],
            relief="flat",
            highlightthickness=1,
            highlightbackground=C["input_border"],
            highlightcolor=C["accent"],
        )
        ent.pack(side="left", fill="x", expand=True, padx=8)
        ent.bind("<KeyRelease>", lambda _e: self._filter_categories())
        self._glass_button(
            filt, "Clear picks", self._clear_category_picks, kind="ghost", padx=12, pady=4, height=32
        ).pack(side="left")

        self.cats_scroll = ScrollableFrame(self.tab_cats, bg=C["bg"])
        self.cats_scroll.pack(fill="both", expand=True, padx=8, pady=4)
        self.cats_inner = self.cats_scroll.inner
        self._cats_placeholder()

    def _cats_placeholder(self):
        for w in self.cats_inner.winfo_children():
            w.destroy()
        tk.Label(
            self.cats_inner,
            text="Run Scrape to load every category checkbox/dropdown from CS-Cart.",
            font=(FONT, 11),
            bg=C["bg"],
            fg=C["muted"],
            pady=30,
        ).pack()
        self.cats_scroll.bind_tree()

    def _build_specs_tab(self):
        head = tk.Frame(self.tab_specs, bg=C["bg"])
        head.pack(fill="x", padx=12, pady=(12, 4))
        tk.Label(
            head,
            text="მახასიათებლები — real page dropdowns (single / multi / text).",
            font=(FONT_UI, 10),
            bg=C["bg"],
            fg=C["text"],
        ).pack(side="left")
        self.specs_count_var = tk.StringVar(value="")
        tk.Label(
            head, textvariable=self.specs_count_var, font=(FONT_UI, 9), bg=C["bg"], fg=C["muted"]
        ).pack(side="right")

        filt = tk.Frame(self.tab_specs, bg=C["bg"])
        filt.pack(fill="x", padx=12, pady=4)
        tk.Label(filt, text="Filter", font=(FONT_UI, 8), bg=C["bg"], fg=C["muted"]).pack(
            side="left"
        )
        ent = tk.Entry(
            filt,
            textvariable=self._feature_filter_var,
            font=(FONT_UI, 10),
            bg=C["input_bg"],
            fg=C["text"],
            insertbackground=C["accent_hi"],
            relief="flat",
            highlightthickness=1,
            highlightbackground=C["input_border"],
            highlightcolor=C["accent"],
        )
        ent.pack(side="left", fill="x", expand=True, padx=8)
        ent.bind("<KeyRelease>", lambda _e: self._filter_feature_rows())

        self.specs_scroll = ScrollableFrame(self.tab_specs, bg=C["bg"])
        self.specs_scroll.pack(fill="both", expand=True, padx=8, pady=(0, 10))
        self.specs_inner = self.specs_scroll.inner
        self._specs_placeholder()

    def _specs_placeholder(self):
        for w in self.specs_inner.winfo_children():
            w.destroy()
        self._feature_rows.clear()
        tk.Label(
            self.specs_inner,
            text="Run Scrape to load every feature control and its options from the page.",
            font=(FONT, 11),
            bg=C["bg"],
            fg=C["muted"],
            pady=30,
        ).pack()
        self.specs_scroll.bind_tree()

    def _build_video_tab(self):
        panel = self._card(self.tab_video)
        panel.pack(fill="both", expand=True, padx=12, pady=12)
        card = panel.body
        cbg = card.cget("bg")

        self._lbl(card, "Video title").pack(anchor="w")
        self.video_title_var = tk.StringVar()
        self._glass_entry(card, self.video_title_var, font=(FONT_UI, 11)).pack(
            fill="x", pady=(2, 10)
        )

        self._lbl(card, "Video URL (YouTube / Vimeo / search link)").pack(anchor="w")
        url_row = tk.Frame(card, bg=cbg)
        url_row.pack(fill="x", pady=(2, 10))
        self.video_url_var = tk.StringVar()
        self._glass_entry(url_row, self.video_url_var).pack(
            side="left", fill="x", expand=True, padx=(0, 8)
        )
        self._glass_button(
            url_row, "Preview in browser", self._preview_video, kind="primary", padx=12, pady=5
        ).pack(side="left")

        self._lbl(card, "Description").pack(anchor="w")
        self.video_desc = scrolledtext.ScrolledText(
            card,
            height=5,
            font=(FONT_UI, 10),
            wrap="word",
            bg=C["input_bg"],
            fg=C["text"],
            relief="flat",
            highlightthickness=1,
            highlightbackground=C["glass_border"],
            insertbackground=C["accent_hi"],
        )
        self.video_desc.pack(fill="x", pady=(2, 10))

        self._lbl(card, "Provider").pack(anchor="w")
        self.video_provider_var = tk.StringVar(value="youtube")
        self._glass_entry(card, self.video_provider_var, width=20).pack(
            anchor="w", pady=(2, 10)
        )

        self.video_preview_frame = tk.Frame(
            card, bg=C["image_bg"], height=120, highlightthickness=1, highlightbackground=C["glass_border"]
        )
        self.video_preview_frame.pack(fill="x", pady=8)
        self.video_preview_frame.pack_propagate(False)
        self.video_preview_lbl = tk.Label(
            self.video_preview_frame,
            text="Preview opens in your browser (YouTube / search page).",
            font=(FONT, 11),
            bg=C["image_bg"],
            fg=C["muted"],
        )
        self.video_preview_lbl.pack(expand=True)

        tk.Label(
            card,
            text="YouTube watch URL (watch?v=… / youtu.be/…) — Scrape resolves a real video from the product title.",
            font=(FONT_UI, 8),
            bg=cbg,
            fg=C["muted"],
            wraplength=700,
            justify="left",
        ).pack(anchor="w", pady=(6, 0))

    def _build_bulk_tab(self):
        wrap = tk.Frame(self.tab_bulk, bg=C["bg"])
        wrap.pack(fill="both", expand=True, padx=12, pady=12)

        intro_p = GlassPanel(wrap, radius=16, padx=12, pady=10, fill=(16, 50, 54, 170))
        intro_p.pack(fill="x", pady=(0, 10))
        tk.Label(
            intro_p.body,
            text=(
                "Bulk: open Products → Products (or a category list) in debug Chrome, "
                "tick products, then Import. Scrape queue → Approve → Fill approved "
                "(fills ONE product, waits for Save, then the next — optional Auto-Save)."
            ),
            font=(FONT_UI, 9),
            bg=intro_p.body.cget("bg"),
            fg=C["text"],
            wraplength=900,
            justify="left",
        ).pack(anchor="w")

        mode_row = tk.Frame(wrap, bg=C["bg"])
        mode_row.pack(fill="x", pady=(0, 8))
        self.bulk_import_mode = tk.StringVar(value="selected")
        tk.Label(
            mode_row, text="Import:", font=(FONT_UI, 9, "bold"), bg=C["bg"], fg=C["muted"]
        ).pack(side="left")
        tk.Radiobutton(
            mode_row,
            text="Selected (ticked) only",
            variable=self.bulk_import_mode,
            value="selected",
            bg=C["bg"],
            fg=C["text"],
            activebackground=C["bg"],
            activeforeground=C["white"],
            selectcolor=C["accent_dim"],
            font=(FONT_UI, 9),
            highlightthickness=0,
        ).pack(side="left", padx=(10, 6))
        tk.Radiobutton(
            mode_row,
            text="All products visible on this list page",
            variable=self.bulk_import_mode,
            value="all_page",
            bg=C["bg"],
            fg=C["text"],
            activebackground=C["bg"],
            activeforeground=C["white"],
            selectcolor=C["accent_dim"],
            font=(FONT_UI, 9),
            highlightthickness=0,
        ).pack(side="left", padx=6)

        btn_row = tk.Frame(wrap, bg=C["bg"])
        btn_row.pack(fill="x", pady=(0, 8))

        self.bulk_import_btn = self._glass_button(
            btn_row, "Import from Chrome list", self.bulk_import_from_page, kind="primary", padx=12, pady=7
        )
        self.bulk_import_btn.pack(side="left", padx=(0, 6))
        self.bulk_scrape_btn = self._glass_button(
            btn_row, "Scrape queue", self.bulk_start_scrape, kind="primary", padx=12, pady=7
        )
        self.bulk_scrape_btn.pack(side="left", padx=4)
        self.bulk_fill_btn = self._glass_button(
            btn_row, "Fill approved", self.bulk_start_fill, kind="primary", padx=12, pady=7
        )
        self.bulk_fill_btn.pack(side="left", padx=4)
        self.bulk_approve_btn = self._glass_button(
            btn_row, "Approve all ready", self.bulk_approve_all_ready, kind="secondary", padx=12, pady=7
        )
        self.bulk_approve_btn.pack(side="left", padx=4)
        self.bulk_cancel_btn = self._glass_button(
            btn_row, "Cancel", self.bulk_cancel_work, kind="danger", padx=12, pady=7, state="disabled"
        )
        self.bulk_cancel_btn.pack(side="left", padx=4)
        self.bulk_remove_btn = self._glass_button(
            btn_row, "Remove selected", self.bulk_remove_selected, kind="ghost", padx=12, pady=7
        )
        self.bulk_remove_btn.pack(side="left", padx=4)
        self.bulk_clear_btn = self._glass_button(
            btn_row, "Clear done", self.bulk_clear_done, kind="ghost", padx=12, pady=7
        )
        self.bulk_clear_btn.pack(side="left", padx=4)

        opt_row = tk.Frame(wrap, bg=C["bg"])
        opt_row.pack(fill="x", pady=(10, 4))
        self.bulk_auto_save_var = tk.BooleanVar(value=True)
        tk.Checkbutton(
            opt_row,
            text="Auto-Save after each fill (required so data is not lost — uncheck to Save yourself)",
            variable=self.bulk_auto_save_var,
            bg=C["bg"],
            fg=C["text"],
            activebackground=C["bg"],
            activeforeground=C["white"],
            selectcolor=C["accent_dim"],
            font=(FONT_UI, 9),
            highlightthickness=0,
            bd=0,
        ).pack(side="left")

        self.bulk_count_var = tk.StringVar(value="0 products in queue")
        tk.Label(
            wrap,
            textvariable=self.bulk_count_var,
            font=(FONT_UI, 9),
            bg=C["bg"],
            fg=C["muted"],
        ).pack(anchor="w", pady=(0, 4))

        tree_panel = GlassPanel(wrap, radius=16, padx=8, pady=8, fill=(12, 40, 44, 180))
        tree_panel.pack(fill="both", expand=True)
        tree_frame = tree_panel.body

        cols = ("pick", "id", "name", "status", "approved", "error")
        self.bulk_tree = ttk.Treeview(
            tree_frame,
            columns=cols,
            show="headings",
            selectmode="extended",
            height=14,
        )
        headings = {
            "pick": "✓",
            "id": "ID",
            "name": "Product name",
            "status": "Status",
            "approved": "Approve",
            "error": "Note / error",
        }
        widths = {"pick": 36, "id": 70, "name": 320, "status": 100, "approved": 70, "error": 220}
        for c in cols:
            self.bulk_tree.heading(c, text=headings[c])
            self.bulk_tree.column(c, width=widths[c], minwidth=30, anchor="w")
        scroll = ttk.Scrollbar(tree_frame, orient="vertical", command=self.bulk_tree.yview)
        self.bulk_tree.configure(yscrollcommand=scroll.set)
        self.bulk_tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        self.bulk_tree.bind("<<TreeviewSelect>>", self._on_bulk_tree_select)
        self.bulk_tree.bind("<Double-1>", self._on_bulk_tree_double)
        self.bulk_tree.bind("<Button-1>", self._on_bulk_tree_click, add="+")

        tip = tk.Label(
            wrap,
            text=(
                "Click ✓ column to toggle include for scrape/fill. "
                "Double-click a row to load it into Texts / Specs for review. "
                "Approve = Yes for fill."
            ),
            font=(FONT_UI, 8),
            bg=C["bg"],
            fg=C["muted"],
            wraplength=900,
            justify="left",
        )
        tip.pack(anchor="w", pady=(8, 0))

    def _build_footer(self):
        progress_wrap = GlassPanel(self, radius=14, padx=8, pady=6, fill=(10, 32, 36, 200))
        progress_wrap.pack(fill="x", side="bottom", padx=14, pady=(0, 10))
        pw = progress_wrap.body
        pbg = pw.cget("bg")

        self.progress_frame = tk.Frame(pw, bg=pbg)
        self.progress_label = tk.StringVar(value="")
        tk.Label(
            self.progress_frame,
            textvariable=self.progress_label,
            font=(FONT_UI, 9),
            bg=pbg,
            fg=C["white"],
            anchor="w",
        ).pack(fill="x", padx=6, pady=(4, 2))

        bar_row = tk.Frame(self.progress_frame, bg=pbg)
        bar_row.pack(fill="x", padx=6, pady=(0, 6))
        self.progress_bar = ttk.Progressbar(
            bar_row, mode="determinate", maximum=100, length=400
        )
        self.progress_bar.pack(side="left", fill="x", expand=True)
        self.progress_pct = tk.StringVar(value="")
        tk.Label(
            bar_row,
            textvariable=self.progress_pct,
            font=(FONT_UI, 9, "bold"),
            bg=pbg,
            fg=C["accent_hi"],
            width=5,
        ).pack(side="right", padx=(8, 0))

        self.footer_frame = tk.Frame(pw, bg=pbg)
        self.footer_frame.pack(fill="x")
        self.status = tk.StringVar()
        tk.Label(
            self.footer_frame,
            textvariable=self.status,
            font=(FONT_UI, 9),
            bg=pbg,
            fg=C["muted"],
            wraplength=920,
            justify="left",
        ).pack(side="left", padx=6, pady=6)

    # ---------- helpers ----------
    def save_key_to_config(self):
        self.config_data["openai_api_key"] = self.api_key_var.get().strip()
        self.config_data["openai_model"] = self.model_var.get().strip() or "gpt-4o-mini"
        self.config_data["content_language"] = "Georgian"
        save_config(self.config_data)
        glass_info(self, "Saved", "Settings written to config.json.")

    def _cfg(self) -> dict:
        cfg = dict(self.config_data)
        cfg["openai_api_key"] = self.api_key_var.get().strip()
        cfg["openai_model"] = self.model_var.get().strip() or "gpt-4o-mini"
        cfg["content_language"] = "Georgian"
        try:
            self.lang_var.set("Georgian")
        except Exception:
            pass
        return cfg

    def _require_ready(self, need_key: bool = True) -> dict | None:
        cfg = self._cfg()
        if need_key and not has_usable_api_key(cfg):
            glass_error(self, 
                "API key missing",
                "Enter your OpenAI API key in the settings strip, then Save settings.",
            )
            return None
        if not chrome_debug_available():
            glass_error(self, 
                "Chrome not connected",
                "Run START.bat (or START_CHROME.bat), log in, open a product edit page.",
            )
            return None
        return cfg

    def _set_buttons_idle(self):
        self.scrape_btn.configure(state="normal")
        self.fill_btn.configure(state="normal" if self._scraped else "disabled")
        for name in (
            "bulk_import_btn",
            "bulk_scrape_btn",
            "bulk_fill_btn",
            "bulk_cancel_btn",
            "bulk_remove_btn",
            "bulk_clear_btn",
            "bulk_approve_btn",
        ):
            btn = getattr(self, name, None)
            if not btn:
                continue
            if name == "bulk_cancel_btn":
                btn.configure(state="disabled")
            else:
                btn.configure(state="normal")

    def _set_busy(self, busy: bool, status: str | None = None):
        self._busy = busy
        state = "disabled" if busy else "normal"
        self.scrape_btn.configure(state=state)
        if busy:
            self.fill_btn.configure(state="disabled")
            for name in (
                "bulk_import_btn",
                "bulk_scrape_btn",
                "bulk_fill_btn",
                "bulk_remove_btn",
                "bulk_clear_btn",
                "bulk_approve_btn",
            ):
                btn = getattr(self, name, None)
                if btn:
                    btn.configure(state="disabled")
            if getattr(self, "bulk_cancel_btn", None):
                self.bulk_cancel_btn.configure(state="normal")
        else:
            self._set_buttons_idle()
        if status is not None:
            self.status.set(status)

    def _set_status(self, text: str):
        self.after(0, lambda: self.status.set(text))

    def _clear_busy(self):
        self.after(0, lambda: self._set_busy(False))

    def _set_text(self, key: str, value: str):
        w = self.text_widgets.get(key)
        if not w:
            return
        if key in getattr(self, "_rich_keys", set()):
            apply_html_to_text_widget(w, value or "")
            return
        w.delete("1.0", "end")
        if value:
            w.insert("1.0", value)

    def _get_text(self, key: str) -> str:
        w = self.text_widgets.get(key)
        if not w:
            return ""
        if key in getattr(self, "_rich_keys", set()):
            return text_widget_to_html(w).strip()
        return w.get("1.0", "end").strip()

    def _set_progress(self, pct: int, label: str):
        pct = max(0, min(100, int(pct)))

        def _ui():
            if not self.progress_frame.winfo_ismapped():
                self.progress_frame.pack(fill="x", before=self.footer_frame)
            self.progress_bar["value"] = pct
            self.progress_pct.set(f"{pct}%")
            self.progress_label.set(label)
            self.status.set(label)

        self.after(0, _ui)

    # ---------- Scrape (main) ----------
    def start_scrape(self):
        if self._busy:
            return
        cfg = self._require_ready(need_key=True)
        if not cfg:
            return
        self._set_busy(True, "Scraping…")
        self._set_progress(2, "Starting scrape…")
        notes = self.notes_var.get().strip()
        threading.Thread(target=self._scrape_worker, args=(cfg, notes), daemon=True).start()

    def _scrape_worker(self, cfg: dict, extra_notes: str):
        try:
            self._set_progress(6, "Checking local Chrome (127.0.0.1:9222)…")
            driver = connect_to_chrome(
                status_cb=lambda m: self._set_progress(10, m[:72])
            )
            self._set_progress(14, "Finding product edit tab…")
            url = find_product_tab(driver)
            if not url:
                raise RuntimeError(
                    "No product edit tab found (dispatch=products.update).\n"
                    "Open a product in the debug Chrome window."
                )
            self._product_url = url

            self._set_progress(22, "Scanning form · Categories…")
            page_context = scan_product_page(
                driver,
                product_url=url,
                progress_cb=lambda m: self._set_progress(24, str(m)[:72]),
            )
            self._page_context = page_context
            self._logged_in_user = (
                page_context.get("logged_in_user")
                if isinstance(page_context.get("logged_in_user"), dict)
                else {}
            )
            product_title = str(page_context.get("product_name") or "").strip()
            if not product_title:
                raise RuntimeError(
                    "Product Name is empty on the CS-Cart form.\n"
                    "Enter the product title first, then Scrape."
                )
            self._product_title = product_title

            nc_pre = len(page_context.get("available_category_options") or [])
            nf_pre = len(page_context.get("available_features") or [])
            self._set_progress(
                36,
                f"Loaded {nc_pre} categories · {nf_pre} features from page…",
            )

            # AI text + image hunt run in parallel (both only need title + page_context)
            self._set_progress(40, "AI write · image search (parallel)…")

            def _run_ai() -> dict:
                return generate_product_fields(
                    api_key=cfg["openai_api_key"],
                    model=cfg["openai_model"],
                    content_language=cfg["content_language"],
                    page_context=page_context,
                    product_title=product_title,
                    extra_notes=extra_notes,
                )

            def _run_images() -> tuple[str, list]:
                q = craft_image_search_query(
                    api_key=cfg["openai_api_key"],
                    model=cfg["openai_model"],
                    product_title=product_title,
                )
                results = search_product_images(
                    q,
                    max_results=14,
                    google_api_key=str(cfg.get("google_api_key") or ""),
                    google_cse_id=str(cfg.get("google_cse_id") or ""),
                    backend=str(cfg.get("image_search_backend") or "auto"),
                )
                prepared_local = []
                for item in results:
                    thumb_path = download_thumbnail_preview(
                        item.get("thumbnail") or item.get("url"),
                        item["id"],
                        alternate_urls=[item.get("url") or "", item.get("thumbnail") or ""],
                        page_url=item.get("page_url") or item.get("source") or None,
                    )
                    row = dict(item)
                    row["thumb_path"] = str(thumb_path) if thumb_path else ""
                    prepared_local.append(row)
                return q, prepared_local

            from concurrent.futures import ThreadPoolExecutor, as_completed

            ai_data: dict = {}
            query = ""
            prepared: list = []
            with ThreadPoolExecutor(max_workers=2) as pool:
                fut_ai = pool.submit(_run_ai)
                fut_img = pool.submit(_run_images)
                for fut in as_completed([fut_ai, fut_img]):
                    if fut is fut_ai:
                        ai_data = fut.result()
                        self._set_progress(70, "AI text ready…")
                    else:
                        query, prepared = fut.result()
                        self._set_progress(70, f"Photos ready ({len(prepared)})…")

            self._ai_raw = ai_data
            self._image_results = prepared

            self._set_progress(94, "Filling review panels…")

            def _apply_ui():
                self._apply_scrape_to_ui(ai_data, product_title, query, prepared)
                self._scraped = True
                nf = len((self._page_context or {}).get("available_features") or [])
                nc = len((self._page_context or {}).get("available_category_options") or [])
                enf = (self._page_context or {}).get("enrich_features") or {}
                human_n = int(enf.get("human_labels") or 0) if isinstance(enf, dict) else 0
                self.progress_bar["value"] = 100
                self.progress_pct.set("100%")
                self.progress_label.set("Scrape complete")
                human_note = f" · {human_n} named options" if human_n else ""
                self._set_busy(
                    False,
                    f"Review tabs · {nc} categories · {nf} specs{human_note} · then Fill page",
                )
                self._select_tab("text")
                # hide bar shortly after finish
                self.after(1200, lambda: self.progress_frame.pack_forget())

            self.after(0, _apply_ui)
        except Exception as exc:
            msg = str(exc)
            self._set_progress(0, "Scrape failed")
            self.after(0, lambda: self.progress_frame.pack_forget())
            self._set_status("Scrape failed.")
            self.after(0, lambda m=msg: glass_error(self, "Scrape", m))
            self.after(0, lambda: self._set_busy(False))

    def _apply_scrape_to_ui(
        self, ai: dict, title: str, image_query: str, images: list[dict]
    ):
        self.title_var.set(title)
        self.name_var.set(str(ai.get("product_name") or title))
        price = ai.get("price")
        self.price_var.set("" if price is None else str(price))
        self._set_text("full_description", str(ai.get("full_description") or ""))
        self._set_text("promo_text", str(ai.get("promo_text") or ""))
        self._set_text("page_title", str(ai.get("page_title") or ""))
        self._set_text("meta_description", str(ai.get("meta_description") or ""))
        self._set_text("meta_keywords", str(ai.get("meta_keywords") or ""))
        self._set_text("seo_name", str(ai.get("seo_name") or ""))

        self.image_query_var.set(image_query)
        self._render_image_grid(images)

        # Categories + features: prefer real page options from scrape
        self._render_categories_from_page(
            self._page_context or {},
            ai_hints=list(ai.get("categories") or []),
        )
        self._render_features_from_page(self._page_context or {}, ai)

        videos = ai.get("videos") or []
        v0 = videos[0] if videos and isinstance(videos[0], dict) else {}
        self.video_title_var.set(str(v0.get("title") or f"{title} — ვიდეო"))
        raw_url = str(v0.get("url") or "").strip()
        if not raw_url or is_youtube_search_url(raw_url):
            raw_url = resolve_youtube_watch_url(title, existing_url=raw_url)
        else:
            raw_url = resolve_youtube_watch_url(title, existing_url=raw_url)
        self.video_url_var.set(raw_url)
        self.video_provider_var.set(str(v0.get("provider") or "youtube"))
        self.video_desc.delete("1.0", "end")
        self.video_desc.insert("1.0", str(v0.get("description") or ""))
        self._update_video_preview_lbl()

    # ---------- Images ----------
    def _style_image_card(self, img_id: str) -> None:
        meta = self._img_card_widgets.get(img_id)
        if not meta:
            return
        badge = meta.get("badge")
        img_lbl = meta.get("img_lbl")
        selected = bool(self._image_vars.get(img_id) and self._image_vars[img_id].get())
        is_main = self._main_image_var.get() == img_id
        if is_main:
            if badge is not None:
                badge.configure(text=" MAIN ", bg=C["accent"], fg=C["white"])
        elif selected:
            if badge is not None:
                badge.configure(text=" SELECTED ", bg=C["accent_dim"], fg=C["white"])
        else:
            if badge is not None:
                badge.configure(text="  click  ", bg=C["glass_hi"], fg=C["muted"])

        thumb_path = meta.get("thumb_path") or ""
        if not img_lbl or not thumb_path or not Path(thumb_path).is_file():
            return
        try:
            from PIL import Image, ImageTk

            im = Image.open(thumb_path)
            composed = rounded_image_card(
                im, size=160, radius=16, selected=selected, is_main=is_main
            )
            photo = ImageTk.PhotoImage(composed)
            self._thumb_refs.append(photo)
            meta["photo"] = photo
            img_lbl.configure(image=photo)
        except Exception:
            pass

    def _toggle_image_select(self, img_id: str) -> None:
        var = self._image_vars.get(img_id)
        if not var:
            return
        var.set(not var.get())
        if var.get():
            # First selected becomes main if none
            if not self._main_image_var.get():
                self._main_image_var.set(img_id)
        else:
            if self._main_image_var.get() == img_id:
                # Promote next selected, or clear
                nxt = next(
                    (
                        i
                        for i, v in self._image_vars.items()
                        if i != img_id and v.get()
                    ),
                    "",
                )
                self._main_image_var.set(nxt)
        for iid in self._image_vars:
            self._style_image_card(iid)

    def _set_image_main(self, img_id: str) -> None:
        var = self._image_vars.get(img_id)
        if var is not None:
            var.set(True)
        self._main_image_var.set(img_id)
        for iid in self._image_vars:
            self._style_image_card(iid)

    def _render_image_grid(self, items: list[dict]):
        for child in self.img_inner.winfo_children():
            child.destroy()
        self._image_vars.clear()
        self._thumb_refs.clear()
        self._img_card_widgets.clear()
        self._main_image_var.set("")

        if not items:
            tk.Label(
                self.img_inner,
                text="No images found. Edit query and click Refresh images.",
                bg=C["image_bg"],
                fg=C["muted"],
                font=(FONT, 11),
                pady=40,
            ).grid(row=0, column=0)
            self.img_count_var.set("0 images")
            self.img_scroll.bind_tree()
            return

        try:
            from PIL import Image, ImageTk
        except ImportError:
            glass_error(self, "Pillow missing", "Run SETUP.bat to install Pillow.")
            return

        cols = 4
        for i, item in enumerate(items):
            r, c = divmod(i, cols)
            frame = tk.Frame(self.img_inner, bg=C["image_bg"], cursor="hand2")
            frame.grid(row=r, column=c, padx=8, pady=8, sticky="n")

            img_id = item["id"]
            var = tk.BooleanVar(value=False)
            self._image_vars[img_id] = var

            badge = tk.Label(
                frame,
                text="  click  ",
                font=(FONT_UI, 8, "bold"),
                bg=C["glass_hi"],
                fg=C["muted"],
                padx=6,
                pady=2,
            )
            badge.pack(anchor="e", pady=(0, 4))

            thumb_path = item.get("thumb_path") or ""
            img_lbl = None
            if thumb_path and Path(thumb_path).is_file():
                try:
                    im = Image.open(thumb_path)
                    composed = rounded_image_card(
                        im, size=160, radius=16, selected=False, is_main=False
                    )
                    photo = ImageTk.PhotoImage(composed)
                    self._thumb_refs.append(photo)
                    img_lbl = tk.Label(frame, image=photo, bg=C["image_bg"], cursor="hand2", bd=0)
                    img_lbl.pack()
                except Exception:
                    img_lbl = tk.Label(
                        frame, text="(preview failed)", bg=C["image_bg"], fg=C["muted"]
                    )
                    img_lbl.pack()
            else:
                img_lbl = tk.Label(
                    frame, text="(no preview)", bg=C["image_bg"], fg=C["muted"]
                )
                img_lbl.pack()

            title = (item.get("title") or "")[:42]
            title_lbl = tk.Label(
                frame,
                text=title or img_id[:18],
                bg=C["image_bg"],
                font=(FONT_UI, 8),
                wraplength=160,
                fg=C["muted"],
            )
            title_lbl.pack(pady=4)

            tip = tk.Label(
                frame,
                text="click · select   ·   double · main",
                bg=C["image_bg"],
                font=(FONT_UI, 7),
                fg=C["accent_dim"],
            )
            tip.pack()

            self._img_card_widgets[img_id] = {
                "frame": frame,
                "badge": badge,
                "img_lbl": img_lbl,
                "thumb_path": thumb_path,
            }

            def _click(_e=None, iid=img_id):
                self._toggle_image_select(iid)

            def _dbl(_e=None, iid=img_id):
                self._set_image_main(iid)

            for w in (frame, badge, img_lbl, title_lbl, tip):
                if w is None:
                    continue
                w.bind("<Button-1>", _click, add="+")
                w.bind("<Double-Button-1>", _dbl, add="+")

            self._style_image_card(img_id)

        self.img_count_var.set(
            f"{len(items)} images · click select · double-click Main · wheel scroll"
        )
        self.img_scroll.bind_tree()
        self.img_scroll._on_inner_configure()

    def _on_main_picked(self, image_id: str):
        self._set_image_main(image_id)

    def start_image_refresh(self):
        if self._busy:
            return
        cfg = self._require_ready(need_key=True)
        if not cfg:
            return
        q = self.image_query_var.get().strip() or self._product_title
        if not q:
            glass_warn(self, "Images", "Enter a search query or Scrape first.")
            return
        self._set_busy(True, "Refreshing images…")
        threading.Thread(target=self._image_refresh_worker, args=(cfg, q), daemon=True).start()

    def _image_refresh_worker(self, cfg: dict, query: str):
        try:
            results = search_product_images(
                query,
                max_results=18,
                google_api_key=str(cfg.get("google_api_key") or ""),
                google_cse_id=str(cfg.get("google_cse_id") or ""),
                backend=str(cfg.get("image_search_backend") or "auto"),
            )
            prepared = []
            for item in results:
                thumb_path = download_thumbnail_preview(
                    item.get("thumbnail") or item.get("url"),
                    item["id"],
                    alternate_urls=[item.get("url") or "", item.get("thumbnail") or ""],
                    page_url=item.get("page_url") or item.get("source") or None,
                )
                item = dict(item)
                item["thumb_path"] = str(thumb_path) if thumb_path else ""
                prepared.append(item)
            self._image_results = prepared
            self.after(0, lambda: self._render_image_grid(prepared))
            self._set_status(f"Loaded {len(prepared)} images. Select + mark Main.")
        except Exception as exc:
            msg = str(exc)
            self.after(0, lambda m=msg: glass_error(self, "Images", m))
        finally:
            self._clear_busy()

    # ---------- Categories ----------
    @staticmethod
    def _norm(s: str) -> str:
        return " ".join(str(s or "").lower().split())

    def _cat_key(self, opt: dict) -> str:
        return f"{opt.get('value','')}|{opt.get('label','')}|{opt.get('field_name','')}"

    def _category_ancestor_chain(self, opt: dict) -> list[str]:
        """
        Root → … → parent names for a category (not including opt's own label).
        Uses parent_id, multi-segment path, or CS-Cart 'name\\nparent' path style.
        """
        label = str(opt.get("label") or "").strip()
        ln = self._norm(label)
        path = str(opt.get("path") or "").strip()
        parent_id = str(opt.get("parent_id") or "").strip()

        if parent_id and parent_id not in ("0", "false", "None"):
            by_id = {
                str(o.get("value") or o.get("id") or ""): o for o in self._cat_options
            }
            chain: list[str] = []
            seen_ids: set[str] = set()
            cur = parent_id
            while cur and cur in by_id and cur not in seen_ids:
                seen_ids.add(cur)
                p = by_id[cur]
                plab = str(p.get("label") or "").strip()
                if plab:
                    chain.append(plab)
                cur = str(p.get("parent_id") or "").strip()
                if cur in ("0", "false", "None"):
                    break
            chain.reverse()
            if chain:
                return chain

        if not path or self._norm(path) == ln:
            return []

        # Split path separators used by CS-Cart / Select2
        parts = [
            p.strip()
            for p in re.split(r"\s*(?:/|›|»|>|\|)\s*", path.replace("\n", " / "))
            if p and p.strip()
        ]
        if not parts:
            return []

        # Leaf-first: "Child / Parent / Grandparent" (Select2 multiline)
        if self._norm(parts[0]) == ln:
            ancestors = parts[1:]
            return list(reversed(ancestors))
        # Root-first: "Grandparent / Parent / Child"
        if self._norm(parts[-1]) == ln:
            return parts[:-1]
        # Path is only parents (doesn't include self)
        if ln and ln not in {self._norm(p) for p in parts}:
            # Prefer treating as parent path root-first
            return parts
        return []

    def _category_tree_groups(self, opts: list[dict], filt: str) -> list[dict]:
        """
        Main categories as groups; subcategories hang under parent_id
        (same tree as categories.manage: გიტარა → აკუსტიკური, ელექტრო, …).
        """
        by_id: dict[str, dict] = {}
        by_label: dict[str, dict] = {}
        for o in opts:
            vid = str(o.get("value") or o.get("id") or "").strip()
            lab = str(o.get("label") or "").strip()
            if vid:
                by_id[vid] = o
            if lab:
                by_label[self._norm(lab)] = o

        # parent_id → children
        children_of: dict[str, list[dict]] = {}
        roots: list[dict] = []
        hanging: list[dict] = []  # parent_id set but parent missing from catalog

        for o in opts:
            pid = str(o.get("parent_id") or "").strip()
            vid = str(o.get("value") or o.get("id") or "").strip()
            if pid and pid in by_id and pid != vid:
                children_of.setdefault(pid, []).append(o)
            elif pid and pid not in by_id and pid != vid:
                hanging.append(o)
            else:
                roots.append(o)

        def _match_opt(o: dict) -> bool:
            if not filt:
                return True
            blob = " ".join(
                [
                    str(o.get("label") or ""),
                    str(o.get("path") or ""),
                    str(o.get("value") or ""),
                ]
            )
            return filt in self._norm(blob)

        ordered: list[dict] = []
        used_child_keys: set[str] = set()

        for root in sorted(roots, key=lambda o: self._norm(str(o.get("label") or ""))):
            rid = str(root.get("value") or root.get("id") or "")
            kids = list(children_of.get(rid) or [])
            # All descendants one level for UI (direct children only — matches manage expand)
            kids = sorted(kids, key=lambda o: self._norm(str(o.get("label") or "")))
            for k in kids:
                used_child_keys.add(self._cat_key(k))

            if filt:
                root_hit = _match_opt(root)
                kids_hit = [c for c in kids if _match_opt(c)]
                if not root_hit and not kids_hit:
                    continue
                if not root_hit and kids_hit:
                    kids = kids_hit
            title = str(root.get("label") or rid or "Category")
            ordered.append(
                {
                    "title": title,
                    "parent_opt": root,
                    "children": kids,
                }
            )

        # Hanging children: group under missing parent_id as virtual title path
        hang_groups: dict[str, list[dict]] = {}
        for o in hanging:
            if self._cat_key(o) in used_child_keys:
                continue
            if filt and not _match_opt(o):
                continue
            pid = str(o.get("parent_id") or "")
            # try label from path
            path = str(o.get("path") or "")
            parts = [p.strip() for p in path.split(" / ") if p.strip()]
            title = parts[0] if len(parts) >= 2 else f"#{pid}"
            hang_groups.setdefault(title, []).append(o)

        for title in sorted(hang_groups.keys(), key=self._norm):
            kids = sorted(
                hang_groups[title],
                key=lambda o: self._norm(str(o.get("label") or "")),
            )
            ordered.append(
                {
                    "title": title,
                    "parent_opt": by_label.get(self._norm(title)),
                    "children": kids,
                }
            )

        # Never invent "Other categories" dump of all mains
        if not ordered and opts:
            # absolute fallback: each root as solo
            for o in sorted(opts, key=lambda x: self._norm(str(x.get("label") or ""))):
                if filt and not _match_opt(o):
                    continue
                ordered.append(
                    {
                        "title": str(o.get("label") or ""),
                        "parent_opt": o,
                        "children": [],
                    }
                )
        return ordered

    def _category_parent_keys_with_children(self, opts: list[dict] | None = None) -> set[str]:
        """Keys of main categories that have at least one subcategory (not selectable alone)."""
        opts = list(opts if opts is not None else self._cat_options)
        by_id = {
            str(o.get("value") or o.get("id") or "").strip(): o
            for o in opts
            if str(o.get("value") or o.get("id") or "").strip()
        }
        parent_ids: set[str] = set()
        for o in opts:
            pid = str(o.get("parent_id") or "").strip()
            if pid and pid in by_id:
                parent_ids.add(pid)
        keys: set[str] = set()
        for pid in parent_ids:
            po = by_id.get(pid)
            if po:
                keys.add(self._cat_key(po))
        return keys

    def _strip_main_category_checks(self, checked: set[str] | None = None) -> set[str]:
        """Drop main-category selections when that main has subcategories."""
        if checked is None:
            checked = set(self._cat_checked)
        blocked = self._category_parent_keys_with_children()
        return {k for k in checked if k not in blocked}

    def _rebuild_category_list(self, checked: set[str] | None = None):
        # Preserve ticks across filter reloads
        for k, v in self._cat_vars.items():
            if v.get():
                self._cat_checked.add(k)
            else:
                self._cat_checked.discard(k)
        if checked is None:
            checked = set(self._cat_checked)
        else:
            self._cat_checked = set(checked)
        # Never keep a main category checked if it has children
        self._cat_checked = self._strip_main_category_checks(self._cat_checked)
        checked = set(self._cat_checked)
        filt = self._norm(self.cat_filter_var.get() if hasattr(self, "cat_filter_var") else "")

        for w in self.cats_inner.winfo_children():
            w.destroy()
        self._cat_vars.clear()
        self._cat_key_to_opt.clear()

        if not self._cat_options:
            self._cats_placeholder()
            self.cat_count_var.set("0 options")
            return

        groups = self._category_tree_groups(self._cat_options, filt)
        shown = 0
        shown_keys: set[str] = set()

        # Collapse all by default; only open mains that have a checked sub
        # (or when the user is filtering).
        open_keys: set[str] = set()
        for g in groups:
            gkey = self._norm(g.get("title") or "")
            kids = list(g.get("children") or [])
            if filt and (
                filt in gkey
                or any(
                    filt
                    in self._norm(
                        f"{c.get('label','')} {c.get('path','')} {c.get('value','')}"
                    )
                    for c in kids
                )
            ):
                open_keys.add(gkey)
            if any(self._cat_key(ch) in self._cat_checked for ch in kids):
                open_keys.add(gkey)
        # Keep user-opened groups that still exist (manual ▼ during session)
        for g in groups:
            gkey = self._norm(g.get("title") or "")
            if gkey in self._cat_expanded:
                open_keys.add(gkey)
        # Fresh render path: caller can clear _cat_expanded before rebuild
        self._cat_expanded = open_keys

        def _add_checkbox(parent, opt: dict, *, is_sub: bool, row_i: int):
            nonlocal shown
            key = self._cat_key(opt)
            if key in shown_keys:
                return
            shown_keys.add(key)
            label = str(opt.get("label") or opt.get("value") or "")
            bg = C["bg_mid"] if row_i % 2 == 0 else C["row_alt"]
            row = tk.Frame(parent, bg=bg, highlightthickness=0)
            row.pack(fill="x", padx=(8, 4), pady=1)
            var = tk.BooleanVar(value=key in self._cat_checked)
            self._cat_vars[key] = var
            self._cat_key_to_opt[key] = opt

            def _toggle(k=key, v=var):
                if v.get():
                    self._cat_checked.add(k)
                else:
                    self._cat_checked.discard(k)

            tk.Checkbutton(
                row,
                text=f"└  {label}",
                variable=var,
                bg=bg,
                activebackground=C["glass_sel"],
                activeforeground=C["white"],
                font=(FONT_UI, 10),
                fg=C["text"],
                anchor="w",
                selectcolor=C["accent_dim"],
                highlightthickness=0,
                bd=0,
                command=_toggle,
            ).pack(fill="x", padx=(22, 10), pady=4)
            shown += 1

        row_i = 0
        for g in groups:
            title = g.get("title") or "Categories"
            parent_opt = g.get("parent_opt")
            children = list(g.get("children") or [])
            gkey = self._norm(title)
            is_open = gkey in self._cat_expanded
            n_sub = len(children)
            n_checked_subs = sum(
                1 for ch in children if self._cat_key(ch) in self._cat_checked
            )

            head_bg = C["glass_hi"]
            head = tk.Frame(
                self.cats_inner,
                bg=head_bg,
                highlightthickness=1,
                highlightbackground=C["glass_border"] if not is_open else C["accent"],
            )
            head.pack(fill="x", padx=4, pady=(8, 2))
            head_row = tk.Frame(head, bg=head_bg)
            head_row.pack(fill="x")

            def _toggle_expand(_e=None, key=gkey):
                if key in self._cat_expanded:
                    self._cat_expanded.discard(key)
                else:
                    self._cat_expanded.add(key)
                self._rebuild_category_list(None)

            # ▶ / ▼ expand control
            arrow = "▼" if is_open else "▶"
            if n_sub == 0:
                arrow = "•"
            arrow_lbl = tk.Label(
                head_row,
                text=f"  {arrow}  ",
                font=(FONT_UI, 11, "bold"),
                bg=head_bg,
                fg=C["accent_hi"] if n_sub else C["muted"],
                cursor="hand2" if n_sub else "arrow",
            )
            arrow_lbl.pack(side="left")
            if n_sub:
                arrow_lbl.bind("<Button-1>", _toggle_expand)
                head.bind("<Button-1>", _toggle_expand)
                head_row.bind("<Button-1>", _toggle_expand)

            # Main with subs: label only (not checkable). Leaf main: checkbox OK.
            lab_text = str(
                (parent_opt or {}).get("label") if parent_opt else title
            ) or title
            if n_sub:
                lab_text = f"{lab_text}  ·  {n_sub} sub"
                if n_checked_subs:
                    lab_text += f"  ·  {n_checked_subs} selected"
                if not is_open:
                    lab_text += "  (click to open)"

            if n_sub == 0 and parent_opt is not None:
                # true leaf at root level — selectable
                key = self._cat_key(parent_opt)
                if key not in shown_keys:
                    shown_keys.add(key)
                    var = tk.BooleanVar(value=key in self._cat_checked)
                    self._cat_vars[key] = var
                    self._cat_key_to_opt[key] = parent_opt

                    def _toggle_leaf(k=key, v=var):
                        if v.get():
                            self._cat_checked.add(k)
                        else:
                            self._cat_checked.discard(k)

                    ck = tk.Checkbutton(
                        head_row,
                        text="",
                        variable=var,
                        bg=head_bg,
                        activebackground=C["glass_sel"],
                        activeforeground=C["white"],
                        selectcolor=C["accent_dim"],
                        highlightthickness=0,
                        bd=0,
                        command=_toggle_leaf,
                        width=2,
                    )
                    ck.pack(side="left", padx=(2, 2), pady=6)
                    tk.Label(
                        head_row,
                        text=lab_text,
                        font=(FONT_UI, 10, "bold"),
                        bg=head_bg,
                        fg=C["accent_hi"],
                        anchor="w",
                    ).pack(side="left", fill="x", expand=True, padx=4, pady=6)
                    shown += 1
            else:
                # Parent header only — select categories under it via sub checkboxes
                name_lbl = tk.Label(
                    head_row,
                    text=lab_text,
                    font=(FONT_UI, 10, "bold"),
                    bg=head_bg,
                    fg=C["accent_hi"],
                    anchor="w",
                    cursor="hand2" if n_sub else "arrow",
                )
                name_lbl.pack(side="left", fill="x", expand=True, padx=8, pady=7)
                if n_sub:
                    name_lbl.bind("<Button-1>", _toggle_expand)

            # Subcategories only when this main category is expanded
            if is_open and children:
                for child in children:
                    _add_checkbox(self.cats_inner, child, is_sub=True, row_i=row_i)
                    row_i += 1
            elif not children and parent_opt is None:
                continue

        if shown == 0 and not groups:
            tk.Label(
                self.cats_inner,
                text="No categories match the filter.",
                font=(FONT_UI, 10),
                bg=C["bg"],
                fg=C["muted"],
                pady=20,
            ).pack()

        n_groups = len(groups)
        n_sub_total = sum(len(g.get("children") or []) for g in groups)
        self.cat_count_var.set(
            f"{len(self._cat_options)} categories · {n_groups} main · "
            f"{n_sub_total} sub · selected {len(self._cat_checked)} · "
            f"open {len(self._cat_expanded)} · check subcategories only"
        )
        self.cats_scroll.bind_tree()

    def _filter_categories(self):
        self._rebuild_category_list(None)

    def _clear_category_picks(self):
        self._cat_checked.clear()
        for v in self._cat_vars.values():
            v.set(False)

    def _render_categories_from_page(self, page: dict, ai_hints: list | None = None):
        opts = list(page.get("available_category_options") or [])
        # Fallback: label-only lists
        if not opts:
            for name in page.get("available_categories") or page.get("categories") or []:
                s = str(name).strip()
                if s:
                    opts.append(
                        {"id": s, "value": s, "label": s, "field_name": "", "selected": False}
                    )
        self._cat_options = opts
        ai_hints = list(ai_hints or [])
        # Expand dict-shaped AI categories
        flat_hints: list[str] = []
        for h in ai_hints:
            if isinstance(h, dict):
                flat_hints.append(str(h.get("label") or h.get("name") or h.get("value") or ""))
            else:
                flat_hints.append(str(h or ""))
        flat_hints = [h for h in flat_hints if str(h).strip()]

        title = (
            self._product_title
            or str((page or {}).get("product_name") or "")
            or self.name_var.get()
            or ""
        )

        # Extremely strict: one confident leaf (or empty — never a wrong main/sibling)
        self._cat_checked = set()
        matched_opts: list[dict] = []
        try:
            matched_opts = match_category_options_strict(
                title, flat_hints, opts, max_keep=1
            )
        except Exception:
            matched_opts = []

        # If AI gave nothing usable, only keep page-selected categories that
        # still pass the same strict kind filter (never soft-accept).
        if not matched_opts:
            page_selected_labels = [
                str(o.get("label") or "")
                for o in opts
                if isinstance(o, dict) and o.get("selected")
            ]
            try:
                matched_opts = match_category_options_strict(
                    title, page_selected_labels, opts, max_keep=1
                )
            except Exception:
                matched_opts = []

        for o in matched_opts:
            self._cat_checked.add(self._cat_key(o))

        # Never select a main category that has subcategories — sub only
        self._cat_checked = self._strip_main_category_checks(self._cat_checked)
        self._cat_expanded.clear()  # collapse all; rebuild opens mains with checked subs
        self._rebuild_category_list(set(self._cat_checked))

    def _selected_categories(self) -> list:
        # sync visible
        for k, v in self._cat_vars.items():
            if v.get():
                self._cat_checked.add(k)
            else:
                self._cat_checked.discard(k)
        self._cat_checked = self._strip_main_category_checks(self._cat_checked)
        out = []
        for key in self._cat_checked:
            opt = self._cat_key_to_opt.get(key)
            if not opt:
                # resolve from full list
                opt = next((o for o in self._cat_options if self._cat_key(o) == key), None)
            if not opt:
                continue
            out.append(
                {
                    "id": str(opt.get("id") or opt.get("value") or ""),
                    "value": str(opt.get("value") or ""),
                    "label": str(opt.get("label") or ""),
                    "field_name": str(opt.get("field_name") or ""),
                    "path": str(opt.get("path") or ""),
                    "parent_id": str(opt.get("parent_id") or ""),
                }
            )
        return out

    # ---------- Specs (dropdown-aware) ----------
    @staticmethod
    def _is_author_feature(feat_or_label) -> bool:
        if isinstance(feat_or_label, dict):
            label = str(
                feat_or_label.get("label")
                or feat_or_label.get("name")
                or ""
            )
        else:
            label = str(feat_or_label or "")
        n = " ".join(label.lower().split())
        if not n:
            return False
        if "ავტორ" in n:
            return True
        if n in ("author", "authors", "ავტორი"):
            return True
        return False

    def _logged_in_author_name(self) -> str:
        u = self._logged_in_user or {}
        if not isinstance(u, dict):
            u = (self._page_context or {}).get("logged_in_user") or {}
        name = str((u or {}).get("name") or "").strip()
        if name:
            return name
        first = str((u or {}).get("first") or "").strip()
        last = str((u or {}).get("last") or "").strip()
        full = " ".join(x for x in (first, last) if x).strip()
        if full:
            return full
        return str((u or {}).get("email") or "").strip()

    def _match_author_option(self, options: list[dict], author_name: str) -> dict | None:
        """Best-match option for logged-in admin among feature variants."""
        if not author_name or not options:
            return None
        an = self._norm(author_name)
        if not an:
            return None
        # Exact label / value
        for o in options:
            lab = self._norm(o.get("label") or "")
            val = self._norm(o.get("value") or "")
            if lab == an or val == an:
                return o
        # Partial: all tokens of author appear in label (or reverse for short names)
        tokens = [t for t in an.split() if len(t) > 1]
        best = None
        best_score = 0
        for o in options:
            lab = self._norm(o.get("label") or "")
            if not lab or lab.isdigit():
                continue
            score = 0
            if tokens and all(t in lab for t in tokens):
                score = 10 + len(lab)
            elif an in lab or lab in an:
                score = 5 + len(set(an) & set(lab))
            elif tokens and any(t in lab for t in tokens):
                score = 2
            if score > best_score:
                best_score = score
                best = o
        return best if best_score >= 2 else None

    def _is_condition_feature(self, feat_or_label) -> bool:
        if isinstance(feat_or_label, dict):
            label = str(
                feat_or_label.get("label")
                or feat_or_label.get("name")
                or ""
            )
        else:
            label = str(feat_or_label or "")
        return _is_condition_label(label)

    def _force_used_condition_wants(self, options: list | None = None) -> list[str]:
        """If title has Used/მეორადი → return მდგომარეობა value labels."""
        title = (
            self._product_title
            or self.name_var.get()
            or str((self._page_context or {}).get("product_name") or "")
            or ""
        )
        if not _title_indicates_used(title):
            return []
        hit = _pick_used_condition_option(list(options or []))
        if hit:
            lab = str(hit.get("label") or "").strip()
            val = str(hit.get("value") or "").strip()
            out = []
            if lab:
                out.append(lab)
            if val and val != lab:
                out.append(val)
            return out or ["მეორადი"]
        return ["მეორადი"]

    def _ai_value_for_feature(self, feat: dict, ai: dict) -> list[str]:
        """Return preferred label(s) from AI for this feature."""
        # ავტორი is always the logged-in admin — never AI
        if self._is_author_feature(feat):
            name = self._logged_in_author_name()
            return [name] if name else []
        # Title Used / მეორადი → მდგომარეობა = მეორადი
        if self._is_condition_feature(feat):
            forced = self._force_used_condition_wants(
                list(feat.get("options") or [])
            )
            if forced:
                return forced
        label = self._norm(feat.get("label") or feat.get("name") or "")
        field = str(feat.get("field_name") or "")
        fid = str(feat.get("id") or "")
        wants: list[str] = []

        fvals = ai.get("feature_values") if isinstance(ai.get("feature_values"), list) else []
        for item in fvals:
            if not isinstance(item, dict):
                continue
            if field and str(item.get("field_name") or "") == field:
                wants.append(str(item.get("value") or ""))
            elif fid and str(item.get("id") or "") == fid:
                wants.append(str(item.get("value") or ""))
            elif label and self._norm(item.get("label") or item.get("name") or "") == label:
                wants.append(str(item.get("value") or ""))
            else:
                # Partial feature name match (ბრენდი ↔ brand)
                ilab = self._norm(item.get("label") or item.get("name") or "")
                if label and ilab and (label in ilab or ilab in label):
                    wants.append(str(item.get("value") or ""))
            if isinstance(item.get("values"), list):
                wants.extend(str(x) for x in item["values"] if str(x).strip())
            if isinstance(item.get("labels"), list):
                wants.extend(str(x) for x in item["labels"] if str(x).strip())

        feats = ai.get("features") if isinstance(ai.get("features"), dict) else {}
        for k, v in feats.items():
            if label and self._norm(k) == label:
                wants.append("" if v is None else str(v))

        flat: list[str] = []
        for w in wants:
            if isinstance(w, list):
                flat.extend(str(x) for x in w)
            elif w:
                flat.append(str(w))
        return flat

    def _match_options(self, options: list[dict], wants: list[str]) -> list[dict]:
        if not wants:
            return []
        matched = []
        for w in wants:
            nw = self._norm(w)
            if not nw and not str(w).strip():
                continue
            # Prefer exact label, then value, then includes
            best = None
            best_score = 0
            for o in options:
                lab = self._norm(o.get("label") or "")
                val = str(o.get("value") or "")
                score = 0
                if val and val == str(w).strip():
                    score = 100
                elif lab and lab == nw:
                    score = 90
                elif nw and lab and (lab in nw or nw in lab):
                    score = 50 + min(len(lab), 20)
                elif nw and lab:
                    wt = set(nw.split())
                    lt = set(lab.split())
                    common = wt & lt
                    if common:
                        score = 10 * len(common)
                if score > best_score:
                    best_score = score
                    best = o
            if best and best_score >= 10 and best not in matched:
                matched.append(best)
        return matched

    def _render_features_from_page(self, page: dict, ai: dict):
        for w in self.specs_inner.winfo_children():
            w.destroy()
        self._feature_rows.clear()

        features = list(page.get("available_features") or [])
        if not features:
            self._specs_placeholder()
            self.specs_count_var.set("0 features")
            return

        header = tk.Frame(self.specs_inner, bg=C["bg_mid"])
        header.pack(fill="x", pady=(0, 4))
        tk.Label(
            header,
            text="Feature",
            font=(FONT_UI, 9, "bold"),
            bg=C["bg_mid"],
            fg=C["white"],
            width=26,
            anchor="w",
        ).pack(side="left", padx=8, pady=6)
        tk.Label(
            header,
            text="AI pick from product model · edit if needed",
            font=(FONT_UI, 9, "bold"),
            bg=C["bg_mid"],
            fg=C["white"],
            anchor="w",
        ).pack(side="left", fill="x", expand=True, padx=8)

        multi_n = 0
        for i, feat in enumerate(features):
            if not isinstance(feat, dict):
                continue
            mode = str(feat.get("selection_mode") or "").lower()
            ftype = str(feat.get("type") or "").lower()
            multiple = bool(feat.get("multiple")) or mode in ("multi", "checkbox_group") or "multi" in ftype
            options = list(feat.get("options") or [])
            # Prefer human-readable option labels over bare numeric IDs
            humanish = [
                o for o in options
                if str(o.get("label") or "").strip()
                and not str(o.get("label") or "").strip().isdigit()
            ]
            if humanish:
                keep_vals = {str(o.get("value")) for o in humanish}
                filtered = []
                for o in options:
                    lab = str(o.get("label") or "").strip()
                    val = str(o.get("value") or "")
                    if val in keep_vals or not lab.isdigit() or o.get("selected"):
                        if lab.isdigit() and not o.get("selected") and val not in keep_vals:
                            continue
                        filtered.append(o)
                by_v = {}
                for o in filtered:
                    v = str(o.get("value") or "")
                    lab = str(o.get("label") or "")
                    if v not in by_v or (lab and not lab.isdigit() and str(by_v[v].get("label") or "").isdigit()):
                        by_v[v] = o
                options = list(by_v.values())
            label = str(feat.get("label") or feat.get("name") or "Feature")
            field_name = str(feat.get("field_name") or "")
            fid = str(feat.get("id") or "")

            page_selected = [o for o in options if o.get("selected")]
            selected_opts: list = []
            ai_wants: list[str] = []

            if self._is_author_feature(feat):
                # ავტორი ALWAYS = logged-in admin (not page value, not AI)
                author = self._logged_in_author_name()
                author_opt = self._match_author_option(options, author) if author else None
                if author_opt:
                    selected_opts = [author_opt]
                elif author:
                    selected_opts = [
                        {"value": author, "label": author, "selected": True}
                    ]
                    if not any(
                        self._norm(o.get("label")) == self._norm(author)
                        or self._norm(o.get("value")) == self._norm(author)
                        for o in options
                    ):
                        options = list(options) + [selected_opts[0]]
                else:
                    selected_opts = page_selected
                ai_wants = [author] if author else []
            elif self._is_condition_feature(feat) and self._force_used_condition_wants(options):
                # Title Used / მეორადი → მდგომარეობა = მეორადი
                ai_wants = self._force_used_condition_wants(options)
                ai_opts = self._match_options(options, ai_wants)
                if ai_opts:
                    selected_opts = ai_opts
                else:
                    selected_opts = [
                        {"value": ai_wants[0], "label": ai_wants[0], "selected": True}
                    ]
                    if not any(
                        self._norm(o.get("label")) == self._norm(ai_wants[0])
                        for o in options
                    ):
                        options = list(options) + [selected_opts[0]]
            else:
                # AI values from product model win over leftover page selection
                ai_wants = self._ai_value_for_feature(feat, ai)
                ai_opts = self._match_options(options, ai_wants)
                if ai_opts:
                    selected_opts = ai_opts
                elif ai_wants and not options:
                    selected_opts = [{"value": ai_wants[0], "label": ai_wants[0], "selected": True}]
                else:
                    selected_opts = page_selected

            bg = C["card"] if i % 2 == 0 else C["row_alt"]
            row = tk.Frame(self.specs_inner, bg=bg)
            row.pack(fill="x", pady=2, padx=2)

            left = tk.Frame(row, bg=bg)
            left.pack(side="left", padx=8, pady=6, anchor="n")
            tk.Label(
                left,
                text=label,
                font=(FONT_UI, 10, "bold"),
                bg=bg,
                fg=C["text"],
                wraplength=200,
                justify="left",
                anchor="w",
            ).pack(anchor="w")
            mode_txt = "multi" if multiple else ("text" if mode == "text" or ftype == "text" or not options else "single")
            if multiple:
                multi_n += 1
            tk.Label(
                left,
                text=mode_txt,
                font=(FONT_UI, 8),
                bg=bg,
                fg=C["accent"] if multiple else C["muted"],
            ).pack(anchor="w")

            right = tk.Frame(row, bg=bg)
            right.pack(side="left", fill="x", expand=True, padx=8, pady=6)

            widget_state: dict = {
                "label": label,
                "id": fid,
                "field_name": field_name,
                "selection_mode": "multi" if multiple else mode_txt,
                "multiple": multiple,
                "options": options,
                "kind": mode_txt,
                "frame": row,
            }

            if mode_txt == "text" or (not options and mode_txt != "multi"):
                # Free text feature
                default = ""
                if selected_opts:
                    default = str(selected_opts[0].get("label") or selected_opts[0].get("value") or "")
                elif ai_wants:
                    default = ai_wants[0]
                elif feat.get("current"):
                    default = str(feat.get("current") or "")
                var = tk.StringVar(value=default)
                ent = tk.Entry(
                    right,
                    textvariable=var,
                    font=(FONT_UI, 10),
                    bg=C["white"],
                    relief="flat",
                    highlightthickness=1,
                    highlightbackground=C["card_border"],
                )
                ent.pack(fill="x")
                widget_state["text_var"] = var
                widget_state["kind"] = "text"

            elif multiple:
                # Multi: listbox of option labels
                lb = tk.Listbox(
                    right,
                    selectmode=tk.EXTENDED,
                    height=min(8, max(3, min(len(options), 8))),
                    font=(FONT_UI, 9),
                    bg=C["white"],
                    exportselection=False,
                    activestyle="dotbox",
                    highlightthickness=1,
                    highlightbackground=C["card_border"],
                    selectbackground=C["action"],
                    selectforeground=C["white"],
                )
                labels = []
                for o in options:
                    lab = str(o.get("label") or o.get("value") or "")
                    labels.append(lab)
                    lb.insert("end", lab)
                # select matching
                want_set = {self._norm(o.get("label") or "") for o in selected_opts}
                for idx, lab in enumerate(labels):
                    if self._norm(lab) in want_set:
                        lb.selection_set(idx)
                lb.pack(fill="x")
                tk.Label(
                    right,
                    text="Ctrl+click for multi",
                    font=(FONT_UI, 7),
                    bg=bg,
                    fg=C["muted"],
                ).pack(anchor="w")
                widget_state["listbox"] = lb
                widget_state["kind"] = "multi"

            else:
                # Single select combobox
                labels = ["— none —"]
                for o in options:
                    lab = str(o.get("label") or o.get("value") or "")
                    if lab and lab not in labels:
                        labels.append(lab)
                var = tk.StringVar(value="— none —")
                if self._is_author_feature(feat):
                    author = self._logged_in_author_name()
                    if author:
                        if author not in labels:
                            labels.append(author)
                        var.set(author)
                    elif selected_opts:
                        var.set(
                            str(
                                selected_opts[0].get("label")
                                or selected_opts[0].get("value")
                                or "— none —"
                            )
                        )
                elif selected_opts:
                    var.set(str(selected_opts[0].get("label") or selected_opts[0].get("value") or "— none —"))
                elif feat.get("selected_labels"):
                    var.set(str(feat["selected_labels"][0]))
                # Author is fixed to logged-in admin — still allow choose if detection failed
                combo_state = "readonly"
                combo = ttk.Combobox(
                    right,
                    textvariable=var,
                    values=labels,
                    state=combo_state,
                    font=(FONT_UI, 10),
                )
                combo.pack(fill="x")
                if self._is_author_feature(feat) and self._logged_in_author_name():
                    tk.Label(
                        right,
                        text="Always logged-in admin",
                        font=(FONT_UI, 7),
                        bg=bg,
                        fg=C["action"],
                    ).pack(anchor="w")
                if self._is_condition_feature(feat) and self._force_used_condition_wants(options):
                    tk.Label(
                        right,
                        text="From title: Used / მეორადი",
                        font=(FONT_UI, 7),
                        bg=bg,
                        fg=C["action"],
                    ).pack(anchor="w")
                widget_state["combo_var"] = var
                widget_state["kind"] = "single"
                widget_state["force_author"] = self._is_author_feature(feat)
                widget_state["force_used_condition"] = bool(
                    self._is_condition_feature(feat)
                    and self._force_used_condition_wants(options)
                )

            self._feature_rows.append(widget_state)

        self.specs_count_var.set(
            f"{len(self._feature_rows)} features · {multi_n} multi-select"
        )
        self.specs_scroll.bind_tree()
        self._filter_feature_rows()

    def _filter_feature_rows(self):
        filt = self._norm(self._feature_filter_var.get())
        for row in self._feature_rows:
            fr = row.get("frame")
            if not fr:
                continue
            if not filt or filt in self._norm(row.get("label") or ""):
                fr.pack(fill="x", pady=2, padx=2)
            else:
                fr.pack_forget()

    def _collect_feature_values(self) -> list[dict]:
        out = []
        for row in self._feature_rows:
            label = str(row.get("label") or "")
            field_name = str(row.get("field_name") or "")
            fid = str(row.get("id") or "")
            mode = str(row.get("selection_mode") or row.get("kind") or "single")
            options = list(row.get("options") or [])
            kind = row.get("kind") or "text"

            values: list[str] = []
            labels_sel: list[str] = []

            if kind == "text":
                text = (row.get("text_var").get() if row.get("text_var") else "").strip()
                if self._is_author_feature(row) or row.get("force_author"):
                    author = self._logged_in_author_name()
                    if author:
                        text = author
                        try:
                            if row.get("text_var"):
                                row["text_var"].set(author)
                        except Exception:
                            pass
                if not text:
                    continue
                labels_sel = [text]
                values = [text]
            elif kind == "multi":
                lb = row.get("listbox")
                if not lb:
                    continue
                idxs = lb.curselection()
                for i in idxs:
                    lab = lb.get(i)
                    labels_sel.append(lab)
                    # map label -> option value
                    found = next(
                        (
                            o
                            for o in options
                            if self._norm(o.get("label") or "") == self._norm(lab)
                            or str(o.get("value")) == str(lab)
                        ),
                        None,
                    )
                    vid = str((found or {}).get("value") or "").strip() if found else ""
                    values.append(vid if vid else lab)
                if not values:
                    continue
            else:  # single
                var = row.get("combo_var")
                pick = (var.get() if var else "").strip()
                # Force ავტორი to logged-in admin on every Fill
                if self._is_author_feature(row) or row.get("force_author"):
                    author = self._logged_in_author_name()
                    if author:
                        pick = author
                        if var is not None:
                            try:
                                var.set(author)
                            except Exception:
                                pass
                # Title Used / მეორადი → მდგომარეობა = მეორადი on every Fill
                if self._is_condition_feature(row) or row.get("force_used_condition"):
                    forced = self._force_used_condition_wants(options)
                    if forced:
                        fo = self._match_options(options, forced)
                        pick = str((fo[0].get("label") if fo else forced[0]) or forced[0])
                        if var is not None:
                            try:
                                var.set(pick)
                            except Exception:
                                pass
                if not pick or pick == "— none —" or pick in (".", "..", "...", "…"):
                    continue
                labels_sel = [pick]
                found = next(
                    (
                        o
                        for o in options
                        if self._norm(o.get("label") or "") == self._norm(pick)
                        or str(o.get("value")) == str(pick)
                    ),
                    None,
                )
                if not found and self._is_author_feature(row):
                    found = self._match_author_option(options, pick)
                vid = str((found or {}).get("value") or "").strip() if found else ""
                # Prefer real variant id for Select2; keep human name in labels always
                values = []
                if vid and vid != pick:
                    values.append(vid)
                values.append(pick)
                if vid and vid not in values:
                    values.append(vid)

            payload_value: str | list[str]
            if kind == "multi" or mode == "multi":
                payload_value = labels_sel if len(labels_sel) > 1 else labels_sel[0]
                # also send values for exact option ids
            else:
                payload_value = labels_sel[0]

            # Clean blank / "..." labels so website Select2 never has empty display
            clean_labels = [
                x for x in labels_sel
                if str(x).strip() and str(x).strip() not in (".", "..", "...", "…")
            ]
            if not clean_labels and not values:
                continue

            # Send labels + variant IDs so page Select2 can match either
            out.append(
                {
                    "id": fid,
                    "field_name": field_name,
                    "label": label,
                    "value": clean_labels if kind == "multi" else (clean_labels[0] if clean_labels else values[0]),
                    "values": values,
                    "labels": clean_labels or labels_sel,
                    "selection_mode": "multi" if kind == "multi" else "single",
                }
            )
        return out

    # ---------- Video ----------
    def _update_video_preview_lbl(self):
        url = self.video_url_var.get().strip()
        if url:
            short = url if len(url) < 90 else url[:87] + "…"
            self.video_preview_lbl.configure(text=f"Ready: {short}\nClick Preview in browser")
        else:
            self.video_preview_lbl.configure(text="No video URL yet — runs with Scrape.")

    def _preview_video(self):
        url = self.video_url_var.get().strip()
        if not url:
            glass_info(self, "Video", "No URL to open.")
            return
        webbrowser.open(url)
        self._update_video_preview_lbl()

    # ---------- Collect payload ----------
    def _collect_ai_payload(self) -> dict:
        features_map = {}
        feature_values = self._collect_feature_values()
        for item in feature_values:
            features_map[item["label"]] = item["value"]

        videos = []
        url = self.video_url_var.get().strip()
        if url:
            # Never send a YouTube search page — resolve to watch?v= when needed
            if is_youtube_search_url(url) or not url:
                url = resolve_youtube_watch_url(
                    self._product_title or self.name_var.get() or "",
                    existing_url=url,
                )
                self.video_url_var.set(url)
            else:
                url = resolve_youtube_watch_url(
                    self._product_title or self.name_var.get() or "",
                    existing_url=url,
                )
                if url:
                    self.video_url_var.set(url)
            if url and not is_youtube_search_url(url):
                videos.append(
                    {
                        "url": url,
                        "title": self.video_title_var.get().strip(),
                        "description": self.video_desc.get("1.0", "end").strip(),
                        "provider": self.video_provider_var.get().strip() or "youtube",
                        "position": 0,
                        "status": "A",
                    }
                )
            elif url:
                # Still only a search page — send it as last resort but try resolve again
                videos.append(
                    {
                        "url": url,
                        "title": self.video_title_var.get().strip(),
                        "description": self.video_desc.get("1.0", "end").strip(),
                        "provider": self.video_provider_var.get().strip() or "youtube",
                        "position": 0,
                        "status": "A",
                    }
                )

        payload = {
            "product_name": self.name_var.get().strip() or self._product_title,
            "full_description": self._get_text("full_description"),
            "promo_text": self._get_text("promo_text"),
            "page_title": self._get_text("page_title"),
            "meta_description": self._get_text("meta_description"),
            "meta_keywords": self._get_text("meta_keywords"),
            "seo_name": self._get_text("seo_name"),
            "categories": self._selected_categories(),
            "features": features_map,
            "feature_values": feature_values,
            "videos": videos,
            "notes_for_user": str((self._ai_raw or {}).get("notes_for_user") or ""),
        }
        price = self.price_var.get().strip()
        if price:
            payload["price"] = price
        return payload

    def _selected_image_jobs(self) -> list[dict]:
        selected_ids = [i for i, v in self._image_vars.items() if v.get()]
        main_id = self._main_image_var.get().strip()
        if main_id and main_id not in selected_ids:
            selected_ids.insert(0, main_id)
        if not main_id and selected_ids:
            main_id = selected_ids[0]
        id_to_item = {x["id"]: x for x in self._image_results}
        jobs = []
        for iid in selected_ids:
            item = id_to_item.get(iid)
            if item:
                jobs.append({"id": iid, "url": item["url"], "is_main": iid == main_id, "meta": item})
        return jobs

    # ---------- Bulk queue ----------
    def _bulk_job_by_id(self, product_id: str) -> dict | None:
        pid = str(product_id or "")
        for j in self._bulk_jobs:
            if str(j.get("product_id")) == pid:
                return j
        return None

    def _bulk_rebuild_tree(self):
        tree = getattr(self, "bulk_tree", None)
        if not tree:
            return
        for iid in tree.get_children():
            tree.delete(iid)
        for job in self._bulk_jobs:
            pid = str(job.get("product_id") or "")
            pick = "☑" if job.get("selected", True) else "☐"
            appr = "Yes" if job.get("approved") else "No"
            err = str(job.get("error_message") or "")[:120]
            tree.insert(
                "",
                "end",
                iid=pid,
                values=(
                    pick,
                    pid,
                    str(job.get("name") or "")[:80],
                    str(job.get("status") or "pending"),
                    appr,
                    err,
                ),
            )
        n = len(self._bulk_jobs)
        by_st: dict[str, int] = {}
        for j in self._bulk_jobs:
            st = str(j.get("status") or "pending")
            by_st[st] = by_st.get(st, 0) + 1
        parts = [f"{n} in queue"]
        for st in (
            "pending",
            "scraping",
            "ready",
            "filling",
            "filled",
            "skipped",
            "error",
        ):
            if by_st.get(st):
                parts.append(f"{by_st[st]} {st}")
        self.bulk_count_var.set(" · ".join(parts))

    def _bulk_update_tree_row(self, job: dict):
        tree = getattr(self, "bulk_tree", None)
        if not tree:
            return
        pid = str(job.get("product_id") or "")
        if not tree.exists(pid):
            self._bulk_rebuild_tree()
            return
        pick = "☑" if job.get("selected", True) else "☐"
        appr = "Yes" if job.get("approved") else "No"
        err = str(job.get("error_message") or "")[:120]
        tree.item(
            pid,
            values=(
                pick,
                pid,
                str(job.get("name") or "")[:80],
                str(job.get("status") or "pending"),
                appr,
                err,
            ),
        )

    def bulk_import_from_page(self):
        if self._busy:
            return
        if not chrome_debug_available():
            glass_error(self, 
                "Chrome not connected",
                "Run START.bat, open Products → Products (or category list), tick products.",
            )
            return
        self._set_busy(True, "Importing product list from Chrome…")
        mode = self.bulk_import_mode.get()
        threading.Thread(
            target=self._bulk_import_worker, args=(mode,), daemon=True
        ).start()

    def _bulk_import_worker(self, mode: str):
        try:
            driver = connect_to_chrome(status_cb=lambda m: self._set_status(m[:80]))
            data = scan_product_list_page(driver)
            products = list(data.get("products") or [])
            if not products:
                raise RuntimeError(
                    "No products found on the open page.\n\n"
                    "Open Products → Products (dispatch=products.manage) or a category "
                    "product list in debug Chrome, then Import again."
                )
            if mode == "selected":
                picked = [p for p in products if p.get("checked")]
                if not picked:
                    raise RuntimeError(
                        f"Found {len(products)} product(s) on the page, but none are ticked.\n"
                        "Tick rows in CS-Cart (or choose “All products visible on this list page”)."
                    )
                products = picked

            added = 0
            updated = 0
            new_jobs: list[dict] = []
            for p in products:
                pid = str(p.get("product_id") or "")
                if not pid:
                    continue
                existing = self._bulk_job_by_id(pid)
                if existing:
                    existing["name"] = p.get("name") or existing.get("name")
                    existing["edit_url"] = p.get("edit_url") or existing.get("edit_url")
                    existing["selected"] = True
                    updated += 1
                else:
                    new_jobs.append(
                        {
                            "product_id": pid,
                            "name": str(p.get("name") or f"Product #{pid}"),
                            "edit_url": str(p.get("edit_url") or ""),
                            "status": "pending",
                            "selected": True,
                            "approved": False,
                            "page_context": {},
                            "ai_payload": {},
                            "image_results": [],
                            "image_query": "",
                            "fill_payload": None,
                            "image_jobs": None,
                            "error_message": "",
                        }
                    )
                    added += 1

            def _ui():
                # Apply updates that only changed in worker by id lookup (already on existing refs)
                for j in new_jobs:
                    if not self._bulk_job_by_id(str(j.get("product_id"))):
                        self._bulk_jobs.append(j)
                self._bulk_rebuild_tree()
                self._select_tab("bulk")
                self._set_busy(
                    False,
                    f"Imported {added} new · {updated} updated · queue {len(self._bulk_jobs)}",
                )
                glass_info(self, 
                    "Bulk import",
                    f"Imported from list page.\n\n"
                    f"New: {added}\nUpdated: {updated}\nQueue total: {len(self._bulk_jobs)}\n\n"
                    f"Page: {(data.get('page_url') or '')[:90]}",
                )

            self.after(0, _ui)
        except Exception as exc:
            msg = str(exc)
            self.after(0, lambda: self._set_busy(False))
            self.after(0, lambda m=msg: glass_error(self, "Bulk import", m))

    def bulk_cancel_work(self):
        self._bulk_cancel = True
        self._set_status("Cancelling bulk work after current product…")

    def bulk_remove_selected(self):
        if self._busy:
            return
        tree = self.bulk_tree
        sel = list(tree.selection())
        if not sel:
            # remove unselected (☐) jobs if any toggled off
            self._bulk_jobs = [j for j in self._bulk_jobs if j.get("selected", True)]
            self._bulk_rebuild_tree()
            return
        drop = set(sel)
        self._bulk_jobs = [
            j for j in self._bulk_jobs if str(j.get("product_id")) not in drop
        ]
        if self._bulk_active_id in drop:
            self._bulk_active_id = None
        self._bulk_rebuild_tree()

    def bulk_clear_done(self):
        if self._busy:
            return
        self._bulk_jobs = [
            j
            for j in self._bulk_jobs
            if str(j.get("status")) not in ("filled", "skipped")
        ]
        self._bulk_rebuild_tree()

    def bulk_approve_all_ready(self):
        n = 0
        for j in self._bulk_jobs:
            if str(j.get("status")) == "ready" and j.get("selected", True):
                j["approved"] = True
                n += 1
                self._bulk_update_tree_row(j)
        self.bulk_count_var.set(self.bulk_count_var.get())
        self._bulk_rebuild_tree()
        self._set_status(f"Approved {n} ready product(s) for fill.")

    def _on_bulk_tree_click(self, event):
        """Toggle pick / approve on column click without blocking select."""
        tree = self.bulk_tree
        region = tree.identify_region(event.x, event.y)
        if region != "cell":
            return
        col = tree.identify_column(event.x)
        row = tree.identify_row(event.y)
        if not row:
            return
        job = self._bulk_job_by_id(row)
        if not job:
            return
        # #1 = pick, #5 = approved (1-based with tree columns)
        if col == "#1":
            job["selected"] = not job.get("selected", True)
            self._bulk_update_tree_row(job)
            return "break"
        if col == "#5":
            if str(job.get("status")) in ("ready", "filled", "error"):
                job["approved"] = not job.get("approved")
                self._bulk_update_tree_row(job)
            return "break"

    def _on_bulk_tree_select(self, _event=None):
        pass

    def _on_bulk_tree_double(self, _event=None):
        if self._busy:
            return
        sel = self.bulk_tree.selection()
        if not sel:
            return
        job = self._bulk_job_by_id(sel[0])
        if not job:
            return
        if str(job.get("status")) not in ("ready", "filled", "error"):
            glass_info(self, 
                "Bulk",
                "Scrape this product first (status must be ready) before reviewing.",
            )
            return
        self._bulk_load_job_into_review(job)

    def _store_active_bulk_review(self):
        """Persist current review panels into the active bulk job (if any)."""
        pid = self._bulk_active_id
        if not pid:
            return
        job = self._bulk_job_by_id(pid)
        if not job or str(job.get("status")) not in ("ready", "filled"):
            return
        try:
            job["fill_payload"] = self._collect_ai_payload()
            job["image_jobs"] = self._selected_image_jobs()
            job["name"] = self.name_var.get().strip() or job.get("name")
            job["approved"] = True
            self._bulk_update_tree_row(job)
        except Exception:
            pass

    def _bulk_load_job_into_review(self, job: dict):
        self._store_active_bulk_review()
        pid = str(job.get("product_id") or "")
        self._bulk_active_id = pid
        self._product_url = str(job.get("edit_url") or "")
        self._product_title = str(job.get("name") or "")
        self._page_context = dict(job.get("page_context") or {})
        self._ai_raw = dict(job.get("ai_payload") or {})
        self._logged_in_user = (
            self._page_context.get("logged_in_user")
            if isinstance(self._page_context.get("logged_in_user"), dict)
            else {}
        )
        images = list(job.get("image_results") or [])
        self._image_results = images
        title = self._product_title or str(
            (self._page_context or {}).get("product_name") or ""
        )
        self._apply_scrape_to_ui(
            self._ai_raw,
            title,
            str(job.get("image_query") or ""),
            images,
        )
        # Restore fill_payload-driven image selection if present
        ij = job.get("image_jobs")
        if isinstance(ij, list) and ij:
            self._image_vars.clear()
            # re-render already set main; re-check selected
            for item in images:
                iid = str(item.get("id") or "")
                if iid and iid not in self._image_vars:
                    continue
            # simpler: set vars after render
            for item in images:
                iid = str(item.get("id") or "")
                if iid and iid in self._image_vars:
                    self._image_vars[iid].set(
                        any(str(j.get("id")) == iid for j in ij)
                    )
            main = next((j for j in ij if j.get("is_main")), None)
            if main:
                self._main_image_var.set(str(main.get("id") or ""))

        # If fill_payload has texts, re-apply collected values over AI defaults
        fp = job.get("fill_payload")
        if isinstance(fp, dict) and fp:
            self.name_var.set(str(fp.get("product_name") or self.name_var.get()))
            if fp.get("price") is not None:
                self.price_var.set(str(fp.get("price") or ""))
            for key in (
                "full_description",
                "promo_text",
                "page_title",
                "meta_description",
                "meta_keywords",
                "seo_name",
            ):
                if fp.get(key) is not None:
                    self._set_text(key, str(fp.get(key) or ""))
            vids = fp.get("videos") or []
            if vids and isinstance(vids[0], dict):
                self.video_url_var.set(str(vids[0].get("url") or ""))
                self.video_title_var.set(str(vids[0].get("title") or ""))
                self.video_desc.delete("1.0", "end")
                self.video_desc.insert("1.0", str(vids[0].get("description") or ""))
                self._update_video_preview_lbl()

        self._scraped = True
        if not getattr(self, "_busy", False):
            self._set_buttons_idle()
        self._select_tab("text")
        self.title_var.set(f"[Bulk #{pid}] {title}")
        self._set_status(f"Reviewing bulk product {pid} — edit tabs, then Approve / Fill.")
        job["approved"] = True
        self._bulk_update_tree_row(job)

    def _bulk_payload_for_job(self, job: dict) -> tuple[dict, list[dict]]:
        """Fill payload for a job: prefer stored review, else collect after load."""
        if self._bulk_active_id == str(job.get("product_id")):
            self._store_active_bulk_review()
        fp = job.get("fill_payload")
        if isinstance(fp, dict) and fp:
            jobs = job.get("image_jobs")
            if not isinstance(jobs, list):
                jobs = []
            return fp, jobs
        # Build from AI (user never opened review)
        ai = job.get("ai_payload") if isinstance(job.get("ai_payload"), dict) else {}
        page = job.get("page_context") if isinstance(job.get("page_context"), dict) else {}
        title = str(
            job.get("name")
            or page.get("product_name")
            or ai.get("product_name")
            or ""
        )
        features_map = {}
        feature_values = []
        if isinstance(ai.get("feature_values"), list):
            feature_values = [x for x in ai["feature_values"] if isinstance(x, dict)]
            for item in feature_values:
                lab = str(item.get("label") or "")
                if lab:
                    features_map[lab] = item.get("value")
        elif isinstance(ai.get("features"), dict):
            features_map = dict(ai["features"])
            for k, v in features_map.items():
                feature_values.append({"label": k, "value": v})

        # Categories: STRICT single best leaf only
        cats: list = []
        raw_cats = list(ai.get("categories") or [])
        opts = list(page.get("available_category_options") or [])
        try:
            hints = []
            for h in raw_cats:
                if isinstance(h, dict):
                    hints.append(str(h.get("label") or h.get("name") or h.get("value") or ""))
                else:
                    hints.append(str(h or ""))
            matched = match_category_options_strict(title, hints, opts, max_keep=1)
            cats = matched if matched else []
        except Exception:
            cats = []

        videos = []
        for v in ai.get("videos") or []:
            if isinstance(v, dict) and v.get("url"):
                videos.append(v)
                break
        if not videos:
            url = resolve_youtube_watch_url(title, existing_url="")
            if url:
                videos.append(
                    {
                        "url": url,
                        "title": f"{title} — ვიდეო",
                        "description": "",
                        "provider": "youtube",
                        "position": 0,
                        "status": "A",
                    }
                )

        payload = {
            "product_name": str(ai.get("product_name") or title),
            "full_description": str(ai.get("full_description") or ""),
            "promo_text": str(ai.get("promo_text") or ""),
            "page_title": str(ai.get("page_title") or ""),
            "meta_description": str(ai.get("meta_description") or ""),
            "meta_keywords": str(ai.get("meta_keywords") or ""),
            "seo_name": str(ai.get("seo_name") or ""),
            "categories": cats,
            "features": features_map,
            "feature_values": feature_values,
            "videos": videos,
            "notes_for_user": str(ai.get("notes_for_user") or ""),
        }
        if ai.get("price") is not None:
            payload["price"] = ai.get("price")

        # Image jobs: prefer saved selection; else none (user picks in review)
        jobs = job.get("image_jobs")
        if not isinstance(jobs, list):
            jobs = []
        if not jobs:
            # Build from any previously stored selection flags on image_results
            for item in job.get("image_results") or []:
                if not isinstance(item, dict):
                    continue
                if item.get("selected") or item.get("is_main"):
                    jobs.append(
                        {
                            "id": item.get("id"),
                            "url": item.get("url") or item.get("thumbnail"),
                            "is_main": bool(item.get("is_main")),
                            "meta": item,
                        }
                    )
        return payload, jobs

    def bulk_start_scrape(self):
        if self._busy:
            return
        cfg = self._require_ready(need_key=True)
        if not cfg:
            return
        jobs = [
            j
            for j in self._bulk_jobs
            if j.get("selected", True)
            and str(j.get("status")) in ("pending", "error", "ready")
        ]
        # re-scrape ready if selected? plan: pending + error primarily; allow ready re-scrape for selected pending only
        jobs = [
            j
            for j in self._bulk_jobs
            if j.get("selected", True)
            and str(j.get("status")) in ("pending", "error")
        ]
        if not jobs:
            glass_info(self, 
                "Bulk scrape",
                "No pending products selected.\nImport a list and leave status pending.",
            )
            return
        self._bulk_cancel = False
        self._set_busy(True, f"Bulk scrape 0/{len(jobs)}…")
        notes = self.notes_var.get().strip()
        threading.Thread(
            target=self._bulk_scrape_worker, args=(cfg, notes, jobs), daemon=True
        ).start()

    def _bulk_scrape_worker(self, cfg: dict, extra_notes: str, jobs: list[dict]):
        ok_n = 0
        err_n = 0
        try:
            driver = connect_to_chrome(
                status_cb=lambda m: self._set_progress(5, m[:72])
            )
            total = len(jobs)
            for i, job in enumerate(jobs):
                if self._bulk_cancel:
                    break
                pid = str(job.get("product_id") or "")
                name = str(job.get("name") or pid)
                base_pct = int(100 * i / max(total, 1))
                self._set_progress(
                    base_pct,
                    f"Bulk scrape {i + 1}/{total} · {name[:50]}",
                )
                job["status"] = "scraping"
                job["error_message"] = ""
                self.after(0, lambda j=job: self._bulk_update_tree_row(j))

                try:
                    if not chrome_debug_available():
                        raise RuntimeError("Chrome debug port closed.")
                    edit_url = str(job.get("edit_url") or "")
                    open_product_edit(driver, edit_url)

                    # Progress callback maps into overall bar slice
                    def _prog(msg, _i=i, _t=total):
                        mid = int(100 * (_i + 0.35) / max(_t, 1))
                        self._set_progress(mid, f"{_i + 1}/{_t} · {str(msg)[:60]}")

                    page_context = scan_product_page(
                        driver,
                        product_url=edit_url,
                        progress_cb=_prog,
                    )
                    # Merge category cache from first successful scrape
                    def _cat_tree_score(items: list) -> tuple[int, int]:
                        if not items:
                            return (0, 0)
                        n_parent = sum(
                            1
                            for c in items
                            if isinstance(c, dict) and str(c.get("parent_id") or "").strip()
                        )
                        return (n_parent, len(items))

                    cats = page_context.get("available_category_options") or []
                    cache = self._bulk_category_cache or []
                    if cache and (
                        not cats or len(cats) < len(cache) // 2
                    ):
                        # keep per-product selected; union labels by id
                        by_id = {
                            str(c.get("value") or c.get("id")): dict(c)
                            for c in cache
                            if isinstance(c, dict)
                        }
                        for c in cats:
                            if not isinstance(c, dict):
                                continue
                            vid = str(c.get("value") or c.get("id"))
                            prev = by_id.get(vid)
                            if not prev:
                                by_id[vid] = dict(c)
                            else:
                                # Prefer richer parent_id / path from fresher scrape
                                if str(c.get("parent_id") or "").strip() and not str(
                                    prev.get("parent_id") or ""
                                ).strip():
                                    prev["parent_id"] = c.get("parent_id")
                                if c.get("selected"):
                                    prev["selected"] = True
                        page_context["available_category_options"] = list(by_id.values())
                    elif cats and (
                        not cache
                        or _cat_tree_score(cats) > _cat_tree_score(cache)
                    ):
                        self._bulk_category_cache = list(cats)

                    product_title = str(page_context.get("product_name") or "").strip()
                    if not product_title:
                        raise RuntimeError("Product Name is empty on the form.")

                    ai_data = generate_product_fields(
                        api_key=cfg["openai_api_key"],
                        model=cfg["openai_model"],
                        content_language=cfg["content_language"],
                        page_context=page_context,
                        product_title=product_title,
                        extra_notes=extra_notes,
                    )
                    query = craft_image_search_query(
                        api_key=cfg["openai_api_key"],
                        model=cfg["openai_model"],
                        product_title=product_title,
                    )
                    results = search_product_images(
                        query,
                        max_results=12,
                        google_api_key=str(cfg.get("google_api_key") or ""),
                        google_cse_id=str(cfg.get("google_cse_id") or ""),
                        backend=str(cfg.get("image_search_backend") or "auto"),
                    )
                    prepared = []
                    for item in results[:12]:
                        thumb_path = download_thumbnail_preview(
                            item.get("thumbnail") or item.get("url"),
                            item["id"],
                            alternate_urls=[item.get("url") or "", item.get("thumbnail") or ""],
                            page_url=item.get("page_url") or item.get("source") or None,
                        )
                        item = dict(item)
                        item["thumb_path"] = str(thumb_path) if thumb_path else ""
                        prepared.append(item)

                    job["page_context"] = page_context
                    job["ai_payload"] = ai_data
                    job["image_results"] = prepared
                    job["image_query"] = query
                    job["name"] = product_title
                    job["fill_payload"] = None
                    job["image_jobs"] = None
                    job["status"] = "ready"
                    job["approved"] = True  # ready for review/fill; user can toggle off
                    job["error_message"] = ""
                    # logged in user global refresh
                    if isinstance(page_context.get("logged_in_user"), dict):
                        self._logged_in_user = page_context["logged_in_user"]
                    ok_n += 1
                except Exception as exc:
                    job["status"] = "error"
                    job["error_message"] = str(exc)[:200]
                    job["approved"] = False
                    err_n += 1

                self.after(0, lambda j=job: self._bulk_update_tree_row(j))

            def _done():
                self.progress_frame.pack_forget()
                self._bulk_rebuild_tree()
                cancelled = " (cancelled)" if self._bulk_cancel else ""
                self._set_busy(
                    False,
                    f"Bulk scrape done{cancelled}: {ok_n} ready · {err_n} errors",
                )
                # Load first ready job into review
                ready = next(
                    (j for j in self._bulk_jobs if str(j.get("status")) == "ready"),
                    None,
                )
                if ready:
                    self._bulk_load_job_into_review(ready)
                glass_info(self, 
                    "Bulk scrape",
                    f"Scrape finished{cancelled}.\n\nReady: {ok_n}\nErrors: {err_n}\n\n"
                    "Review jobs (double-click), then Fill approved.\n"
                    "Still never auto-saves — Save in CS-Cart yourself.",
                )

            self.after(0, _done)
        except Exception as exc:
            msg = str(exc)
            self.after(0, lambda: self.progress_frame.pack_forget())
            self.after(0, lambda: self._set_busy(False))
            self.after(0, lambda m=msg: glass_error(self, "Bulk scrape", m))

    def bulk_start_fill(self):
        if self._busy:
            return
        if not chrome_debug_available():
            glass_error(self, 
                "Chrome not connected",
                "Run START.bat and keep the debug Chrome session open.",
            )
            return
        self._store_active_bulk_review()
        jobs = [
            j
            for j in self._bulk_jobs
            if j.get("selected", True)
            and j.get("approved")
            and str(j.get("status")) == "ready"
        ]
        if not jobs:
            glass_info(self, 
                "Bulk fill",
                "No approved ready products.\nScrape queue, then Approve (or double-click to review).",
            )
            return
        auto_save = bool(self.bulk_auto_save_var.get())
        mode_txt = (
            "AUTO-SAVE is ON: each product will be filled, then the app clicks "
            "«Save / შენახვა» in Chrome before opening the next.\n\n"
            "Without Save, CS-Cart throws away the form → products look empty."
            if auto_save
            else "AUTO-SAVE is OFF: after each fill YOU must click Save in Chrome, "
            "THEN click «I saved — Next» in the app.\n\n"
            "If you open the next product without Save, fields will be EMPTY."
        )
        if not glass_yesno(self, 
            "Bulk fill",
            f"Fill {len(jobs)} product(s) one-by-one?\n\n{mode_txt}",
        ):
            return
        self._bulk_cancel = False
        self._set_busy(True, f"Bulk fill 0/{len(jobs)}…")
        threading.Thread(
            target=self._bulk_fill_worker,
            args=(jobs, auto_save),
            daemon=True,
        ).start()

    def _main_thread_action(
        self,
        title: str,
        message: str,
        actions: list[tuple[str, str]],
        *,
        width: int = 540,
        height: int = 320,
    ) -> str:
        """Show multi-button glass dialog from a worker thread; return chosen key."""
        box: dict[str, str] = {"action": "cancel"}
        done = threading.Event()

        def _show():
            try:
                box["action"] = glass_actions(
                    self, title, message, actions, width=width, height=height
                )
            except Exception:
                box["action"] = "cancel"
            finally:
                done.set()

        self.after(0, _show)
        done.wait(timeout=3600)
        return str(box.get("action") or "cancel")

    def _bulk_fill_worker(self, jobs: list[dict], auto_save: bool = False):
        """
        Fill products SEQUENTIALLY and only move on after Save.

        Without waiting, Chrome navigates to the next product and discards
        unsaved form fields — so it looks like “only the last one filled”.
        """
        filled = 0
        saved_n = 0
        errors = 0
        lines: list[str] = []
        try:
            driver = connect_to_chrome(status_cb=lambda m: self._set_status(m[:80]))
            total = len(jobs)
            for i, job in enumerate(jobs):
                if self._bulk_cancel:
                    break
                pid = str(job.get("product_id") or "")
                name = str(job.get("name") or pid)
                remaining = total - i - 1
                self._set_progress(
                    int(100 * i / max(total, 1)),
                    f"Bulk fill {i + 1}/{total} · {name[:50]}",
                )
                job["status"] = "filling"
                self.after(0, lambda j=job: self._bulk_update_tree_row(j))
                # Sync review UI (blocking) so store/load does not race with fill
                load_done = threading.Event()

                def _load(j=job):
                    try:
                        self._bulk_load_job_into_review(j)
                    except Exception:
                        pass
                    finally:
                        load_done.set()

                self.after(0, _load)
                load_done.wait(timeout=30)
                try:
                    if not chrome_debug_available():
                        raise RuntimeError("Chrome debug port closed.")
                    edit_url = str(job.get("edit_url") or "")
                    open_product_edit(driver, edit_url)
                    # Bring focus to product (helps user see Save button)
                    try:
                        driver.switch_to.window(driver.current_window_handle)
                    except Exception:
                        pass
                    payload, image_jobs = self._bulk_payload_for_job(job)
                    # Cache payload on job for later re-fill
                    if not job.get("fill_payload"):
                        job["fill_payload"] = payload
                    if not job.get("image_jobs") and image_jobs:
                        job["image_jobs"] = image_jobs
                    self._product_url = edit_url
                    self._product_title = name
                    result = apply_product_fill(driver, payload, product_url=edit_url)
                    verify = verify_product_form_filled(driver)
                    if not verify.get("ok"):
                        # One retry fill if DOM still empty
                        time.sleep(0.4)
                        result = apply_product_fill(driver, payload, product_url=edit_url)
                        verify = verify_product_form_filled(driver)
                    if not verify.get("ok"):
                        raise RuntimeError(
                            "Fill did not write fields on the page "
                            f"(name_len={len(str(verify.get('name') or ''))}, "
                            f"descr_len={verify.get('description_len', 0)}). "
                            "Stay on product edit and try Fill again."
                        )

                    image_note = "No images."
                    if image_jobs:
                        prepared_main = None
                        prepared_extra: list[str] = []
                        for im_job in image_jobs:
                            meta = im_job.get("meta") or {}
                            try:
                                info = prepare_image_for_upload(
                                    im_job["url"],
                                    image_id=im_job["id"],
                                    alternate_urls=[
                                        meta.get("thumbnail") or "",
                                        meta.get("url") or "",
                                    ],
                                    page_url=meta.get("page_url")
                                    or meta.get("source")
                                    or None,
                                )
                            except Exception:
                                continue
                            path = info["path"]
                            if im_job.get("is_main"):
                                prepared_main = path
                            else:
                                prepared_extra.append(path)
                        if not prepared_main and prepared_extra:
                            prepared_main = prepared_extra.pop(0)
                        if prepared_main or prepared_extra:
                            upload_images_to_product(
                                driver,
                                main_path=prepared_main,
                                additional_paths=prepared_extra,
                                product_url=edit_url,
                            )
                            image_note = "Images attached."
                    filled += 1

                    # Always need Save for CS-Cart to keep data
                    if auto_save:
                        self._set_progress(
                            int(100 * (i + 0.5) / max(total, 1)),
                            f"Saving {i + 1}/{total} in CS-Cart…",
                        )
                        # Repair labels once more right before Save
                        try:
                            driver.execute_script(
                                "try{document.body&&document.body.click()}catch(e){}"
                            )
                        except Exception:
                            pass
                        save_info = click_product_save(
                            driver, product_url=edit_url, wait_s=3.0
                        )
                        save_ok = bool(save_info.get("ok"))
                        if save_ok:
                            saved_n += 1
                            job["status"] = "saved"
                            how = save_info.get("how") or "save"
                            job["error_message"] = (
                                f"Filled + Save ({how}). {image_note}"
                            )
                            lines.append(f"OK+SAVE {pid}: {name[:36]}")
                            # brief pause so save finishes before next product
                            time.sleep(0.8)
                        else:
                            job["status"] = "filled"
                            job["error_message"] = (
                                f"Filled but Auto-Save failed "
                                f"({save_info.get('reason') or 'unknown'}). "
                                f"YOU must click Save in Chrome. {image_note}"
                            )
                            lines.append(
                                f"FILL/no-save {pid}: {save_info.get('reason') or 'Save failed'}"
                            )
                            act = self._main_thread_action(
                                f"Save failed · {i + 1}/{total}",
                                f"Product #{pid}\n{name}\n\n"
                                "Fields were written on the page, but auto-click Save failed.\n\n"
                                "→ Switch to Chrome and click «Save / შენახვა» NOW.\n"
                                "→ Then click «I saved — Next».\n\n"
                                "If you go to the next product first, this one will be EMPTY.",
                                [
                                    ("next", "I saved — Next"),
                                    ("skip", "Skip rest"),
                                    ("cancel", "Cancel bulk"),
                                ],
                                height=360,
                            )
                            if act == "next":
                                saved_n += 1
                                job["status"] = "saved"
                                job["error_message"] = f"Filled + manual Save. {image_note}"
                            elif act in ("cancel", "skip"):
                                if act == "cancel":
                                    self._bulk_cancel = True
                                break
                    else:
                        job["status"] = "filled"
                        job["error_message"] = (
                            f"Filled — SAVE REQUIRED in Chrome. {image_note}"
                        )
                        lines.append(f"FILLED {pid}: {name[:40]}")
                        is_last = remaining == 0
                        act = self._main_thread_action(
                            f"SAVE in Chrome · {i + 1}/{total}",
                            f"Product #{pid}\n{name}\n\n"
                            "Fill is only in the browser form — not saved yet.\n\n"
                            "1) Go to Chrome\n"
                            "2) Click «Save / შენახვა»\n"
                            "3) Wait until CS-Cart shows success\n"
                            + (
                                "4) Click Done"
                                if is_last
                                else f"4) Click «I saved — Next» ({remaining} left)"
                            )
                            + "\n\n⚠ Without Save this product stays EMPTY.",
                            [
                                ("next", "Done" if is_last else "I saved — Next"),
                                ("skip", "Skip rest"),
                                ("cancel", "Cancel bulk"),
                            ],
                            height=380,
                        )
                        if act == "next":
                            saved_n += 1
                            job["status"] = "saved"
                            job["error_message"] = f"Filled + you confirmed Save. {image_note}"
                        elif act in ("cancel", "skip"):
                            if act == "cancel":
                                self._bulk_cancel = True
                            if not is_last:
                                break
                    _ = result  # debug hook
                except Exception as exc:
                    job["status"] = "error"
                    job["error_message"] = str(exc)[:200]
                    errors += 1
                    lines.append(f"ERR {pid}: {str(exc)[:60]}")
                    if not self._bulk_cancel and i + 1 < total:
                        act = self._main_thread_action(
                            f"Error on {i + 1}/{total}",
                            f"#{pid} {name}\n\n{exc}\n\nContinue with remaining products?",
                            [
                                ("next", "Continue"),
                                ("cancel", "Stop bulk"),
                            ],
                        )
                        if act == "cancel":
                            self._bulk_cancel = True
                            break
                self.after(0, lambda j=job: self._bulk_update_tree_row(j))
                time.sleep(0.35)

            def _done():
                self.progress_frame.pack_forget()
                self._bulk_rebuild_tree()
                cancelled = " (cancelled)" if self._bulk_cancel else ""
                self._set_busy(
                    False,
                    f"Bulk fill done{cancelled}: {filled} filled · "
                    f"{saved_n} confirmed · {errors} errors",
                )
                body = "\n".join(lines[:40])
                if len(lines) > 40:
                    body += f"\n… +{len(lines) - 40} more"
                glass_info(self, 
                    "Bulk fill",
                    f"Finished{cancelled}.\n\n"
                    f"Filled: {filled}\n"
                    f"Save confirmed / auto-saved: {saved_n}\n"
                    f"Errors: {errors}\n\n"
                    f"{body}",
                )

            self.after(0, _done)
        except Exception as exc:
            msg = str(exc)
            self.after(0, lambda: self.progress_frame.pack_forget())
            self.after(0, lambda: self._set_busy(False))
            self.after(0, lambda m=msg: glass_error(self, "Bulk fill", m))

    # ---------- Fill ----------
    def start_fill(self):
        if self._busy:
            return
        if not self._scraped:
            glass_info(self, "Fill", "Run Scrape first, then review, then Fill.")
            return
        if not chrome_debug_available():
            glass_error(self, 
                "Chrome not connected",
                "Run START.bat and keep the product page open.",
            )
            return

        # If reviewing a bulk job, persist panel edits first
        self._store_active_bulk_review()

        payload = self._collect_ai_payload()
        jobs = self._selected_image_jobs()
        if self._bulk_active_id:
            bj = self._bulk_job_by_id(self._bulk_active_id)
            if bj:
                bj["fill_payload"] = payload
                bj["image_jobs"] = jobs
                # Prefer bulk job URL
                if bj.get("edit_url"):
                    self._product_url = str(bj["edit_url"])
        if not any(
            [
                payload.get("full_description"),
                payload.get("promo_text"),
                payload.get("feature_values"),
                payload.get("categories"),
                payload.get("videos"),
                jobs,
            ]
        ):
            if not glass_yesno(self, 
                "Fill",
                "Almost nothing is selected to fill.\nContinue anyway with name/SEO fields only?",
            ):
                return

        self._set_busy(True, "Filling CS-Cart form…")
        threading.Thread(
            target=self._fill_worker, args=(payload, jobs), daemon=True
        ).start()

    def _fill_worker(self, payload: dict, image_jobs: list[dict]):
        try:
            self._set_status("Attaching to local Chrome…")
            driver = connect_to_chrome(status_cb=lambda m: self._set_status(m[:80]))
            url = self._product_url or find_product_tab(driver)
            if url and "products.update" in url:
                try:
                    open_product_edit(driver, url)
                    url = driver.current_url or url
                except Exception:
                    url = find_product_tab(driver) or url
            else:
                url = find_product_tab(driver)
            if not url:
                raise RuntimeError("Open a product edit page (products.update) first.")

            self._set_status("Writing texts · specs · categories · video…")
            result = apply_product_fill(driver, payload, product_url=url)

            image_note = "No images selected."
            if image_jobs:
                self._set_status("Compressing & attaching images…")
                prepared_main = None
                prepared_extra: list[str] = []
                size_lines = []
                fail_lines = []
                for job in image_jobs:
                    meta = job.get("meta") or {}
                    try:
                        info = prepare_image_for_upload(
                            job["url"],
                            image_id=job["id"],
                            alternate_urls=[
                                meta.get("thumbnail") or "",
                                meta.get("url") or "",
                            ],
                            page_url=meta.get("page_url") or meta.get("source") or None,
                        )
                    except Exception as exc:
                        fail_lines.append(f"{job['id']}: {exc}")
                        continue
                    path = info["path"]
                    kb = info["bytes"] / 1024
                    size_lines.append(
                        f"{'MAIN' if job['is_main'] else 'extra'}: {kb:.1f} KB"
                    )
                    if job["is_main"]:
                        prepared_main = path
                    else:
                        prepared_extra.append(path)

                if not prepared_main and prepared_extra:
                    prepared_main = prepared_extra.pop(0)

                if prepared_main or prepared_extra:
                    report = upload_images_to_product(
                        driver,
                        main_path=prepared_main,
                        additional_paths=prepared_extra,
                        product_url=url,
                    )
                    ok_main = report.get("main", {}).get("ok")
                    ok_extra = sum(
                        1 for a in report.get("additional") or [] if a.get("ok")
                    )
                    image_note = (
                        f"Main: {'OK' if ok_main else report.get('main', {}).get('error')}\n"
                        f"Additional OK: {ok_extra}\n"
                        + "\n".join(size_lines)
                    )
                    if fail_lines:
                        image_note += "\nSkipped: " + "; ".join(fail_lines[:4])
                else:
                    image_note = "Image download failed:\n" + "\n".join(fail_lines[:5])

            try:
                debug_path = Path(__file__).resolve().parent / "last_fill_debug.json"
                debug_path.write_text(
                    json.dumps(
                        {
                            "url": url,
                            "product_title": self._product_title,
                            "payload_keys": list(payload.keys()),
                            "fill_result": result,
                            "image_note": image_note,
                        },
                        ensure_ascii=False,
                        indent=2,
                    ),
                    encoding="utf-8",
                )
            except Exception:
                pass

            notes = str(payload.get("notes_for_user") or "")
            summary = (
                f"Product:\n{self._product_title}\n\n"
                + summarize_results(result, notes, image_note)
            )
            self._set_status("Fill done — review in Chrome, then Save there.")
            # Mark bulk job filled if this was the active bulk product
            if self._bulk_active_id:
                bj = self._bulk_job_by_id(self._bulk_active_id)
                if bj and str(bj.get("product_id")) in str(url or ""):
                    bj["status"] = "filled"
                    bj["error_message"] = "Filled (single Fill page)"
                    self.after(0, lambda j=bj: self._bulk_update_tree_row(j))
            self.after(0, lambda: glass_info(self, "Fill complete", summary))
        except Exception as exc:
            msg = str(exc)
            self._set_status("Fill failed.")
            self.after(0, lambda m=msg: glass_error(self, "Fill", m))
        finally:
            self._clear_busy()


if __name__ == "__main__":
    AcousticSmartFiller().mainloop()
