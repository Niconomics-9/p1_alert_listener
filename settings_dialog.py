"""
settings_dialog.py – In-app settings dialog (Tkinter).

Opens as a modal Toplevel.  Reads from and writes back to AppState.settings.
Changes are applied at runtime and persisted to data/settings.json.
"""
from __future__ import annotations

import logging
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from typing import Any, Callable

import config
from utils import get_logger

log = get_logger("p1alert.settings_dialog")


class SettingsDialog:
    def __init__(
        self,
        parent: tk.Widget,
        current_settings: dict[str, Any],
        on_save: Callable[[dict[str, Any]], None],
    ) -> None:
        self.parent = parent
        self.on_save = on_save

        self._win = tk.Toplevel(parent)
        self._win.title("Settings – P1 Alert Listener")
        self._win.configure(bg=config.DASH_BG)
        self._win.geometry("620x700")
        self._win.resizable(True, True)
        self._win.attributes("-topmost", True)
        self._win.grab_set()  # Modal

        self._settings = dict(current_settings)  # working copy
        self._vars: dict[str, tk.Variable] = {}

        self._build(self._settings)

    # ------------------------------------------------------------------ build

    def _build(self, s: dict) -> None:
        # Scrollable canvas
        canvas = tk.Canvas(self._win, bg=config.DASH_BG, highlightthickness=0)
        scrollbar = ttk.Scrollbar(self._win, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        inner = tk.Frame(canvas, bg=config.DASH_BG)
        canvas_window = canvas.create_window((0, 0), window=inner, anchor="nw")

        def _on_frame_configure(event):
            canvas.configure(scrollregion=canvas.bbox("all"))

        def _on_canvas_configure(event):
            canvas.itemconfig(canvas_window, width=event.width)

        inner.bind("<Configure>", _on_frame_configure)
        canvas.bind("<Configure>", _on_canvas_configure)

        # Mouse-wheel scroll
        def _on_mousewheel(event):
            canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
        canvas.bind_all("<MouseWheel>", _on_mousewheel)

        pad = {"padx": 16, "pady": 4}

        # ── Section: Network ──────────────────────────────────────────────
        self._section(inner, "🌐  Network")
        self._text_field(inner, "Host", "host", s.get("host", "127.0.0.1"), **pad)
        self._int_field(inner, "Port", "port", s.get("port", 8787), **pad)
        self._bool_field(inner, "Allow LAN binding (0.0.0.0) – shows warning", "allow_lan",
                         s.get("allow_lan", False), **pad)

        # ── Section: Alert ────────────────────────────────────────────────
        self._section(inner, "🚨  Alert Behaviour")
        self._int_field(inner, "Cooldown (seconds)", "cooldown_seconds",
                        s.get("cooldown_seconds", 60), **pad)
        self._int_field(inner, "Flash interval (ms)", "flash_interval_ms",
                        s.get("flash_interval_ms", 600), **pad)
        self._bool_field(inner, "Always-on-top alert window", "always_on_top",
                         s.get("always_on_top", True), **pad)
        self._bool_field(inner, "Auto-open full-screen on new alert", "auto_open_fullscreen",
                         s.get("auto_open_fullscreen", True), **pad)

        # ── Section: Sound ────────────────────────────────────────────────
        self._section(inner, "🔊  Sound")
        self._choice_field(inner, "Sound mode", "sound_mode",
                           s.get("sound_mode", "beep"),
                           ["beep", "wav", "silent"], **pad)
        self._file_field(inner, "WAV file path", "wav_path",
                         s.get("wav_path", ""), **pad)

        # ── Section: Authentication ───────────────────────────────────────
        self._section(inner, "🔐  Authentication")
        self._bool_field(inner, "Enable shared secret (X-Webhook-Token)", "auth_enabled",
                         s.get("auth_enabled", False), **pad)
        self._text_field(inner, "Shared secret value", "shared_secret",
                         s.get("shared_secret", ""), secret=True, **pad)
        self._bool_field(inner, "Enable IP allowlist (Cloudflare + extra IPs only)",
                         "ip_allowlist_enabled", s.get("ip_allowlist_enabled", False), **pad)
        allowed_ips_str = ", ".join(s.get("allowed_ips", []))
        self._text_field(inner, "Extra allowed IPs / CIDRs (comma separated)",
                         "allowed_ips_raw", allowed_ips_str, **pad)

        # ── Section: History ──────────────────────────────────────────────
        self._section(inner, "📋  History & Storage")
        self._int_field(inner, "Max history count", "max_history",
                        s.get("max_history", 100), **pad)
        self._bool_field(inner, "Persist history to disk (data/alert_history.json)",
                         "persist_history", s.get("persist_history", True), **pad)

        # ── Section: Logging ──────────────────────────────────────────────
        self._section(inner, "📝  Logging")
        self._text_field(inner, "Log file path", "log_file",
                         s.get("log_file", config.DEFAULT_LOG_FILE), **pad)

        # ── Section: Integrations (future) ───────────────────────────────
        # TODO: Add outbound integration settings here when needed.
        # Example: Halo PSA write-back (create/update tickets on P1).
        # See integrations/base.py for the BaseIntegration class to subclass.

        # ── Buttons ───────────────────────────────────────────────────────
        btn_row = tk.Frame(self._win, bg=config.DASH_BG)
        btn_row.pack(side=tk.BOTTOM, fill=tk.X, padx=16, pady=12)

        tk.Button(
            btn_row, text="💾  Save & Apply",
            command=self._do_save,
            bg="#005580", fg="white",
            font=("Arial", 12, "bold"),
            relief=tk.FLAT, padx=14, pady=6,
        ).pack(side=tk.LEFT, padx=4)

        tk.Button(
            btn_row, text="✖  Cancel",
            command=self._win.destroy,
            bg=config.DASH_BTN_BG, fg=config.DASH_FG,
            font=("Arial", 11),
            relief=tk.FLAT, padx=14, pady=6,
        ).pack(side=tk.LEFT, padx=4)

        tk.Button(
            btn_row, text="↺  Reset Defaults",
            command=self._do_reset,
            bg="#442222", fg="#FFAAAA",
            font=("Arial", 11),
            relief=tk.FLAT, padx=14, pady=6,
        ).pack(side=tk.RIGHT, padx=4)

    # ------------------------------------------------------------------ field helpers

    def _section(self, parent: tk.Widget, title: str) -> None:
        f = tk.Frame(parent, bg=config.DASH_BG)
        f.pack(fill=tk.X, padx=12, pady=(14, 2))
        tk.Label(
            f, text=title,
            font=("Arial", 12, "bold"),
            fg=config.DASH_ACCENT, bg=config.DASH_BG,
        ).pack(anchor="w")
        tk.Frame(f, height=1, bg=config.DASH_ACCENT).pack(fill=tk.X, pady=2)

    def _text_field(self, parent, label, key, value, secret=False, **kw):
        row = tk.Frame(parent, bg=config.DASH_BG)
        row.pack(fill=tk.X, **kw)
        tk.Label(row, text=label + ":", fg=config.DASH_FG, bg=config.DASH_BG,
                 font=("Arial", 10), width=30, anchor="w").pack(side=tk.LEFT)
        var = tk.StringVar(value=str(value))
        show = "*" if secret else ""
        tk.Entry(row, textvariable=var, show=show,
                 bg="#313244", fg=config.DASH_FG,
                 insertbackground=config.DASH_FG,
                 relief=tk.FLAT, font=("Consolas", 10), width=34
                 ).pack(side=tk.LEFT, fill=tk.X, expand=True)
        self._vars[key] = var

    def _int_field(self, parent, label, key, value, **kw):
        row = tk.Frame(parent, bg=config.DASH_BG)
        row.pack(fill=tk.X, **kw)
        tk.Label(row, text=label + ":", fg=config.DASH_FG, bg=config.DASH_BG,
                 font=("Arial", 10), width=30, anchor="w").pack(side=tk.LEFT)
        var = tk.StringVar(value=str(value))
        tk.Entry(row, textvariable=var,
                 bg="#313244", fg=config.DASH_FG,
                 insertbackground=config.DASH_FG,
                 relief=tk.FLAT, font=("Consolas", 10), width=12
                 ).pack(side=tk.LEFT)
        self._vars[key] = var

    def _bool_field(self, parent, label, key, value, **kw):
        row = tk.Frame(parent, bg=config.DASH_BG)
        row.pack(fill=tk.X, **kw)
        var = tk.BooleanVar(value=bool(value))
        tk.Checkbutton(
            row, text=label, variable=var,
            fg=config.DASH_FG, bg=config.DASH_BG,
            activebackground=config.DASH_BG,
            activeforeground=config.DASH_ACCENT,
            selectcolor=config.DASH_BTN_BG,
            font=("Arial", 10),
        ).pack(anchor="w")
        self._vars[key] = var

    def _choice_field(self, parent, label, key, value, choices, **kw):
        row = tk.Frame(parent, bg=config.DASH_BG)
        row.pack(fill=tk.X, **kw)
        tk.Label(row, text=label + ":", fg=config.DASH_FG, bg=config.DASH_BG,
                 font=("Arial", 10), width=30, anchor="w").pack(side=tk.LEFT)
        var = tk.StringVar(value=value)
        cb = ttk.Combobox(row, textvariable=var, values=choices,
                          state="readonly", width=14)
        cb.pack(side=tk.LEFT)
        self._vars[key] = var

    def _file_field(self, parent, label, key, value, **kw):
        row = tk.Frame(parent, bg=config.DASH_BG)
        row.pack(fill=tk.X, **kw)
        tk.Label(row, text=label + ":", fg=config.DASH_FG, bg=config.DASH_BG,
                 font=("Arial", 10), width=30, anchor="w").pack(side=tk.LEFT)
        var = tk.StringVar(value=str(value))
        tk.Entry(row, textvariable=var,
                 bg="#313244", fg=config.DASH_FG,
                 insertbackground=config.DASH_FG,
                 relief=tk.FLAT, font=("Consolas", 10), width=26
                 ).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 4))
        tk.Button(
            row, text="Browse",
            command=lambda: self._browse_file(var),
            bg=config.DASH_BTN_BG, fg=config.DASH_FG,
            font=("Arial", 9), relief=tk.FLAT, padx=6,
        ).pack(side=tk.LEFT)
        self._vars[key] = var

    def _browse_file(self, var: tk.StringVar) -> None:
        path = filedialog.askopenfilename(
            title="Select WAV file",
            filetypes=[("WAV files", "*.wav"), ("All files", "*.*")],
        )
        if path:
            var.set(path)

    # ------------------------------------------------------------------ actions

    def _do_save(self) -> None:
        new_settings = dict(self._settings)
        errors = []
        for key, var in self._vars.items():
            try:
                val = var.get()
                # Type coercions for integer fields
                if key in ("port", "cooldown_seconds", "flash_interval_ms", "max_history"):
                    val = int(val)
                new_settings[key] = val
            except ValueError:
                errors.append(f"Invalid value for '{key}'")

        # Convert allowed_ips_raw string → list
        raw = new_settings.pop("allowed_ips_raw", "")
        new_settings["allowed_ips"] = [
            ip.strip() for ip in raw.split(",") if ip.strip()
        ]

        if errors:
            messagebox.showerror("Validation Error", "\n".join(errors), parent=self._win)
            return

        # LAN warning
        if new_settings.get("allow_lan"):
            messagebox.showwarning(
                "LAN Mode",
                "Enabling LAN binding exposes the listener to your network.\n"
                "Make sure this machine is on a trusted LAN.\n"
                "Changes take effect after restarting the listener.",
                parent=self._win,
            )

        log.info("Settings saved from dialog")
        self.on_save(new_settings)
        self._win.destroy()

    def _do_reset(self) -> None:
        if messagebox.askyesno("Reset Defaults",
                               "Reset all settings to defaults?\n"
                               "You will still need to click Save.",
                               parent=self._win):
            from state import AppState
            defaults = AppState._default_settings()
            for key, var in self._vars.items():
                if key in defaults:
                    try:
                        var.set(defaults[key])
                    except Exception:
                        pass
