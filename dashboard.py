"""
dashboard.py – Main Tkinter dashboard window.

All GUI operations run on the Tkinter main thread.
Communicates with the listener via AppState.alert_queue (queue.Queue).
Polls the queue every POLL_INTERVAL_MS milliseconds using root.after().
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

# ── Design tokens ────────────────────────────────────────────────────────────
FONT_UI    = ("Segoe UI", 10)
FONT_UI_SB = ("Segoe UI Semibold", 10)
FONT_TITLE = ("Segoe UI", 18, "bold")
FONT_MONO  = ("Consolas", 10)
FONT_MONO_SM = ("Consolas", 9)
FONT_LABEL   = ("Segoe UI", 9)

BG_SURFACE  = "#1E1E2E"   # base background
BG_CARD     = "#242436"   # card background
BG_DEEP     = "#11111B"   # top/bottom bars
BG_INPUT    = "#313244"   # entry fields
BG_TOOLBAR  = "#181825"   # toolbar strip

BTN_PRIMARY  = "#4C9BE8"  # blue action
BTN_DANGER   = "#F38BA8"  # red
BTN_SUCCESS  = "#A6E3A1"  # green
BTN_MUTED    = "#313244"  # neutral
BTN_WARN     = "#F9E2AF"  # amber

ACCENT_RED  = "#F38BA8"
ACCENT_BLUE = "#89B4FA"


class Dashboard:
    """Main application window and event loop host."""

    def __init__(self, root: tk.Tk, app_state: "AppState") -> None:
        self.root = root
        self.state = app_state
        self._alert_window: AlertWindow | None = None
        self._uptime_job: str | None = None
        self._log_visible = True

        self._build_window()
        self._schedule_poll()
        self._schedule_uptime()
        log.info("Dashboard initialised")

    # ------------------------------------------------------------------ build

    def _build_window(self) -> None:
        r = self.root
        r.title(config.APP_TITLE)
        r.configure(bg=BG_SURFACE)
        r.geometry("1100x760")
        r.minsize(900, 600)
        r.protocol("WM_DELETE_WINDOW", self._on_close)

        self._build_top_bar()
        self._build_toolbar()
        self._build_cards_area()
        self._build_log_drawer()
        self._build_status_bar()

    # ── Top bar ───────────────────────────────────────────────────────────────

    def _build_top_bar(self) -> None:
        bar = tk.Frame(self.root, bg=BG_DEEP, height=54)
        bar.pack(fill=tk.X)
        bar.pack_propagate(False)

        # Brand
        brand = tk.Frame(bar, bg=BG_DEEP)
        brand.pack(side=tk.LEFT, padx=20, pady=0)
        tk.Label(brand, text="⚡ NetWatch", font=FONT_TITLE,
                 fg=ACCENT_BLUE, bg=BG_DEEP).pack(side=tk.LEFT)

        # Right side
        right = tk.Frame(bar, bg=BG_DEEP)
        right.pack(side=tk.RIGHT, padx=16)

        self._uptime_lbl = tk.Label(right, text="00:00:00",
                                     font=("Segoe UI", 9), fg=config.COLOR_MUTED, bg=BG_DEEP)
        self._uptime_lbl.pack(side=tk.RIGHT, padx=(12, 0))
        tk.Label(right, text="Uptime", font=("Segoe UI", 8),
                 fg=config.COLOR_MUTED, bg=BG_DEEP).pack(side=tk.RIGHT)

        # Status pills
        pills = tk.Frame(bar, bg=BG_DEEP)
        pills.pack(side=tk.RIGHT, padx=12)

        self._pill_listener = _Pill(pills, "Listener", "STOPPED", config.COLOR_ERR)
        self._pill_listener.pack(side=tk.LEFT, padx=3)
        self._pill_sound = _Pill(pills, "Sound", "ON", config.COLOR_OK)
        self._pill_sound.pack(side=tk.LEFT, padx=3)
        self._pill_active = _Pill(pills, "Active", "0", config.COLOR_MUTED)
        self._pill_active.pack(side=tk.LEFT, padx=3)
        self._pill_queued = _Pill(pills, "Queued", "0", config.COLOR_MUTED)
        self._pill_queued.pack(side=tk.LEFT, padx=3)

    # ── Toolbar ───────────────────────────────────────────────────────────────

    def _build_toolbar(self) -> None:
        bar = tk.Frame(self.root, bg=BG_TOOLBAR, height=48)
        bar.pack(fill=tk.X)
        bar.pack_propagate(False)

        left = tk.Frame(bar, bg=BG_TOOLBAR)
        left.pack(side=tk.LEFT, padx=12, pady=8)

        # Primary actions
        self._tb_btn(left, "↺  Restart",    self._do_restart_listener, BTN_MUTED)
        self._tb_sep(left)
        self._tb_btn(left, "🧪  Test Alert", self._do_test_alert,       BTN_PRIMARY)
        self._tb_btn(left, "🖥  Open Alert", self._do_open_alert,        BTN_WARN,   fg="#11111B")
        self._tb_btn(left, "🔇  Silence",    self._do_silence,           BTN_MUTED)
        self._tb_btn(left, "✔  Acknowledge",self._do_acknowledge,        BTN_SUCCESS, fg="#11111B")

        right = tk.Frame(bar, bg=BG_TOOLBAR)
        right.pack(side=tk.RIGHT, padx=12, pady=8)

        self._tb_btn(right, "⚙  Settings",  self._do_settings,    BTN_MUTED)
        self._tb_btn(right, "🔊  Sound",     self._do_test_sound,  BTN_MUTED)
        self._tb_btn(right, "📁  Logs",      self._do_open_logs,   BTN_MUTED)
        self._tb_btn(right, "📋  Copy JSON", self._do_copy_json,   BTN_MUTED)
        self._tb_btn(right, "🗑  Clear",     self._do_clear_history, "#442222", fg="#FFAAAA")

    def _tb_btn(self, parent, label, cmd, bg, fg="white"):
        btn = tk.Label(
            parent, text=label, font=("Segoe UI", 9, "bold"),
            fg=fg, bg=bg, padx=12, pady=4,
            cursor="hand2", relief=tk.FLAT,
        )
        btn.pack(side=tk.LEFT, padx=3)
        btn.bind("<Button-1>", lambda e: cmd())
        _hover(btn, bg, _lighten(bg))

    def _tb_sep(self, parent):
        tk.Frame(parent, bg="#313244", width=1, height=28).pack(side=tk.LEFT, padx=6)

    # ── Cards area ────────────────────────────────────────────────────────────

    def _build_cards_area(self) -> None:
        area = tk.Frame(self.root, bg=BG_SURFACE)
        area.pack(fill=tk.BOTH, expand=True, padx=12, pady=(10, 0))

        # Top row: Active Alert + Queue side by side
        top_row = tk.Frame(area, bg=BG_SURFACE)
        top_row.pack(fill=tk.BOTH, expand=False)

        self._build_active_card(top_row)
        self._build_queue_card(top_row)

        # Bottom: History full width
        self._build_history_card(area)

    def _build_active_card(self, parent) -> None:
        outer = _Card(parent, "ACTIVE ALERT", accent=ACCENT_RED)
        outer.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 6), pady=(0, 8))

        self._active_text = scrolledtext.ScrolledText(
            outer.body, bg=BG_SURFACE, fg=config.DASH_FG,
            font=FONT_MONO, relief=tk.FLAT, state=tk.DISABLED,
            height=9, wrap=tk.WORD, bd=0,
        )
        self._active_text.pack(fill=tk.BOTH, expand=True)
        self._active_card = outer

    def _build_queue_card(self, parent) -> None:
        outer = _Card(parent, "QUEUE", accent=BTN_WARN)
        outer.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(6, 0), pady=(0, 8))

        self._queued_list = tk.Listbox(
            outer.body, bg=BG_SURFACE, fg=config.DASH_FG,
            font=FONT_MONO_SM, relief=tk.FLAT, selectbackground=BTN_MUTED,
            height=9, bd=0, highlightthickness=0,
        )
        self._queued_list.pack(fill=tk.BOTH, expand=True)

    def _build_history_card(self, parent) -> None:
        outer = _Card(parent, "HISTORY", accent=ACCENT_BLUE)
        outer.pack(fill=tk.BOTH, expand=True, pady=(0, 8))

        # Filter row inside card header area
        filter_row = tk.Frame(outer.header_frame, bg=BG_CARD)
        filter_row.pack(side=tk.RIGHT, padx=8)
        tk.Label(filter_row, text="Filter:", font=FONT_LABEL,
                 fg=config.COLOR_MUTED, bg=BG_CARD).pack(side=tk.LEFT)
        self._filter_var = tk.StringVar()
        self._filter_var.trace_add("write", lambda *_: self._refresh_history())
        tk.Entry(filter_row, textvariable=self._filter_var,
                 bg=BG_INPUT, fg=config.DASH_FG,
                 insertbackground=config.DASH_FG,
                 relief=tk.FLAT, font=FONT_MONO_SM, width=28,
                 ).pack(side=tk.LEFT, padx=(4, 0))

        # Listbox with scrollbar
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

    # ── Log drawer ────────────────────────────────────────────────────────────

    def _build_log_drawer(self) -> None:
        self._log_frame = tk.Frame(self.root, bg=BG_DEEP)
        self._log_frame.pack(fill=tk.X, padx=12, pady=(0, 4))

        # Header row
        hdr = tk.Frame(self._log_frame, bg=BG_DEEP)
        hdr.pack(fill=tk.X)
        tk.Label(hdr, text="EVENT LOG", font=("Segoe UI", 8, "bold"),
                 fg=config.COLOR_MUTED, bg=BG_DEEP).pack(side=tk.LEFT, padx=8, pady=4)
        self._log_toggle_lbl = tk.Label(hdr, text="▼ hide", font=FONT_LABEL,
                                         fg=ACCENT_BLUE, bg=BG_DEEP, cursor="hand2")
        self._log_toggle_lbl.pack(side=tk.RIGHT, padx=8)
        self._log_toggle_lbl.bind("<Button-1>", lambda e: self._toggle_log())

        self._log_body = tk.Frame(self._log_frame, bg=BG_DEEP)
        self._log_body.pack(fill=tk.X)
        self._log_text = scrolledtext.ScrolledText(
            self._log_body, bg=BG_DEEP, fg=config.DASH_LOG_FG,
            font=FONT_MONO_SM, relief=tk.FLAT, state=tk.DISABLED,
            height=6, wrap=tk.WORD, bd=0,
        )
        self._log_text.pack(fill=tk.X, padx=8, pady=(0, 6))

    def _toggle_log(self) -> None:
        if self._log_visible:
            self._log_body.pack_forget()
            self._log_toggle_lbl.configure(text="▲ show")
        else:
            self._log_body.pack(fill=tk.X)
            self._log_toggle_lbl.configure(text="▼ hide")
        self._log_visible = not self._log_visible

    # ── Status bar ────────────────────────────────────────────────────────────

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

    # ------------------------------------------------------------------ queue polling

    def _schedule_poll(self) -> None:
        self.root.after(POLL_INTERVAL_MS, self._poll_queue)

    def _poll_queue(self) -> None:
        try:
            q = self.state.alert_queue
            while not q.empty():
                msg = q.get_nowait()
                self._dispatch(msg)
        except Exception as exc:
            log.error(f"Queue poll error: {exc}", exc_info=True)
        finally:
            self._schedule_poll()

    def _dispatch(self, msg) -> None:
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
        else:
            log.debug(f"Unknown queue msg kind: {kind!r}")

    def _handle_new_alert(self, alert: Alert) -> None:
        self._refresh_all()
        # Red accent on active card
        self._active_card.set_accent(ACCENT_RED)
        if self.state.settings.get("auto_open_fullscreen", True):
            self._open_alert_window(alert)
        if not self.state.sound_silenced:
            s = self.state.settings
            sound.start_alert_sound(s.get("sound_mode", "beep"), s.get("wav_path", ""))
        self._set_status(f"🚨 P1 ALERT: {alert.ticket_id}")
        log.info(f"Dashboard handling alert: {alert.ticket_id}")

    # ------------------------------------------------------------------ alert window

    def _open_alert_window(self, alert: Alert | None = None) -> None:
        if alert is None:
            alert = self.state.active_alert
        if alert is None:
            messagebox.showinfo("No Alert", "No active alert to display.", parent=self.root)
            return
        if self._alert_window is not None:
            try:
                self._alert_window.destroy()
            except Exception:
                pass
            self._alert_window = None
        s = self.state.settings
        try:
            self._alert_window = AlertWindow(
                root=self.root,
                alert=alert,
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
            messagebox.showerror("Alert Error",
                                 f"Could not open full-screen alert:\n{exc}",
                                 parent=self.root)

    # ------------------------------------------------------------------ actions

    def _do_restart_listener(self) -> None:
        """Stop the current listener thread and start a fresh one."""
        import threading
        def _restart():
            t = self.state.listener_thread
            if t is not None and t.is_alive():
                self.state.post("log", "↺ Restarting listener…")
                try:
                    t.stop()
                    t.join(timeout=4)
                except Exception as exc:
                    log.warning(f"Listener stop error: {exc}")

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
            payload = _build_test_payload()
            alert = build_alert(payload)
            alert.dedupe_key = f"test:{uuid.uuid4().hex[:8]}"
            placement = self.state.push_alert(alert)
            if placement == "active":
                self.state.post("alert", alert)
                self.state.post("log", f"🚨 Test P1 ALERT: {alert.ticket_id}")
            else:
                self.state.post("queued_alert", alert)
                self.state.post("log", f"📋 Test alert queued ({self.state.queue_count()})")
        threading.Thread(target=_inject, daemon=True).start()

    def _do_open_alert(self) -> None:
        self._open_alert_window()

    def _do_silence(self) -> None:
        self.state.silence_active()
        if self.state.sound_silenced:
            sound.stop_alert_sound()
            self._append_log("🔇 Alert silenced")
            self._pill_sound.set("MUTED", config.COLOR_MUTED)
        else:
            if self.state.active_alert:
                s = self.state.settings
                sound.start_alert_sound(s.get("sound_mode", "beep"), s.get("wav_path", ""))
            self._append_log("🔊 Alert un-silenced")
            self._pill_sound.set("ON", config.COLOR_OK)

    def _do_acknowledge(self) -> None:
        old = self.state.acknowledge_active()
        if old is None:
            self._append_log("ℹ️ No active alert to acknowledge")
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
        self._refresh_all()
        if not self.state.active_alert:
            self._active_card.set_accent(ACCENT_BLUE)
        if self.state.settings.get("persist_history"):
            storage.save_history(self.state.history)

    def _do_next_alert(self) -> None:
        next_alert = self.state.next_queued()
        sound.stop_alert_sound()
        self._append_log("⏭ Next alert")
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
        self._append_log("📋 Latest alert JSON copied to clipboard")

    def _do_settings(self) -> None:
        def _on_save(new_settings):
            self.state.settings.update(new_settings)
            storage.save_settings(self.state.settings)
            self._append_log("⚙ Settings updated")
            log.info("Settings updated from dialog")
        SettingsDialog(self.root, self.state.settings, _on_save)

    def _do_open_logs(self) -> None:
        config.LOGS_DIR.mkdir(parents=True, exist_ok=True)
        try:
            os.startfile(str(config.LOGS_DIR))
        except Exception as exc:
            messagebox.showinfo("Logs", f"Logs: {config.LOGS_DIR}\n\n{exc}", parent=self.root)

    def _do_test_sound(self) -> None:
        s = self.state.settings
        self._append_log("🔊 Playing test sound…")
        import threading
        threading.Thread(
            target=sound.test_sound,
            args=(s.get("sound_mode", "beep"), s.get("wav_path", "")),
            daemon=True,
        ).start()

    # ------------------------------------------------------------------ refresh

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
            lines = [
                ("Ticket   ", a.ticket_id),
                ("Client   ", a.client),
                ("Summary  ", a.summary),
                ("Source   ", a.source),
                ("Priority ", f"{a.priority}  ·  {a.severity or '—'}"),
                ("Team     ", a.assigned_team or "—"),
                ("Received ", a.display_received()),
            ]
            for label, val in lines:
                self._active_text.insert(tk.END, label, "lbl")
                self._active_text.insert(tk.END, f"  {val}\n")
            self._active_text.tag_configure("lbl", foreground=config.COLOR_MUTED,
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
                    tk.END, f"  {a.display_received()}  {a.ticket_id}  {a.client[:22]}")

    def _refresh_history(self) -> None:
        self._hist_list.delete(0, tk.END)
        filt = self._filter_var.get().strip().lower()
        for a in self.state.get_recent_history(200):
            tag = _source_tag(a.source)
            row = f"  {a.display_received()}  {tag}  {a.ticket_id}  {a.client[:22]}  {a.short_summary(42)}"
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
        self._pill_active.set(str(active),
                               config.COLOR_ERR if active else config.COLOR_MUTED)
        q = self.state.queue_count()
        self._pill_queued.set(str(q), config.COLOR_WARN if q else config.COLOR_MUTED)

    # ------------------------------------------------------------------ log

    def _append_log(self, message: str) -> None:
        ts = now_display()
        self._log_text.configure(state=tk.NORMAL)
        self._log_text.insert(tk.END, f"[{ts}]  {message}\n")
        lines = int(self._log_text.index(tk.END).split(".")[0])
        if lines > LOG_MAX_LINES:
            self._log_text.delete("1.0", f"{lines - LOG_MAX_LINES}.0")
        self._log_text.see(tk.END)
        self._log_text.configure(state=tk.DISABLED)

    # ------------------------------------------------------------------ misc

    def _set_status(self, text: str) -> None:
        self._status_lbl.configure(text=text)

    def _on_listener_status(self, running: bool) -> None:
        self._refresh_pills()
        if running:
            self._set_status("Listener running")
            self._append_log("🟢 Listener is running")
        else:
            self._set_status("Listener stopped")
            self._append_log("🔴 Listener stopped")

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


# ---------------------------------------------------------------------------
# Widgets
# ---------------------------------------------------------------------------

class _Card(tk.Frame):
    """A card with a coloured top accent bar, title, and body area."""

    def __init__(self, parent, title: str, accent: str = ACCENT_BLUE) -> None:
        super().__init__(parent, bg=BG_CARD, padx=0, pady=0)
        self._accent_bar = tk.Frame(self, bg=accent, height=3)
        self._accent_bar.pack(fill=tk.X)

        self.header_frame = tk.Frame(self, bg=BG_CARD)
        self.header_frame.pack(fill=tk.X, padx=10, pady=(6, 4))
        tk.Label(self.header_frame, text=title,
                 font=("Segoe UI", 8, "bold"),
                 fg=config.COLOR_MUTED, bg=BG_CARD).pack(side=tk.LEFT)

        self.body = tk.Frame(self, bg=BG_SURFACE)
        self.body.pack(fill=tk.BOTH, expand=True, padx=8, pady=(0, 8))

    def set_accent(self, color: str) -> None:
        self._accent_bar.configure(bg=color)


class _Pill(tk.Frame):
    """Compact status indicator."""

    def __init__(self, parent, name: str, value: str, color: str) -> None:
        super().__init__(parent, bg=BG_DEEP)
        tk.Label(self, text=name, font=("Segoe UI", 7),
                 fg=config.COLOR_MUTED, bg=BG_DEEP).pack(side=tk.LEFT)
        self._lbl = tk.Label(self, text=value, font=("Segoe UI", 7, "bold"),
                              fg=BG_DEEP, bg=color, padx=6, pady=1)
        self._lbl.pack(side=tk.LEFT, padx=(2, 0))

    def set(self, value: str, color: str) -> None:
        self._lbl.configure(text=value, bg=color)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _hover(widget, off: str, on: str) -> None:
    widget.bind("<Enter>", lambda e: widget.configure(bg=on))
    widget.bind("<Leave>", lambda e: widget.configure(bg=off))


def _lighten(hex_color: str) -> str:
    """Return a slightly lighter version of a hex colour for hover."""
    try:
        h = hex_color.lstrip("#")
        r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
        r = min(255, r + 30)
        g = min(255, g + 30)
        b = min(255, b + 30)
        return f"#{r:02x}{g:02x}{b:02x}"
    except Exception:
        return hex_color


def _source_tag(source: str) -> str:
    s = (source or "").lower()
    if "halo" in s:
        return "[HALO ]"
    if "datto" in s:
        return "[DATTO]"
    return "[OTHER]"
