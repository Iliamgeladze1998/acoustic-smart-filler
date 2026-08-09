"""
Acoustic Smart Filler — visual setup wizard (Windows UI, no CMD).
Shows live progress; never waits for a keypress in a hidden console.
"""

from __future__ import annotations

import os
import queue
import shutil
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path
import tkinter as tk
from tkinter import messagebox, ttk

ROOT = Path(__file__).resolve().parent
APP = ROOT / "App"
REQ = APP / "requirements.txt"
CFG = APP / "config.json"
CFG_EX = APP / "config.example.json"
ICON_VBS = ROOT / "CreateDesktopIcon.vbs"
START_VBS = APP / "START.vbs"

C = {
    "bg": "#e6ebe8",
    "bg_deep": "#102027",
    "card": "#f7f9f8",
    "card_border": "#c5d0cb",
    "accent": "#b86a2b",
    "action": "#1b6b5c",
    "action_hi": "#228576",
    "text": "#142028",
    "muted": "#5a6b73",
    "white": "#ffffff",
    "ok": "#1b6b5c",
    "log_bg": "#0f1c22",
    "log_fg": "#c8d6d0",
}

# Hide child console windows; never attach a console that can steal "press any key"
CREATE_NO_WINDOW = 0x08000000
DETACHED_PROCESS = 0x00000008


def _to_console_python(path: str) -> str:
    """Prefer python.exe over pythonw.exe for pip (reliable stdout)."""
    p = Path(path)
    name = p.name.lower()
    if name == "pythonw.exe":
        alt = p.with_name("python.exe")
        if alt.is_file():
            return str(alt)
    if name == "pyw.exe":
        alt = p.with_name("py.exe")
        if alt.is_file():
            return str(alt)
    return path


def _find_python() -> str | None:
    candidates: list[str] = []
    for name in ("python", "py"):
        p = shutil.which(name)
        if p:
            candidates.append(p)
    local = Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Python"
    if local.is_dir():
        for sub in sorted(local.iterdir(), reverse=True):
            for exe in ("python.exe", "pythonw.exe"):
                cand = sub / exe
                if cand.is_file():
                    candidates.append(str(cand))
    for c in (
        r"C:\Python313\python.exe",
        r"C:\Python312\python.exe",
        r"C:\Python311\python.exe",
        r"C:\Python310\python.exe",
    ):
        if Path(c).is_file():
            candidates.append(c)
    seen: set[str] = set()
    ordered: list[str] = []
    for c in candidates:
        c = _to_console_python(c)
        key = c.lower()
        if key in seen:
            continue
        seen.add(key)
        ordered.append(c)
    # Prefer real python.exe paths over bare "py"
    for c in ordered:
        if Path(c).name.lower() == "python.exe" and Path(c).is_file():
            return c
    return ordered[0] if ordered else None


def _hidden_popen_kwargs() -> dict:
    if sys.platform != "win32":
        return {}
    # No new window + stdin closed (prevents hidden "Press any key")
    si = subprocess.STARTUPINFO()
    si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    si.wShowWindow = 0  # SW_HIDE
    return {
        "creationflags": CREATE_NO_WINDOW,
        "startupinfo": si,
        "stdin": subprocess.DEVNULL,
    }


def _run_cmd(
    args: list[str],
    log_q: queue.Queue,
    *,
    env: dict | None = None,
    step_cb=None,
) -> int:
    """
    Run process with stdout captured live (including \\r progress).
    stdin is DEVNULL so nothing can wait for a key.
    """
    run_env = os.environ.copy()
    run_env["PYTHONUNBUFFERED"] = "1"
    run_env["PYTHONIOENCODING"] = "utf-8"
    run_env["PIP_NO_INPUT"] = "1"
    run_env["PIP_DISABLE_PIP_VERSION_CHECK"] = "1"
    run_env["PIP_PROGRESS_BAR"] = "off"
    # Never open an interactive page / prompt
    run_env["CI"] = "1"
    if env:
        run_env.update(env)

    try:
        proc = subprocess.Popen(
            args,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            bufsize=0,
            env=run_env,
            **_hidden_popen_kwargs(),
        )
    except Exception as exc:
        log_q.put(f"ERROR: could not start process: {exc}\n")
        return 1

    assert proc.stdout is not None
    buf = b""
    last_emit = time.time()
    try:
        while True:
            chunk = proc.stdout.read(256)
            if not chunk:
                break
            buf += chunk
            # Emit on newline or carriage-return (pip progress)
            while True:
                nl = buf.find(b"\n")
                cr = buf.find(b"\r")
                if nl < 0 and cr < 0:
                    break
                if nl < 0:
                    cut = cr
                elif cr < 0:
                    cut = nl
                else:
                    cut = min(nl, cr)
                part = buf[:cut].decode("utf-8", "replace").strip()
                buf = buf[cut + 1 :]
                if part:
                    log_q.put(part + "\n")
                    if step_cb:
                        step_cb(part)
            now = time.time()
            if now - last_emit > 2.0:
                log_q.put("… still working (please wait) …\n")
                last_emit = now
        leftover = buf.decode("utf-8", "replace").strip()
        if leftover:
            log_q.put(leftover + "\n")
    except Exception as exc:
        log_q.put(f"ERROR reading process output: {exc}\n")
        try:
            proc.kill()
        except Exception:
            pass
    return int(proc.wait() or 0)


