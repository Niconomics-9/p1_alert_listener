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
from tkinter import messagebox, scrolledtext, ttk
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

POLL_INTERVAL_MS = 150   # How often to drain the queue (ms)
LOG_MAX_LINES = 500       # Cap log panel lines


class Dashboard:
    """Main application window and event loop host."""

    def __init__(self, root: tk.Tk, app_state: "AppState") -> None:
        self.root = root
        self.state = app_state
        self._alert_window: AlertWindow | None = None
        self._uptime_job: str | None = None

        self._build_window()
        self._schedule_poll()
        self._schedule_uptime()
        log.info("Dashboard initialised")

    # ------------------------------------------------------------------ build

    def _build_window(self) -> None:
        r = self.root
        r.title(config.APP_TITLE)
        r.configure(bg=config.DASH_BG)
        r.geometry("1050x740")
        r.minsize(820, 580)
        r.protocol("WM_DELETE_WINDOW", self._on_close)

        self._build_top_bar()
        self._build_main_area()
        self._build_status_bar()

    # ── Top bar ───────────────────────────────────────────────────────────────

    def _build_top_bar(self) -> None:
        bar = tk.Frame(self.root, bg="#11111B", height=56)
        bar.pack(fill=tk.X)
        bar.pack_propagate(False)

        tk.Label(
            bar, text="⚡ P1 Alert Listener",
            font=("Arial Black", 18), fg=config.DASH_ACCENT, bg="#11111B",
        ).pack(side=tk.LEFT, padx=16, pady=8)

        # Uptime
        self._uptime_lbl = tk.Label(
            bar, text="Uptime: 00:00:00",
            font=("Arial", 10), fg=config.DASH_FG, bg="#11111B",
        )
        self._uptime_lbl.pack(side=tk.RIGHT, padx=16)

        # Status pills
        pill_frame = tk.Frame(bar, bg="#11111B")
        pill_frame.pack(side=tk.RIGHT, padx=8)

        self._pill_listener = _Pill(pill_frame, "Listener", "STOPPED", config.COLOR_ERR)
        self._pill_listener.pack(side=tk.LEFT, padx=4)

        self._pill_sound = _Pill(pill_frame, "Sound", "ON", config.COLOR_OK)
        self._pill_sound.pack(side=tk.LEFT, padx=4)

        self._pill_active = _Pill(pill_frame, "Active", "0", config.COLOR_MUTED)
        self._pill_active.pack(side=tk.LEFT, padx=4)

        self._pill_queued = _Pill(pill_frame, "Queued", "0", config.COLOR_MUTED)
        self._pill_queued.pack(side=tk.LEFT, padx=4)

    # ── Main area ─────────────────────────────────────────────────────────────

    def _build_main_area(self) -> None:
        main = tk.Frame(self.root, bg=config.DASH_BG)
        main.pack(fill=tk.BOTH, expand=True, padx=8, pady=4)

        # Left column: controls
        left = tk.Frame(main, bg=config.DASH_BG, width=220)
        left.pack(side=tk.LEFT, fill=tk.Y, padx=(0, 6))
        left.pack_propagate(False)
        self._build_controls(left)

        # Right column: panels
        right = tk.Frame(main, bg=config.DASH_BG)
        right.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self._build_right_panels(right)

    def _build_controls(self, parent: tk.Frame) -> None:
        # Connection info
        info = tk.LabelFrame(parent, text="Listener Info",
                              bg=config.DASH_BG, fg=config.DASH_ACCENT,
                              font=("Arial", 10, "bold"),
                              relief=tk.FLAT, bd=1)
        info.pack(fill=tk.X, pady=(0, 6))

        self._info_lbl = tk.Label(
            info, text=self._info_text(),
            font=("Consolas", 9), fg=config.DASH_FG, bg=config.DASH_BG,
            justify=tk.LEFT, anchor="w",
        )
        self._info_lbl.pack(padx=8, pady=6, anchor="w")

        # Button sections
        self._make_btn_section(parent, "Listener",
            [("▶  Start Listener", self._do_start_listener, config.COLOR_OK),
             ("⏹  Stop Listener",  self._do_stop_listener,  config.COLOR_ERR)])

        self._make_btn_section(parent, "Alert Actions",
            [("🧪  Test Alert",          self._do_test_alert,     config.DASH_ACCENT),
             ("🖥  Open Alert Screen",   self._do_open_alert,     "#FF8800"),
             ("🔇  Silence Current",     self._do_silence,        "#AA6600"),
             ("✔  Acknowledge Current", self._do_acknowledge,    "#006600")])

        self._make_btn_section(parent, "History",
            [("🗑  Clear History",       self._do_clear_history,  "#664444"),
             ("📋  Copy Last Alert JSON",self._do_copy_json,      config.DASH_BTN_BG)])

        self._make_btn_section(parent, "App",
            [("⚙  Settings",            self._do_settings,       config.DASH_BTN_BG),
             ("📁  Open Logs Folder",   self._do_open_logs,      config.DASH_BTN_BG),
             ("🔊  Test Sound",         self._do_test_sound,     config.DASH_BTN_BG)])

    def _make_btn_section(self, parent, title: str, buttons: list) -> None:
        frame = tk.LabelFrame(parent, text=title,
                               bg=config.DASH_BG, fg=config.DASH_FG,
                               font=("Arial", 9), relief=tk.FLAT, bd=1)
        frame.pack(fill=tk.X, pady=(0, 6))
        for label, cmd, color in buttons:
            tk.Button(
                frame, text=label, command=cmd,
                bg=color,
                fg="white" if color != config.DASH_BTN_BG else config.DASH_FG,
                font=("Arial", 10), relief=tk.FLAT, anchor="w",
                padx=8, pady=4, cursor="hand2",
                activebackground="#45475A",
            ).pack(fill=tk.X, padx=4, pady=2)

    def _build_right_panels(self, parent: tk.Frame) -> None:
        # Top: history + active/queued side by side
        top = tk.Frame(parent, bg=config.DASH_BG)
        top.pack(fill=tk.BOTH, expand=True)

        # Active alerts (left)
        alert_frame = tk.LabelFrame(top, text="Active Alert",
                                     bg=config.DASH_BG, fg=config.COLOR_ERR,
                                     font=("Arial", 10, "bold"), relief=tk.FLAT)
        alert_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 4))

        self._active_text = scrolledtext.ScrolledText(
            alert_frame, bg=config.DASH_LOG_BG, fg=config.DASH_FG,
            font=("Consolas", 10), relief=tk.FLAT, state=tk.DISABLED,
            height=8, wrap=tk.WORD,
        )
        self._active_text.pack(fill=tk.BOTH, expand=True, padx=4, pady=4)

        # Queued (right)
        queue_frame = tk.LabelFrame(top, text="Queued Alerts",
                                     bg=config.DASH_BG, fg=config.COLOR_WARN,
                                     font=("Arial", 10, "bold"), relief=tk.FLAT)
        queue_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(4, 0))

        self._queued_list = tk.Listbox(
            queue_frame, bg=config.DASH_LOG_BG, fg=config.DASH_FG,
            font=("Consolas", 10), relief=tk.FLAT, selectbackground="#313244",
            height=8,
        )
        self._queued_list.pack(fill=tk.BOTH, expand=True, padx=4, pady=4)

        # Middle: history
        hist_frame = tk.LabelFrame(parent, text="Alert History (newest first)",
                                    bg=config.DASH_BG, fg=config.DASH_FG,
                                    font=("Arial", 10, "bold"), relief=tk.FLAT)
        hist_frame.pack(fill=tk.BOTH, expand=True, pady=(6, 0))

        # Filter bar
        filter_row = tk.Frame(hist_frame, bg=config.DASH_BG)
        filter_row.pack(fill=tk.X, padx=4, pady=(4, 0))
        tk.Label(filter_row, text="Filter:", fg=config.DASH_FG, bg=config.DASH_BG,
                 font=("Arial", 9)).pack(side=tk.LEFT)
        self._filter_var = tk.StringVar()
        self._filter_var.trace_add("write", lambda *_: self._refresh_history())
        tk.Entry(filter_row, textvariable=self._filter_var,
                 bg="#313244", fg=config.DASH_FG,
                 insertbackground=config.DASH_FG,
                 relief=tk.FLAT, font=("Consolas", 9), width=30,
                 ).pack(side=tk.LEFT, padx=4)

        # History listbox with scrollbar
        hist_inner = tk.Frame(hist_frame, bg=config.DASH_BG)
        hist_inner.pack(fill=tk.BOTH, expand=True, padx=4, pady=4)

        hist_scroll = tk.Scrollbar(hist_inner)
        hist_scroll.pack(side=tk.RIGHT, fill=tk.Y)

        self._hist_list = tk.Listbox(
            hist_inner, bg=config.DASH_LOG_BG, fg=config.DASH_FG,
            font=("Consolas", 10), relief=tk.FLAT, selectbackground="#313244",
            yscrollcommand=hist_scroll.set,
        )
        self._hist_list.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        hist_scroll.config(command=self._hist_list.yview)
        self._hist_list.bind("<Double-Button-1>", self._on_history_double_click)

        # Bottom: event log
        log_frame = tk.LabelFrame(parent, text="Event Log",
                                   bg=config.DASH_BG, fg=config.DASH_FG,
                                   font=("Arial", 10, "bold"), relief=tk.FLAT)
        log_frame.pack(fill=tk.X, pady=(6, 0))

        self._log_text = scrolledtext.ScrolledText(
            log_frame, bg=config.DASH_LOG_BG, fg=config.DASH_LOG_FG,
            font=("Consolas", 9), relief=tk.FLAT, state=tk.DISABLED,
            height=7, wrap=tk.WORD,
        )
        self._log_text.pack(fill=tk.BOTH, expand=True, padx=4, pady=4)

    def _build_status_bar(self) -> None:
        bar = tk.Frame(self.root, bg="#11111B", height=22)
        bar.pack(side=tk.BOTTOM, fill=tk.X)
        bar.pack_propagate(False)

        self._status_lbl = tk.Label(
            bar, text="Ready",
            font=("Arial", 9), fg=config.DASH_FG, bg="#11111B",
        )
        self._status_lbl.pack(side=tk.LEFT, padx=10)

        s = self.state.settings
        tk.Label(
            bar,
            text=f"v1.0 | {s.get('host','127.0.0.1')}:{s.get('port', 8787)}",
            font=("Arial", 9), fg=config.COLOR_MUTED, bg="#11111B",
        ).pack(side=tk.RIGHT, padx=10)

    # ------------------------------------------------------------------ queue polling

    def _schedule_poll(self) -> None:
        self.root.after(POLL_INTERVAL_MS, self._poll_queue)

    def _poll_queue(self) -> None:
        """Drain the inter-thread queue and dispatch messages."""
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
        kind = msg.kind
        data = msg.data

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
        if self.state.settings.get("auto_open_fullscreen", True):
            self._open_alert_window(alert)
        # Start sound
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

        # Close previous if any
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
            log.info(f"Alert window opened: {alert.ticket_id}")
        except Exception as exc:
            log.error(f"Failed to open alert window: {exc}", exc_info=True)
            self._append_log(f"❌ Could not open alert window: {exc}")
            messagebox.showerror("Alert Error",
                                 f"Could not open full-screen alert:\n{exc}\n\n"
                                 "Alert is still active in the dashboard.",
                                 parent=self.root)

    # ------------------------------------------------------------------ actions

    def _do_start_listener(self) -> None:
        if self.state.listener_running:
            self._append_log("ℹ️ Listener already running")
            return
        from listener import ListenerThread
        t = ListenerThread(self.state)
        self.state.listener_thread = t
        t.start()
        self._append_log("▶ Starting listener…")
        self._set_status("Starting listener…")

    def _do_stop_listener(self) -> None:
        if not self.state.listener_running:
            self._append_log("ℹ️ Listener not running")
            return
        # Flask dev server can't be stopped cleanly from outside.
        # Best approach for a local tool: restart the app.
        messagebox.showinfo(
            "Stop Listener",
            "The Flask dev server cannot be stopped independently.\n"
            "To restart the listener, restart the application.\n\n"
            "Tip: Use Ctrl+C in the terminal to quit cleanly.",
            parent=self.root,
        )
        self._append_log("ℹ️ Listener stop requested – restart app to rebind port")

    def _do_test_alert(self) -> None:
        log.info("Test alert triggered from dashboard")
        self._append_log("🧪 Triggering test alert…")
        import threading

        def _inject():
            """Inject a test alert directly into state (no HTTP round-trip needed)."""
            from listener import _build_test_payload
            from parser import build_alert
            payload = _build_test_payload()
            alert = build_alert(payload)
            # Give the test alert a unique dedupe key so it always fires
            import uuid
            alert.dedupe_key = f"test:{uuid.uuid4().hex[:8]}"
            placement = self.state.push_alert(alert)
            if placement == "active":
                self.state.post("alert", alert)
                self.state.post("log", f"🚨 Test P1 ALERT: {alert.ticket_id}")
            else:
                self.state.post("queued_alert", alert)
                self.state.post("log", f"📋 Test alert queued (queue: {self.state.queue_count()})")

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
            # Un-silence → restart sound if there's an active alert
            if self.state.active_alert:
                s = self.state.settings
                sound.start_alert_sound(s.get("sound_mode", "beep"), s.get("wav_path", ""))
            self._append_log("🔊 Alert un-silenced")
            self._pill_sound.set("ON", config.COLOR_OK)
        # Update alert window button if open
        if self._alert_window:
            pass  # AlertWindow handles its own button state

    def _do_acknowledge(self) -> None:
        old = self.state.acknowledge_active()
        if old is None:
            self._append_log("ℹ️ No active alert to acknowledge")
            return
        sound.stop_alert_sound()
        log.info(f"Alert acknowledged: {old.ticket_id}")
        self._append_log(f"✔ Acknowledged: {old.ticket_id}")

        # Close alert window if open
        if self._alert_window:
            try:
                self._alert_window.destroy()
            except Exception:
                pass
            self._alert_window = None

        # If a new alert was promoted, open it
        if self.state.active_alert and self.state.settings.get("auto_open_fullscreen"):
            self._open_alert_window(self.state.active_alert)
            s = self.state.settings
            sound.start_alert_sound(s.get("sound_mode", "beep"), s.get("wav_path", ""))

        self._refresh_all()
        if self.state.settings.get("persist_history"):
            storage.save_history(self.state.history)

    def _do_next_alert(self) -> None:
        next_alert = self.state.next_queued()
        sound.stop_alert_sound()
        log.info("Cycling to next queued alert")
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
        alert = history[0]
        text = json.dumps(alert.to_dict(), indent=2, ensure_ascii=False)
        self.root.clipboard_clear()
        self.root.clipboard_append(text)
        self._append_log("📋 Latest alert JSON copied to clipboard")

    def _do_settings(self) -> None:
        def _on_save(new_settings):
            self.state.settings.update(new_settings)
            storage.save_settings(self.state.settings)
            self._refresh_info()
            self._append_log("⚙ Settings updated")
            log.info("Settings updated from dialog")

        SettingsDialog(self.root, self.state.settings, _on_save)

    def _do_open_logs(self) -> None:
        logs_dir = config.LOGS_DIR
        logs_dir.mkdir(parents=True, exist_ok=True)
        try:
            os.startfile(str(logs_dir))
        except Exception as exc:
            messagebox.showinfo("Logs",
                                f"Logs folder: {logs_dir}\n\nCould not open: {exc}",
                                parent=self.root)

    def _do_test_sound(self) -> None:
        s = self.state.settings
        self._append_log("🔊 Playing test sound…")
        import threading
        threading.Thread(
            target=sound.test_sound,
            args=(s.get("sound_mode", "beep"), s.get("wav_path", "")),
            daemon=True,
        ).start()

    # ------------------------------------------------------------------ refresh helpers

    def _refresh_all(self) -> None:
        self._refresh_active()
        self._refresh_queued()
        self._refresh_history()
        self._refresh_pills()
        self._refresh_info()
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
            self._active_text.insert(tk.END,
                f"Ticket:   {a.ticket_id}\n"
                f"Client:   {a.client}\n"
                f"Summary:  {a.summary}\n"
                f"Source:   {a.source}\n"
                f"Priority: {a.priority}  Severity: {a.severity or '—'}\n"
                f"Team:     {a.assigned_team or '—'}\n"
                f"Received: {a.display_received()}\n"
            )
        else:
            self._active_text.insert(tk.END, "(no active alert)")
        self._active_text.configure(state=tk.DISABLED)

    def _refresh_queued(self) -> None:
        self._queued_list.delete(0, tk.END)
        with self.state._lock:
            for a in self.state.alert_queue_list:
                self._queued_list.insert(tk.END,
                    f"{a.display_received()}  {a.ticket_id}  {a.client[:24]}")

    def _refresh_history(self) -> None:
        self._hist_list.delete(0, tk.END)
        filt = self._filter_var.get().strip().lower()
        for a in self.state.get_recent_history(200):
            source_tag = _source_tag(a.source)
            row = f"{a.display_received()}  {source_tag}  {a.ticket_id}  {a.client[:24]}  {a.short_summary(40)}"
            if filt and filt not in row.lower():
                continue
            self._hist_list.insert(tk.END, row)

    def _refresh_pills(self) -> None:
        if self.state.listener_running:
            self._pill_listener.set("RUNNING", config.COLOR_OK)
        else:
            self._pill_listener.set("STOPPED", config.COLOR_ERR)

        if self.state.sound_silenced:
            self._pill_sound.set("MUTED", config.COLOR_MUTED)
        else:
            self._pill_sound.set("ON", config.COLOR_OK)

        active_count = 1 if self.state.active_alert else 0
        self._pill_active.set(
            str(active_count),
            config.COLOR_ERR if active_count > 0 else config.COLOR_MUTED,
        )
        q = self.state.queue_count()
        self._pill_queued.set(
            str(q),
            config.COLOR_WARN if q > 0 else config.COLOR_MUTED,
        )

    def _refresh_info(self) -> None:
        self._info_lbl.configure(text=self._info_text())

    def _info_text(self) -> str:
        s = self.state.settings
        host = s.get("host", config.DEFAULT_HOST)
        port = s.get("port", config.DEFAULT_PORT)
        auth = "ON" if s.get("auth_enabled") else "OFF"
        snd = s.get("sound_mode", "beep").upper()
        lan = "LAN" if s.get("allow_lan") else "localhost"
        return (
            f"Host:  {host}:{port}\n"
            f"Mode:  {lan}\n"
            f"Auth:  {auth}\n"
            f"Sound: {snd}"
        )

    # ------------------------------------------------------------------ log panel

    def _append_log(self, message: str) -> None:
        ts = now_display()
        line = f"[{ts}] {message}\n"
        self._log_text.configure(state=tk.NORMAL)
        self._log_text.insert(tk.END, line)
        # Trim log panel
        lines = int(self._log_text.index(tk.END).split(".")[0])
        if lines > LOG_MAX_LINES:
            self._log_text.delete("1.0", f"{lines - LOG_MAX_LINES}.0")
        self._log_text.see(tk.END)
        self._log_text.configure(state=tk.DISABLED)

    # ------------------------------------------------------------------ status

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

    # ------------------------------------------------------------------ uptime

    def _schedule_uptime(self) -> None:
        self._uptime_job = self.root.after(1000, self._tick_uptime)

    def _tick_uptime(self) -> None:
        self._uptime_lbl.configure(text=f"Uptime: {self.state.uptime_str()}")
        self._schedule_uptime()

    # ------------------------------------------------------------------ history double-click

    def _on_history_double_click(self, event) -> None:
        sel = self._hist_list.curselection()
        if not sel:
            return
        idx = sel[0]
        history = self.state.get_recent_history(200)
        if idx >= len(history):
            return
        alert = history[idx]
        import json as _json
        from alert_ui import _show_details_popup
        raw = _json.dumps(alert.raw_payload or alert.to_dict(), indent=2, ensure_ascii=False)
        _show_details_popup(self.root, alert, raw)

    # ------------------------------------------------------------------ close

    def _on_close(self) -> None:
        if messagebox.askyesno("Quit", "Quit P1 Alert Listener?", parent=self.root):
            log.info("Dashboard close requested – shutting down")
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
# Status Pill widget
# ---------------------------------------------------------------------------

class _Pill(tk.Frame):
    """Small coloured label used as a status indicator."""

    def __init__(self, parent, name: str, value: str, color: str) -> None:
        super().__init__(parent, bg="#11111B")
        tk.Label(self, text=name + ":", font=("Arial", 8),
                 fg=config.DASH_FG, bg="#11111B").pack(side=tk.LEFT)
        self._lbl = tk.Label(
            self, text=value,
            font=("Arial", 8, "bold"),
            fg="#11111B", bg=color,
            padx=5, pady=1,
        )
        self._lbl.pack(side=tk.LEFT, padx=2)

    def set(self, value: str, color: str) -> None:
        self._lbl.configure(text=value, bg=color)


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------

def _source_tag(source: str) -> str:
    """Return a short fixed-width tag for the history list."""
    s = (source or "").lower()
    if "halo" in s:
        return "[HALO  ]"
    if "datto" in s:
        return "[DATTO ]"
    return "[OTHER ]"
