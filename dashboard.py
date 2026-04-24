"""
dashboard.py – Main Tkinter dashboard window.

Layout
------
  Top bar      – hamburger (☰), brand, status pills, uptime
  Outage banner – amber strip when external outages detected (hidden when none)
  Body         – [animated side menu] | [cards area + log drawer]
  Status bar   – status text, host:port, by Niconomics

Side menu slides in/out via smooth width animation (☰ toggle in top bar).
Cards area splits: left (active alert, P1 queue, history) | right (Halo tickets).
Active/queue use grid within their row for proportional resize.
"""
from __future__ import annotations

import json
import logging
import os
import tkinter as tk
from tkinter import messagebox, scrolledtext
from typing import TYPE_CHECKING

import config
from alert_ui import AlertWindow
from models import Alert
from settings_dialog import SettingsDialog
import sound
import storage
from utils import now_display

if TYPE_CHECKING:
    from state import AppState

log = logging.getLogger("p1alert.dashboard")

POLL_INTERVAL_MS = 150
LOG_MAX_LINES    = 500
MENU_WIDTH       = 210    # px when fully open
MENU_ANIM_STEP   = 24     # px per animation frame
MENU_ANIM_MS     = 12     # ms between frames

# ── Design tokens ─────────────────────────────────────────────────────────────
FONT_TITLE   = ("Segoe UI", 17, "bold")
FONT_UI      = ("Segoe UI", 10)
FONT_LABEL   = ("Segoe UI", 9)
FONT_MONO    = ("Consolas", 10)
FONT_MONO_SM = ("Consolas", 9)

BG_SURFACE  = "#1E1E2E"
BG_CARD     = "#242436"
BG_DEEP     = "#11111B"
BG_INPUT    = "#313244"
BG_MENU     = "#181825"
BG_MENU_BTN = "#1E1E30"

BTN_PRIMARY = "#4C9BE8"
BTN_SUCCESS = "#A6E3A1"
BTN_WARN    = "#F9E2AF"
BTN_MUTED   = "#313244"
BTN_RED     = "#442233"

ACCENT_RED   = "#F38BA8"
ACCENT_BLUE  = "#89B4FA"
ACCENT_AMBER = "#F9E2AF"