def ensure_config() -> str:
    if CFG.is_file():
        return "config.json already present — kept your settings."
    if CFG_EX.is_file():
        shutil.copy2(CFG_EX, CFG)
        return "Created App\\config.json from template."
    CFG.write_text(
        '{\n  "openai_api_key": "",\n  "openai_model": "gpt-4o-mini",\n'
        '  "content_language": "Georgian"\n}\n',
        encoding="utf-8",
    )
    return "Created a new App\\config.json."


def create_desktop_icon() -> str:
    """Create desktop shortcut without any interactive / pause prompts."""
    if not START_VBS.is_file():
        raise RuntimeError("App\\START.vbs missing — cannot create shortcut.")

    desktop = Path(os.environ.get("USERPROFILE", str(Path.home()))) / "Desktop"
    # OneDrive Desktop fallback
    if not desktop.is_dir():
        od = Path(os.environ.get("USERPROFILE", str(Path.home()))) / "OneDrive" / "Desktop"
        if od.is_dir():
            desktop = od
    link = desktop / "Acoustic Smart Filler.lnk"

    # 1) PowerShell COM — non-interactive, no pause
    # Escape single quotes for PowerShell single-quoted strings
    def _psq(s: str) -> str:
        return s.replace("'", "''")

    ps = (
        f"$p = '{_psq(str(link))}'; "
        f"$t = (New-Object -ComObject WScript.Shell).CreateShortcut($p); "
        f"$t.TargetPath = 'wscript.exe'; "
        f"$t.Arguments = '//nologo \"{_psq(str(START_VBS))}\"'; "
        f"$t.WorkingDirectory = '{_psq(str(APP))}'; "
        f"$t.Description = 'Acoustic Smart Filler'; "
        f"$t.WindowStyle = 1; "
        # Chrome icon if available
        f"$chrome = $env:ProgramFiles + '\\Google\\Chrome\\Application\\chrome.exe'; "
        f"if (Test-Path $chrome) {{ $t.IconLocation = $chrome + ',0' }} "
        f"$t.Save(); "
        f"if (Test-Path $p) {{ Write-Output $p }} else {{ exit 1 }}"
    )
    r = subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-WindowStyle",
            "Hidden",
            "-Command",
            ps,
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        **_hidden_popen_kwargs(),
    )
    if r.returncode == 0 and link.is_file():
        return f"Desktop icon ready:\n{link}"

    # 2) Fallback VBS (also non-interactive with //nologo)
    if ICON_VBS.is_file():
        r2 = subprocess.run(
            ["cscript", "//nologo", "//B", str(ICON_VBS)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=20,
            **_hidden_popen_kwargs(),
        )
        if link.is_file() or r2.returncode == 0:
            return (r2.stdout or "").strip() or f"Desktop icon ready:\n{link}"
        raise RuntimeError(
            (r.stderr or r.stdout or "").strip()
            or (r2.stderr or r2.stdout or "").strip()
            or "Could not create desktop shortcut."
        )

    raise RuntimeError(
        (r.stderr or r.stdout or "").strip() or "Could not create desktop shortcut."
    )


class SetupWizard(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("Acoustic Smart Filler — Setup")
        self.configure(bg=C["bg"])
        self.minsize(540, 600)
        self.geometry("600x620")
        self.resizable(True, True)

        self._log_q: queue.Queue = queue.Queue()
        self._ui_q: queue.Queue = queue.Queue()  # (kind, payload)
        self._busy = False
        self._t0 = 0.0
        self.py = _find_python()

        self._build()
        self._refresh_status()
        self.after(80, self._pump)

    def _build(self) -> None:
        head = tk.Frame(self, bg=C["bg_deep"], height=88)
        head.pack(fill="x")
        head.pack_propagate(False)
        tk.Label(
            head,
            text="Acoustic Smart Filler",
            font=("Segoe UI Semibold", 18),
            bg=C["bg_deep"],
            fg=C["white"],
        ).pack(anchor="w", padx=22, pady=(16, 0))
        tk.Label(
            head,
            text="Setup · packages · config · desktop icon",
            font=("Segoe UI", 10),
            bg=C["bg_deep"],
            fg="#9bb0ae",
        ).pack(anchor="w", padx=22, pady=(4, 0))

        body = tk.Frame(self, bg=C["bg"])
        body.pack(fill="both", expand=True, padx=18, pady=16)

        card = tk.Frame(
            body,
            bg=C["card"],
            highlightbackground=C["card_border"],
            highlightthickness=1,
        )
        card.pack(fill="x", pady=(0, 10))

        self.status_var = tk.StringVar(value="Checking…")
        tk.Label(
            card,
            textvariable=self.status_var,
            font=("Segoe UI", 10),
            bg=C["card"],
            fg=C["text"],
            justify="left",
            wraplength=540,
            anchor="w",
        ).pack(fill="x", padx=16, pady=12)

        # Live step status
        self.step_var = tk.StringVar(value="Ready — click Run setup.")
        tk.Label(
            body,
            textvariable=self.step_var,
            font=("Segoe UI Semibold", 11),
            bg=C["bg"],
            fg=C["action"],
            anchor="w",
            wraplength=540,
            justify="left",
        ).pack(fill="x", pady=(0, 6))

        # Step checkmarks
        self.s1 = tk.StringVar(value="○  1 / 3  Install packages")
        self.s2 = tk.StringVar(value="○  2 / 3  Create config.json")
        self.s3 = tk.StringVar(value="○  3 / 3  Desktop icon")
        for var in (self.s1, self.s2, self.s3):
            tk.Label(
                body,
                textvariable=var,
                font=("Segoe UI", 10),
                bg=C["bg"],
                fg=C["muted"],
                anchor="w",
            ).pack(fill="x")

        btn_row = tk.Frame(body, bg=C["bg"])
        btn_row.pack(fill="x", pady=(12, 8))

        self.run_btn = tk.Button(
            btn_row,
            text="  Run setup  ",
            font=("Segoe UI Semibold", 11),
            bg=C["action"],
            fg=C["white"],
            activebackground=C["action_hi"],
            activeforeground=C["white"],
            relief="flat",
            cursor="hand2",
            padx=16,
            pady=8,
            command=self._on_run,
        )
        self.run_btn.pack(side="left")

        tk.Button(
            btn_row,
            text="Desktop icon only",
            font=("Segoe UI", 10),
            bg=C["card"],
            fg=C["text"],
            relief="flat",
            highlightthickness=1,
            highlightbackground=C["card_border"],
            cursor="hand2",
            padx=12,
            pady=8,
            command=self._on_icon_only,
        ).pack(side="left", padx=(10, 0))

        tk.Button(
            btn_row,
            text="Open App folder",
            font=("Segoe UI", 10),
            bg=C["card"],
            fg=C["text"],
            relief="flat",
            highlightthickness=1,
            highlightbackground=C["card_border"],
            cursor="hand2",
            padx=12,
            pady=8,
            command=self._open_app_folder,
        ).pack(side="left", padx=(10, 0))

        # Determinate progress 0–100
        self.prog = ttk.Progressbar(body, mode="determinate", maximum=100)
        self.prog.pack(fill="x", pady=(10, 4))
        self.pct_var = tk.StringVar(value="0%")
        tk.Label(
            body,
            textvariable=self.pct_var,
            font=("Segoe UI", 9),
            bg=C["bg"],
            fg=C["muted"],
            anchor="e",
        ).pack(fill="x")

        tk.Label(
            body,
            text="Log (live)",
            font=("Segoe UI", 9),
            bg=C["bg"],
            fg=C["muted"],
        ).pack(anchor="w", pady=(6, 0))

        self.log = tk.Text(
            body,
            height=12,
            bg=C["log_bg"],
            fg=C["log_fg"],
            insertbackground=C["log_fg"],
            font=("Consolas", 9),
            relief="flat",
            wrap="word",
        )
        self.log.pack(fill="both", expand=True, pady=(4, 0))
        self.log.insert(
            "end",
            "Ready. Click “Run setup”.\n"
            "Progress appears here while packages install (can take 1–3 minutes).\n",
        )
        self.log.configure(state="disabled")

        foot = tk.Frame(self, bg=C["bg"])
        foot.pack(fill="x", padx=18, pady=(0, 12))
        tk.Label(
            foot,
            text="No black CMD window. Nothing asks you to press a key.",
            font=("Segoe UI", 9),
            bg=C["bg"],
            fg=C["muted"],
        ).pack(side="left")

    def _append(self, text: str) -> None:
        self.log.configure(state="normal")
        self.log.insert("end", text)
        self.log.see("end")
        self.log.configure(state="disabled")

    def _set_pct(self, pct: float, note: str | None = None) -> None:
        pct = max(0.0, min(100.0, float(pct)))
        self.prog["value"] = pct
        self.pct_var.set(f"{int(pct)}%")
        if note:
            self.step_var.set(note)

    def _pump(self) -> None:
        try:
            while True:
                line = self._log_q.get_nowait()
                self._append(line if str(line).endswith("\n") else str(line) + "\n")
        except queue.Empty:
            pass
        try:
            while True:
                kind, payload = self._ui_q.get_nowait()
                if kind == "pct":
                    self._set_pct(payload[0], payload[1] if len(payload) > 1 else None)
                elif kind == "step":
                    name, text = payload
                    if name == "s1":
                        self.s1.set(text)
                    elif name == "s2":
                        self.s2.set(text)
                    elif name == "s3":
                        self.s3.set(text)
                elif kind == "step_msg":
                    self.step_var.set(str(payload))
                elif kind == "done":
                    self._finish(*payload)
        except queue.Empty:
            pass
        # Elapsed ticker while busy
        if self._busy and self._t0:
            el = int(time.time() - self._t0)
            cur = self.step_var.get()
            base = cur.split(" · ")[0] if " · " in cur else cur
            if "Installing" in base or "Step" in base or "packages" in base.lower():
                self.step_var.set(f"{base.split(' (')[0]}  ({el}s elapsed)")
        self.after(100, self._pump)

    def _refresh_status(self) -> None:
        lines = []
        if not APP.is_dir() or not (APP / "app.py").is_file():
            lines.append("⚠ App folder is missing or incomplete next to this wizard.")
        else:
            lines.append(f"App folder: {APP}")
        if self.py:
            lines.append(f"Python: {self.py}")
        else:
            lines.append(
                "⚠ Python not found. Install Python 3 from python.org "
                "and tick “Add python.exe to PATH”, then reopen Setup."
            )
        if CFG.is_file():
            lines.append("config.json: found")
        else:
            lines.append("config.json: will be created")
        self.status_var.set("\n".join(lines))
        if not self.py or not (APP / "app.py").is_file():
            self.run_btn.configure(state="disabled", bg="#7a8a86")

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        if busy:
            self._t0 = time.time()
            self.run_btn.configure(state="disabled", bg="#7a8a86")
        else:
            self._t0 = 0
            if self.py and (APP / "app.py").is_file():
                self.run_btn.configure(state="normal", bg=C["action"])

    def _open_app_folder(self) -> None:
        if APP.is_dir():
            os.startfile(str(APP))  # type: ignore[attr-defined]
        else:
            messagebox.showerror("Setup", "App folder not found.")

    def _on_icon_only(self) -> None:
        if self._busy:
            return
        try:
            self._append("Creating desktop icon…\n")
            msg = create_desktop_icon()
            self._append(msg + "\n")
            self.s3.set("●  3 / 3  Desktop icon — done")
            messagebox.showinfo("Desktop icon", msg)
        except Exception as exc:
            messagebox.showerror("Desktop icon", str(exc))

    def _finish(self, ok: bool, msg: str) -> None:
        self._set_busy(False)
        self._refresh_status()
        if ok:
            self._set_pct(100, "Setup complete.")
            messagebox.showinfo("Setup complete", msg)
        else:
            self.step_var.set("Setup failed — see log.")
            messagebox.showerror("Setup incomplete", msg)

    def _on_run(self) -> None:
        if self._busy:
            return
        if not self.py:
            if messagebox.askyesno(
                "Python required",
                "Python was not found on this PC.\n\nOpen the download page now?",
            ):
                webbrowser.open("https://www.python.org/downloads/windows/")
            return
        if not REQ.is_file():
            messagebox.showerror("Setup", f"Missing requirements file:\n{REQ}")
            return

        self._set_busy(True)
        self._set_pct(2, "Starting setup…")
        self.s1.set("…  1 / 3  Install packages")
        self.s2.set("○  2 / 3  Create config.json")
        self.s3.set("○  3 / 3  Desktop icon")
        self._append("\n—— Setup started ——\n")
        self._append("No keypress needed. Please wait while packages install.\n")

        py = self.py

        def ui(kind, payload) -> None:
            self._ui_q.put((kind, payload))

        def work() -> None:
            ok = True
            err_detail = ""
            try:
                self._log_q.put(f"Using Python: {py}\n")
                ui("pct", (5, "Step 1/3 — Installing packages…"))
                ui("step", ("s1", "…  1 / 3  Install packages (running)"))

                # Non-interactive pip: no upgrade-self prompts, no key waits
                pip_args = [
                    py,
                    "-u",
                    "-m",
                    "pip",
                    "install",
                    "--no-input",
                    "--disable-pip-version-check",
                    "--progress-bar",
                    "off",
                    "-r",
                    str(REQ),
                ]
                self._log_q.put("Command: " + " ".join(pip_args) + "\n")

                # Fake progress while pip runs (0–70%)
                stop_tick = threading.Event()

                def ticker() -> None:
                    p = 8.0
                    while not stop_tick.wait(1.2):
                        p = min(68.0, p + 1.5)
                        ui(
                            "pct",
                            (
                                p,
                                f"Step 1/3 — Installing packages… ({int(p)}%)",
                            ),
                        )

                th = threading.Thread(target=ticker, daemon=True)
                th.start()
                code = _run_cmd(pip_args, self._log_q)
                stop_tick.set()

                if code != 0:
                    ok = False
                    err_detail = f"pip failed (exit {code}). See log."
                    self._log_q.put(f"\n{err_detail}\n")
                    ui("step", ("s1", "✗  1 / 3  Install packages — failed"))
                else:
                    self._log_q.put("Packages installed OK.\n")
                    ui("step", ("s1", "●  1 / 3  Install packages — done"))
                    ui("pct", (75, "Step 2/3 — Config…"))

                if ok:
                    ui("step", ("s2", "…  2 / 3  Create config.json"))
                    msg = ensure_config()
                    self._log_q.put(msg + "\n")
                    ui("step", ("s2", "●  2 / 3  Create config.json — done"))
                    ui("pct", (88, "Step 3/3 — Desktop icon…"))

                    ui("step", ("s3", "…  3 / 3  Desktop icon"))
                    try:
                        icon_msg = create_desktop_icon()
                        self._log_q.put(icon_msg + "\n")
                        ui("step", ("s3", "●  3 / 3  Desktop icon — done"))
                        ui("pct", (100, "Setup complete."))
                    except Exception as exc:
                        ok = False
                        err_detail = f"Desktop icon error: {exc}"
                        self._log_q.put(err_detail + "\n")
                        ui("step", ("s3", "✗  3 / 3  Desktop icon — failed"))

            except Exception as exc:
                ok = False
                err_detail = str(exc)
                self._log_q.put(f"ERROR: {exc}\n")

            if ok:
                ui(
                    "done",
                    (
                        True,
                        "Everything is ready.\n\n"
                        "1. Open App\\config.json and paste your OpenAI API key\n"
                        "   (or save it later inside the app)\n\n"
                        "2. Double-click the Desktop icon:\n"
                        "   Acoustic Smart Filler",
                    ),
                )
            else:
                ui(
                    "done",
                    (
                        False,
                        (err_detail or "Setup failed.")
                        + "\n\nSee the Log panel for details.\n"
                        "Check internet and that Python is installed with PATH enabled.",
                    ),
                )

        threading.Thread(target=work, daemon=True).start()


def main() -> None:
    try:
        from ctypes import windll

        windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass
    app = SetupWizard()
    app.mainloop()


if __name__ == "__main__":
    main()