class Dashboard:
    """Main application window and event loop host."""

    def __init__(self, root: tk.Tk, app_state: "AppState") -> None:
        self.root = root
        self.state = app_state
        self._alert_window: AlertWindow | None = None
        self._uptime_job: str | None = None
        self._log_visible = True
        self._menu_open = False
        self._menu_anim_id: str | None = None

        self._build_window()
        self._schedule_poll()
        self._schedule_uptime()
        log.info("Dashboard initialised")

    # ──────────────────────────────────────────────── window skeleton

    def _build_window(self) -> None:
        r = self.root
        r.title(config.APP_TITLE)
        r.configure(bg=BG_SURFACE)
        r.geometry("1400x800")
        r.minsize(1000, 600)
        r.protocol("WM_DELETE_WINDOW", self._on_close)

        # Status bar must be packed before body so it claims BOTTOM first
        self._build_status_bar()
        self._build_top_bar()
        self._build_outage_banner()
        self._build_body()

    # ──────────────────────────────────────────────── top bar

    def _build_top_bar(self) -> None:
        bar = tk.Frame(self.root, bg=BG_DEEP, height=52)
        bar.pack(fill=tk.X)
        bar.pack_propagate(False)
        self._top_bar = bar

        # Hamburger toggle
        ham = tk.Label(bar, text="☰", font=("Segoe UI", 18),
                       fg=ACCENT_BLUE, bg=BG_DEEP, cursor="hand2", padx=14)
        ham.pack(side=tk.LEFT)
        ham.bind("<Button-1>", lambda e: self._toggle_menu())
        _hover(ham, BG_DEEP, "#252540")

        tk.Label(bar, text="⚡ NetWatch", font=FONT_TITLE,
                 fg=ACCENT_BLUE, bg=BG_DEEP).pack(side=tk.LEFT, padx=(0, 16))

        # Right: uptime + pills
        right = tk.Frame(bar, bg=BG_DEEP)
        right.pack(side=tk.RIGHT, padx=16)
        self._uptime_lbl = tk.Label(right, text="00:00:00",
                                     font=("Segoe UI", 9), fg=config.COLOR_MUTED, bg=BG_DEEP)
        self._uptime_lbl.pack(side=tk.RIGHT, padx=(8, 0))
        tk.Label(right, text="Uptime", font=("Segoe UI", 8),
                 fg=config.COLOR_MUTED, bg=BG_DEEP).pack(side=tk.RIGHT)

        pills = tk.Frame(bar, bg=BG_DEEP)
        pills.pack(side=tk.RIGHT, padx=8)
        self._pill_listener = _Pill(pills, "Listener", "STOPPED", config.COLOR_ERR)
        self._pill_listener.pack(side=tk.LEFT, padx=3)
        self._pill_sound = _Pill(pills, "Sound", "ON", config.COLOR_OK)
        self._pill_sound.pack(side=tk.LEFT, padx=3)
        self._pill_active = _Pill(pills, "Active", "0", config.COLOR_MUTED)
        self._pill_active.pack(side=tk.LEFT, padx=3)
        self._pill_queued = _Pill(pills, "Queued", "0", config.COLOR_MUTED)
        self._pill_queued.pack(side=tk.LEFT, padx=3)

    # ──────────────────────────────────────────────── outage banner

    def _build_outage_banner(self) -> None:
        """Amber strip between top bar and body; hidden until outages exist."""
        self._outage_banner = tk.Frame(self.root, bg="#5C4000", height=28)
        self._outage_banner.pack_propagate(False)
        # Not packed initially — inserted via pack(after=_top_bar) when needed
        self._outage_lbl = tk.Label(
            self._outage_banner, text="",
            font=("Segoe UI", 9, "bold"), fg="#FFF3CD", bg="#5C4000",
        )
        self._outage_lbl.pack(side=tk.LEFT, padx=12)
        dismiss = tk.Label(
            self._outage_banner, text="✕", font=("Segoe UI", 9),
            fg="#FFF3CD", bg="#5C4000", cursor="hand2",
        )
        dismiss.pack(side=tk.RIGHT, padx=12)
        dismiss.bind("<Button-1>", lambda e: self._hide_outage_banner())
        self._outage_banner_visible = False

    def _show_outage_banner(self, text: str) -> None:
        self._outage_lbl.configure(text=text)
        if not self._outage_banner_visible:
            self._outage_banner.pack(fill=tk.X, after=self._top_bar)
            self._outage_banner_visible = True

    def _hide_outage_banner(self) -> None:
        if self._outage_banner_visible:
            self._outage_banner.pack_forget()
            self._outage_banner_visible = False

    def _refresh_outage_banner(self, outages: list) -> None:
        active = [o for o in outages if not getattr(o, "resolved", False)]
        if not active:
            self._hide_outage_banner()
            return
        services = ", ".join(o.service for o in active[:3])
        if len(active) > 3:
            services += f" +{len(active) - 3} more"
        self._show_outage_banner(
            f"⚠  {len(active)} ACTIVE OUTAGE{'S' if len(active) > 1 else ''}  —  {services}"
        )

    # ──────────────────────────────────────────────── body (side menu + content)

    def _build_body(self) -> None:
        self._body = tk.Frame(self.root, bg=BG_SURFACE)
        self._body.pack(fill=tk.BOTH, expand=True)

        # Side menu — width=0 at start; pack_propagate=False lets animation work
        self._side_frame = tk.Frame(self._body, bg=BG_MENU, width=0)
        self._side_frame.pack(side=tk.LEFT, fill=tk.Y)
        self._side_frame.pack_propagate(False)
        self._build_side_menu()

        # Content pane — fills all remaining space and reflows on resize
        content = tk.Frame(self._body, bg=BG_SURFACE)
        content.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self._build_cards(content)
        self._build_log_drawer(content)

    # ──────────────────────────────────────────────── side menu

    def _build_side_menu(self) -> None:
        m = self._side_frame
        tk.Frame(m, bg=ACCENT_BLUE, height=2).pack(fill=tk.X)

        # Scrollable canvas so menu works on short windows
        canvas = tk.Canvas(m, bg=BG_MENU, highlightthickness=0, bd=0)
        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        inner = tk.Frame(canvas, bg=BG_MENU)
        cwin = canvas.create_window((0, 0), window=inner, anchor="nw")

        inner.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", lambda e: canvas.itemconfig(cwin, width=e.width))
        canvas.bind_all("<MouseWheel>",
                        lambda e: canvas.yview_scroll(int(-1 * e.delta / 120), "units"))

        pad = dict(padx=10, pady=2)

        self._menu_section(inner, "ALERT ACTIONS")
        self._menu_btn(inner, "🧪  Test Alert",      self._do_test_alert,       BTN_PRIMARY,             **pad)
        self._menu_btn(inner, "🖥  Open Alert",       self._do_open_alert,       BTN_MUTED,               **pad)
        self._menu_btn(inner, "🔇  Silence",          self._do_silence,          BTN_MUTED,               **pad)
        self._menu_btn(inner, "✔  Acknowledge",      self._do_acknowledge,      BTN_SUCCESS, fg="#11111B", **pad)

        self._menu_divider(inner)

        self._menu_section(inner, "LISTENER")
        self._menu_btn(inner, "↺  Restart Listener", self._do_restart_listener, BTN_MUTED,               **pad)

        self._menu_divider(inner)

        self._menu_section(inner, "HISTORY")
        self._menu_btn(inner, "📋  Copy Last JSON",   self._do_copy_json,        BTN_MUTED,               **pad)
        self._menu_btn(inner, "🗑  Clear History",    self._do_clear_history,    BTN_RED,    fg="#FFAAAA", **pad)

        self._menu_divider(inner)

        self._menu_section(inner, "APP")
        self._menu_btn(inner, "⚙  Settings",         self._do_settings,         BTN_MUTED,               **pad)
        self._menu_btn(inner, "🔊  Test Sound",       self._do_test_sound,       BTN_MUTED,               **pad)
        self._menu_btn(inner, "📁  Open Logs",        self._do_open_logs,        BTN_MUTED,               **pad)
        self._menu_btn(inner, "🌐  Status Board",     self._do_open_board,       BTN_MUTED,               **pad)

    def _menu_section(self, parent, title: str) -> None:
        tk.Label(parent, text=title, font=("Segoe UI", 7, "bold"),
                 fg=config.COLOR_MUTED, bg=BG_MENU,
                 anchor="w").pack(fill=tk.X, padx=12, pady=(12, 2))

    def _menu_divider(self, parent) -> None:
        tk.Frame(parent, bg="#2A2A40", height=1).pack(fill=tk.X, padx=10, pady=6)

    def _menu_btn(self, parent, label, cmd, bg, fg="white", **kw):
        btn = tk.Label(parent, text=label, font=("Segoe UI", 10),
                       fg=fg, bg=bg, anchor="w", padx=14, pady=7, cursor="hand2")
        btn.pack(fill=tk.X, **kw)
        btn.bind("<Button-1>", lambda e: cmd())
        _hover(btn, bg, _lighten(bg))

    # ──────────────────────────────────────────────── menu animation

    def _toggle_menu(self) -> None:
        if self._menu_anim_id:
            self.root.after_cancel(self._menu_anim_id)
            self._menu_anim_id = None
        self._animate_menu(0 if self._menu_open else MENU_WIDTH)

    def _animate_menu(self, target: int) -> None:
        current = self._side_frame.winfo_width()
        if current == target:
            self._menu_open = target > 0
            self._menu_anim_id = None
            return
        step = MENU_ANIM_STEP if target > current else -MENU_ANIM_STEP
        new_w = current + step
        if (step > 0 and new_w >= target) or (step < 0 and new_w <= target):
            new_w = target
        self._side_frame.configure(width=new_w)
        if new_w != target:
            self._menu_anim_id = self.root.after(MENU_ANIM_MS, lambda: self._animate_menu(target))
        else:
            self._menu_open = target > 0
            self._menu_anim_id = None

    # ──────────────────────────────────────────────── cards

    def _build_cards(self, parent: tk.Frame) -> None:
        area = tk.Frame(parent, bg=BG_SURFACE)
        area.pack(fill=tk.BOTH, expand=True, padx=10, pady=(10, 0))

        # Horizontal split: left (main) | right (Halo queue)
        split = tk.Frame(area, bg=BG_SURFACE)
        split.pack(fill=tk.BOTH, expand=True)

        self._build_halo_panel(split)   # right — packed first, fixed width

        left = tk.Frame(split, bg=BG_SURFACE)
        left.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        # Top row — grid so active + queue resize proportionally
        top_row = tk.Frame(left, bg=BG_SURFACE)
        top_row.pack(fill=tk.BOTH, expand=True, pady=(0, 8))
        top_row.columnconfigure(0, weight=3)
        top_row.columnconfigure(1, weight=2)
        top_row.rowconfigure(0, weight=1)

        self._build_active_card(top_row)
        self._build_queue_card(top_row)
        self._build_history_card(left)

    def _build_active_card(self, parent: tk.Frame) -> None:
        outer = _Card(parent, "ACTIVE ALERT", accent=ACCENT_BLUE)
        outer.grid(row=0, column=0, sticky="nsew", padx=(0, 5))
        self._active_card = outer

        self._active_text = scrolledtext.ScrolledText(
            outer.body, bg=BG_SURFACE, fg=config.DASH_FG,
            font=FONT_MONO, relief=tk.FLAT, state=tk.DISABLED,
            height=9, wrap=tk.WORD, bd=0,
        )
        self._active_text.pack(fill=tk.BOTH, expand=True)

    def _build_queue_card(self, parent: tk.Frame) -> None:
        outer = _Card(parent, "QUEUE", accent=ACCENT_AMBER)
        outer.grid(row=0, column=1, sticky="nsew", padx=(5, 0))

        self._queued_list = tk.Listbox(
            outer.body, bg=BG_SURFACE, fg=config.DASH_FG,
            font=FONT_MONO_SM, relief=tk.FLAT, selectbackground=BTN_MUTED,
            height=9, bd=0, highlightthickness=0,
        )
        self._queued_list.pack(fill=tk.BOTH, expand=True)

    def _build_halo_panel(self, parent) -> None:
        outer = _Card(parent, "HALO TICKET QUEUE", accent=ACCENT_BLUE)
        outer.pack(side=tk.RIGHT, fill=tk.BOTH, expand=False,
                   padx=(6, 0), pady=(0, 8))
        outer.configure(width=310)
        outer.pack_propagate(False)

        self._halo_count_lbl = tk.Label(
            outer.header_frame, text="—",
            font=FONT_LABEL, fg=config.COLOR_MUTED, bg=BG_CARD,
        )
        self._halo_count_lbl.pack(side=tk.RIGHT, padx=4)

        inner = tk.Frame(outer.body, bg=BG_SURFACE)
        inner.pack(fill=tk.BOTH, expand=True)
        sb = tk.Scrollbar(inner, bg=BG_SURFACE, troughcolor=BG_SURFACE)
        sb.pack(side=tk.RIGHT, fill=tk.Y)
        self._halo_list = tk.Listbox(
            inner, bg=BG_SURFACE, fg=config.DASH_FG,
            font=FONT_MONO_SM, relief=tk.FLAT, selectbackground=BTN_MUTED,
            yscrollcommand=sb.set, bd=0, highlightthickness=0,
        )
        self._halo_list.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        sb.config(command=self._halo_list.yview)

    def _build_history_card(self, parent: tk.Frame) -> None:
        outer = _Card(parent, "HISTORY", accent=ACCENT_BLUE)
        outer.pack(fill=tk.BOTH, expand=True, pady=(0, 6))

        fr = tk.Frame(outer.header_frame, bg=BG_CARD)
        fr.pack(side=tk.RIGHT, padx=6)
        tk.Label(fr, text="Filter:", font=FONT_LABEL,
                 fg=config.COLOR_MUTED, bg=BG_CARD).pack(side=tk.LEFT)
        self._filter_var = tk.StringVar()
        self._filter_var.trace_add("write", lambda *_: self._refresh_history())
        tk.Entry(fr, textvariable=self._filter_var,
                 bg=BG_INPUT, fg=config.DASH_FG,
                 insertbackground=config.DASH_FG,
                 relief=tk.FLAT, font=FONT_MONO_SM, width=26,
                 ).pack(side=tk.LEFT, padx=(4, 0))

        inner = tk.Frame(outer.body, bg=BG_SURFACE)
        inner.pack(fill=tk.BOTH, expand=True)
        sb = tk.Scrollbar(inner, bg=BG_SURFACE, troughcolor=BG_SURFACE)
        sb.pack(side=tk.RIGHT, fill=tk.Y)
        self._hist_list = tk.Listbox(
            inner, bg=BG_SURFACE, fg=config.DASH_FG,
            font=FONT_MONO_SM, relief=tk.FLAT, selectbackground=BTN_MUTED,
            yscrollcommand=sb.set, bd=0, highlightthickness=0,
        )
        self._hist_list.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        sb.config(command=self._hist_list.yview)
        self._hist_list.bind("<Double-Button-1>", self._on_history_double_click)

    # ──────────────────────────────────────────────── log drawer

    def _build_log_drawer(self, parent: tk.Frame) -> None:
        self._log_outer = tk.Frame(parent, bg=BG_DEEP)
        self._log_outer.pack(fill=tk.X, padx=10, pady=(0, 6))

        hdr = tk.Frame(self._log_outer, bg=BG_DEEP)
        hdr.pack(fill=tk.X)
        tk.Label(hdr, text="EVENT LOG", font=("Segoe UI", 8, "bold"),
                 fg=config.COLOR_MUTED, bg=BG_DEEP).pack(side=tk.LEFT, padx=10, pady=5)
        self._log_toggle_lbl = tk.Label(hdr, text="▼ hide", font=FONT_LABEL,
                                         fg=ACCENT_BLUE, bg=BG_DEEP, cursor="hand2")
        self._log_toggle_lbl.pack(side=tk.RIGHT, padx=10)
        self._log_toggle_lbl.bind("<Button-1>", lambda e: self._toggle_log())

        self._log_body = tk.Frame(self._log_outer, bg=BG_DEEP)
        self._log_body.pack(fill=tk.X)
        self._log_text = scrolledtext.ScrolledText(
            self._log_body, bg=BG_DEEP, fg=config.DASH_LOG_FG,
            font=FONT_MONO_SM, relief=tk.FLAT, state=tk.DISABLED,
            height=6, wrap=tk.WORD, bd=0,
        )
        self._log_text.pack(fill=tk.X, padx=10, pady=(0, 6))

    def _toggle_log(self) -> None:
        if self._log_visible:
            self._log_body.pack_forget()
            self._log_toggle_lbl.configure(text="▲ show")
        else:
            self._log_body.pack(fill=tk.X)
            self._log_toggle_lbl.configure(text="▼ hide")
        self._log_visible = not self._log_visible

    # ──────────────────────────────────────────────── status bar

    def _build_status_bar(self) -> None:
        bar = tk.Frame(self.root, bg=BG_DEEP, height=22)
        bar.pack(side=tk.BOTTOM, fill=tk.X)
        bar.pack_propagate(False)
        self._status_lbl = tk.Label(bar, text="Ready", font=FONT_LABEL,
                                     fg=config.DASH_FG, bg=BG_DEEP)
        self._status_lbl.pack(side=tk.LEFT, padx=10)
        tk.Label(bar, text="by Niconomics", font=("Segoe UI", 8),
                 fg=config.COLOR_MUTED, bg=BG_DEEP).pack(side=tk.RIGHT, padx=12)
        s = self.state.settings
        tk.Label(bar, text=f"{s.get('host','127.0.0.1')}:{s.get('port',8787)}",
                 font=FONT_LABEL, fg=config.COLOR_MUTED, bg=BG_DEEP,
                 ).pack(side=tk.RIGHT, padx=(10, 4))

    # ──────────────────────────────────────────────── queue polling

    def _schedule_poll(self) -> None:
        self.root.after(POLL_INTERVAL_MS, self._poll_queue)

    def _poll_queue(self) -> None:
        try:
            q = self.state.alert_queue
            while not q.empty():
                self._dispatch(q.get_nowait())
        except Exception as exc:
            log.error(f"Queue poll error: {exc}", exc_info=True)
        finally:
            self._schedule_poll()

    def _dispatch(self, msg) -> None:
        from models import MSG_TICKET_UPDATE, MSG_OUTAGE_UPDATE
        kind, data = msg.kind, msg.data
        if kind == "alert":
            self._handle_new_alert(data)
        elif kind == "queued_alert":
            self._refresh_all()
        elif kind == "log":
            self._append_log(str(data))
        elif kind == "listener_status":
            self._on_listener_status(bool(data))
        elif kind == "error":
            self._append_log(f"❌ ERROR: {data}")
            messagebox.showerror("Listener Error", str(data), parent=self.root)
        elif kind == MSG_TICKET_UPDATE:
            self._refresh_halo_tickets(data or [])
        elif kind == MSG_OUTAGE_UPDATE:
            self._refresh_outage_banner(data or [])
        elif kind == "open_alert_request":
            self._open_alert_window()
        elif kind == "restart_listener":
            self._do_restart_listener()

    def _handle_new_alert(self, alert: Alert) -> None:
        self._refresh_all()
        self._active_card.set_accent(ACCENT_RED)
        if self.state.settings.get("auto_open_fullscreen", True):
            self._open_alert_window(alert)
        if not self.state.sound_silenced:
            s = self.state.settings
            sound.start_alert_sound(s.get("sound_mode", "beep"), s.get("wav_path", ""))
        self._set_status(f"🚨 P1 ALERT: {alert.ticket_id}")

    # ──────────────────────────────────────────────── alert window

    def _open_alert_window(self, alert: Alert | None = None) -> None:
        if alert is None:
            alert = self.state.active_alert
        if alert is None:
            messagebox.showinfo("No Alert", "No active alert to display.", parent=self.root)
            return
        if self._alert_window:
            try:
                self._alert_window.destroy()
            except Exception:
                pass
            self._alert_window = None
        s = self.state.settings
        try:
            self._alert_window = AlertWindow(
                root=self.root, alert=alert,
                queue_count=self.state.queue_count(),
                on_acknowledge=self._do_acknowledge,
                on_silence=self._do_silence,
                on_next=self._do_next_alert,
                on_test=self._do_test_sound,
                flash_interval_ms=s.get("flash_interval_ms", 600),
                always_on_top=s.get("always_on_top", True),
            )
        except Exception as exc:
            log.error(f"Failed to open alert window: {exc}", exc_info=True)
            messagebox.showerror("Alert Error", f"Could not open alert:\n{exc}", parent=self.root)

    # ──────────────────────────────────────────────── actions

    def _do_restart_listener(self) -> None:
        import threading
        def _restart():
            t = self.state.listener_thread
            if t and t.is_alive():
                self.state.post("log", "↺ Stopping listener…")
                try:
                    t.stop()
                    t.join(timeout=4)
                except Exception as exc:
                    log.warning(f"Stop error: {exc}")
            from listener import ListenerThread
            new_t = ListenerThread(self.state)
            self.state.listener_thread = new_t
            new_t.start()
        threading.Thread(target=_restart, daemon=True).start()
        self._set_status("Restarting listener…")

    def _do_test_alert(self) -> None:
        import threading, uuid
        def _inject():
            from listener import _build_test_payload
            from parser import build_alert
            alert = build_alert(_build_test_payload())
            alert.dedupe_key = f"test:{uuid.uuid4().hex[:8]}"
            placement = self.state.push_alert(alert)
            key = "alert" if placement == "active" else "queued_alert"
            self.state.post(key, alert)
            self.state.post("log", f"🧪 Test alert ({placement})")
        threading.Thread(target=_inject, daemon=True).start()

    def _do_open_alert(self) -> None:
        self._open_alert_window()

    def _do_silence(self) -> None:
        self.state.silence_active()
        if self.state.sound_silenced:
            sound.stop_alert_sound()
            self._append_log("🔇 Silenced")
            self._pill_sound.set("MUTED", config.COLOR_MUTED)
        else:
            if self.state.active_alert:
                s = self.state.settings
                sound.start_alert_sound(s.get("sound_mode", "beep"), s.get("wav_path", ""))
            self._append_log("🔊 Un-silenced")
            self._pill_sound.set("ON", config.COLOR_OK)

    def _do_acknowledge(self) -> None:
        old = self.state.acknowledge_active()
        if old is None:
            self._append_log("ℹ️ No active alert")
            return
        sound.stop_alert_sound()
        self._append_log(f"✔ Acknowledged: {old.ticket_id}")
        if self._alert_window:
            try:
                self._alert_window.destroy()
            except Exception:
                pass
            self._alert_window = None
        if self.state.active_alert and self.state.settings.get("auto_open_fullscreen"):
            self._open_alert_window(self.state.active_alert)
            s = self.state.settings
            sound.start_alert_sound(s.get("sound_mode", "beep"), s.get("wav_path", ""))
        if not self.state.active_alert:
            self._active_card.set_accent(ACCENT_BLUE)
        self._refresh_all()
        if self.state.settings.get("persist_history"):
            storage.save_history(self.state.history)

    def _do_next_alert(self) -> None:
        next_alert = self.state.next_queued()
        sound.stop_alert_sound()
        if self._alert_window:
            try:
                self._alert_window.destroy()
            except Exception:
                pass
            self._alert_window = None
        if next_alert:
            if self.state.settings.get("auto_open_fullscreen"):
                self._open_alert_window(next_alert)
            s = self.state.settings
            sound.start_alert_sound(s.get("sound_mode", "beep"), s.get("wav_path", ""))
        self._refresh_all()

    def _do_clear_history(self) -> None:
        if messagebox.askyesno("Clear History",
                                "Clear all alert history?\nThis cannot be undone.",
                                parent=self.root):
            self.state.clear_history()
            if self.state.settings.get("persist_history"):
                storage.save_history([])
            self._refresh_history()
            self._append_log("🗑 History cleared")

    def _do_copy_json(self) -> None:
        history = self.state.get_recent_history(1)
        if not history:
            messagebox.showinfo("Copy JSON", "No alerts in history.", parent=self.root)
            return
        text = json.dumps(history[0].to_dict(), indent=2, ensure_ascii=False)
        self.root.clipboard_clear()
        self.root.clipboard_append(text)
        self._append_log("📋 Copied last alert JSON")

    def _do_settings(self) -> None:
        def _on_save(new_settings):
            self.state.settings.update(new_settings)
            storage.save_settings(self.state.settings)
            self._append_log("⚙ Settings updated")
        SettingsDialog(self.root, self.state.settings, _on_save)

    def _do_open_logs(self) -> None:
        config.LOGS_DIR.mkdir(parents=True, exist_ok=True)
        try:
            os.startfile(str(config.LOGS_DIR))
        except Exception as exc:
            messagebox.showinfo("Logs", f"{config.LOGS_DIR}\n\n{exc}", parent=self.root)

    def _do_open_board(self) -> None:
        import webbrowser
        port = self.state.settings.get("port", config.DEFAULT_PORT)
        webbrowser.open(f"http://127.0.0.1:{port}/board")
        self._append_log("🌐 Status Board opened in browser")

    def _do_test_sound(self) -> None:
        s = self.state.settings
        import threading
        threading.Thread(
            target=sound.test_sound,
            args=(s.get("sound_mode", "beep"), s.get("wav_path", "")),
            daemon=True,
        ).start()
        self._append_log("🔊 Test sound playing…")

    # ──────────────────────────────────────────────── refresh

    def _refresh_all(self) -> None:
        self._refresh_active()
        self._refresh_queued()
        self._refresh_history()
        self._refresh_pills()
        if self._alert_window:
            try:
                self._alert_window.update_queue_count(self.state.queue_count())
            except Exception:
                pass

    def _refresh_active(self) -> None:
        self._active_text.configure(state=tk.NORMAL)
        self._active_text.delete("1.0", tk.END)
        a = self.state.active_alert
        if a:
            rows = [
                ("Ticket   ", a.ticket_id),
                ("Client   ", a.client),
                ("Summary  ", a.summary),
                ("Source   ", a.source),
                ("Priority ", f"{a.priority}  ·  {a.severity or '—'}"),
                ("Team     ", a.assigned_team or "—"),
                ("Received ", a.display_received()),
            ]
            for lbl, val in rows:
                self._active_text.insert(tk.END, lbl, "dim")
                self._active_text.insert(tk.END, f"  {val}\n")
            self._active_text.tag_configure("dim", foreground=config.COLOR_MUTED,
                                             font=("Segoe UI", 9))
        else:
            self._active_text.insert(tk.END, "\n  No active alert", "empty")
            self._active_text.tag_configure("empty", foreground=config.COLOR_MUTED,
                                             font=("Segoe UI", 10))
        self._active_text.configure(state=tk.DISABLED)

    def _refresh_queued(self) -> None:
        self._queued_list.delete(0, tk.END)
        with self.state._lock:
            for a in self.state.alert_queue_list:
                self._queued_list.insert(
                    tk.END, f"  {a.display_received()}  {a.ticket_id}  {a.client[:20]}")

    def _refresh_history(self) -> None:
        self._hist_list.delete(0, tk.END)
        filt = self._filter_var.get().strip().lower()
        for a in self.state.get_recent_history(200):
            tag = _source_tag(a.source)
            row = f"  {a.display_received()}  {tag}  {a.ticket_id}  {a.client[:20]}  {a.short_summary(40)}"
            if filt and filt not in row.lower():
                continue
            self._hist_list.insert(tk.END, row)

    def _refresh_pills(self) -> None:
        self._pill_listener.set(
            "RUNNING" if self.state.listener_running else "STOPPED",
            config.COLOR_OK if self.state.listener_running else config.COLOR_ERR,
        )
        self._pill_sound.set(
            "MUTED" if self.state.sound_silenced else "ON",
            config.COLOR_MUTED if self.state.sound_silenced else config.COLOR_OK,
        )
        active = 1 if self.state.active_alert else 0
        self._pill_active.set(str(active), config.COLOR_ERR if active else config.COLOR_MUTED)
        q = self.state.queue_count()
        self._pill_queued.set(str(q), config.COLOR_WARN if q else config.COLOR_MUTED)

    def _refresh_halo_tickets(self, rows: list) -> None:
        self._halo_list.delete(0, tk.END)
        self._halo_count_lbl.configure(text=f"{len(rows)} open" if rows else "0 open")
        for r in rows:
            if r.sla_remaining_minutes is None:
                sla_str = "SLA:—   "
            elif r.sla_remaining_minutes < 0:
                sla_str = "SLA:OVR "
            else:
                h, m = divmod(r.sla_remaining_minutes, 60)
                sla_str = f"SLA:{h:02d}h{m:02d}"
            pri    = (r.priority or "")[:4].ljust(4)
            client = (r.client or "")[:18].ljust(18)
            subj   = (r.subject or "")[:26]
            self._halo_list.insert(tk.END, f" {sla_str}  {pri}  {client}  {subj}")
            if r.sla_remaining_minutes is not None and r.sla_remaining_minutes < 0:
                self._halo_list.itemconfig(tk.END, fg=config.COLOR_ERR)
            elif r.sla_remaining_minutes is not None and r.sla_remaining_minutes < 60:
                self._halo_list.itemconfig(tk.END, fg=config.COLOR_WARN)

    # ──────────────────────────────────────────────── log

    def _append_log(self, message: str) -> None:
        self._log_text.configure(state=tk.NORMAL)
        self._log_text.insert(tk.END, f"[{now_display()}]  {message}\n")
        lines = int(self._log_text.index(tk.END).split(".")[0])
        if lines > LOG_MAX_LINES:
            self._log_text.delete("1.0", f"{lines - LOG_MAX_LINES}.0")
        self._log_text.see(tk.END)
        self._log_text.configure(state=tk.DISABLED)

    # ──────────────────────────────────────────────── misc

    def _set_status(self, text: str) -> None:
        self._status_lbl.configure(text=text)

    def _on_listener_status(self, running: bool) -> None:
        self._refresh_pills()
        status = "Listener running" if running else "Listener stopped"
        self._set_status(status)
        self._append_log(f"{'🟢' if running else '🔴'} {status}")

    def _schedule_uptime(self) -> None:
        self._uptime_job = self.root.after(1000, self._tick_uptime)

    def _tick_uptime(self) -> None:
        self._uptime_lbl.configure(text=self.state.uptime_str())
        self._schedule_uptime()

    def _on_history_double_click(self, event) -> None:
        sel = self._hist_list.curselection()
        if not sel:
            return
        history = self.state.get_recent_history(200)
        if sel[0] >= len(history):
            return
        alert = history[sel[0]]
        from alert_ui import _show_details_popup
        raw = json.dumps(alert.raw_payload or alert.to_dict(), indent=2, ensure_ascii=False)
        _show_details_popup(self.root, alert, raw)

    def _on_close(self) -> None:
        if messagebox.askyesno("Quit", "Quit NetWatch?", parent=self.root):
            sound.stop_alert_sound()
            if self.state.settings.get("persist_history"):
                storage.save_history(self.state.history)
            if self._uptime_job:
                try:
                    self.root.after_cancel(self._uptime_job)
                except Exception:
                    pass
            self.root.destroy()


# ─────────────────────────────────────────────────────────────── widgets

class _Card(tk.Frame):
    """Card with a coloured top accent bar, small-caps title, and body."""

    def __init__(self, parent, title: str, accent: str = ACCENT_BLUE) -> None:
        super().__init__(parent, bg=BG_CARD)
        self._accent_bar = tk.Frame(self, bg=accent, height=3)
        self._accent_bar.pack(fill=tk.X)
        self.header_frame = tk.Frame(self, bg=BG_CARD)
        self.header_frame.pack(fill=tk.X, padx=10, pady=(5, 3))
        tk.Label(self.header_frame, text=title, font=("Segoe UI", 8, "bold"),
                 fg=config.COLOR_MUTED, bg=BG_CARD).pack(side=tk.LEFT)
        self.body = tk.Frame(self, bg=BG_SURFACE)
        self.body.pack(fill=tk.BOTH, expand=True, padx=8, pady=(0, 8))

    def set_accent(self, color: str) -> None:
        self._accent_bar.configure(bg=color)


class _Pill(tk.Frame):
    def __init__(self, parent, name: str, value: str, color: str) -> None:
        super().__init__(parent, bg=BG_DEEP)
        tk.Label(self, text=name, font=("Segoe UI", 7),
                 fg=config.COLOR_MUTED, bg=BG_DEEP).pack(side=tk.LEFT)
        self._lbl = tk.Label(self, text=value, font=("Segoe UI", 7, "bold"),
                              fg=BG_DEEP, bg=color, padx=6, pady=1)
        self._lbl.pack(side=tk.LEFT, padx=(2, 0))

    def set(self, value: str, color: str) -> None:
        self._lbl.configure(text=value, bg=color)


# ─────────────────────────────────────────────────────────────── helpers

def _hover(widget, off: str, on: str) -> None:
    widget.bind("<Enter>", lambda e: widget.configure(bg=on))
    widget.bind("<Leave>", lambda e: widget.configure(bg=off))


def _lighten(hex_color: str) -> str:
    try:
        h = hex_color.lstrip("#")
        r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
        return f"#{min(255,r+28):02x}{min(255,g+28):02x}{min(255,b+28):02x}"
    except Exception:
        return hex_color


def _source_tag(source: str) -> str:
    s = (source or "").lower()
    if "halo" in s:  return "[HALO ]"
    if "datto" in s: return "[DATTO]"
    return "[OTHER]"
