"""
alert_ui.py – Full-screen flashing P1 alert window (Tkinter).

Rules:
  - Always runs on the main Tkinter thread (called via root.after).
  - Flashes between ALERT_BG_1 (red) and ALERT_BG_2 (dark red/black).
  - Stays always-on-top while active.
  - Keyboard shortcuts: Esc=ack, S=silence, N=next, D=details, T=test.
  - Closing via window X recovers gracefully (treats as acknowledge).
"""
from __future__ import annotations

import logging
import tkinter as tk
from tkinter import messagebox, scrolledtext
from typing import Callable

import config
from models import Alert

log = logging.getLogger("p1alert.alert_ui")


class AlertWindow:
    """Full-screen P1 alert overlay."""

    def __init__(
        self,
        root: tk.Tk,
        alert: Alert,
        queue_count: int,
        on_acknowledge: Callable[[], None],
        on_silence: Callable[[], None],
        on_next: Callable[[], None],
        on_test: Callable[[], None],
        flash_interval_ms: int = 600,
        always_on_top: bool = True,
    ) -> None:
        self.root = root
        self.alert = alert
        self.queue_count = queue_count
        self.on_acknowledge = on_acknowledge
        self.on_silence = on_silence
        self.on_next = on_next
        self.on_test = on_test
        self.flash_interval_ms = flash_interval_ms
        self.always_on_top = always_on_top

        self._flash_state = False
        self._flash_job: str | None = None
        self._win: tk.Toplevel | None = None
        self._destroyed = False

        self._build()
        log.info(f"AlertWindow opened for {alert.ticket_id}")

    # ------------------------------------------------------------------ build

    def _build(self) -> None:
        win = tk.Toplevel(self.root)
        self._win = win
        win.title("⚠ P1 INCIDENT")
        win.configure(bg=config.ALERT_BG_1)
        win.attributes("-fullscreen", True)
        win.attributes("-topmost", self.always_on_top)
        win.protocol("WM_DELETE_WINDOW", self._on_close)

        # ── Key bindings ─────────────────────────────────────────────────
        win.bind("<Escape>", lambda e: self._do_acknowledge())
        win.bind("<s>", lambda e: self._do_silence())
        win.bind("<S>", lambda e: self._do_silence())
        win.bind("<n>", lambda e: self._do_next())
        win.bind("<N>", lambda e: self._do_next())
        win.bind("<d>", lambda e: self._do_details())
        win.bind("<D>", lambda e: self._do_details())
        win.bind("<t>", lambda e: self._do_test())
        win.bind("<T>", lambda e: self._do_test())
        win.focus_set()

        # ── Main frame ───────────────────────────────────────────────────
        self._frame = tk.Frame(win, bg=config.ALERT_BG_1)
        self._frame.pack(expand=True, fill=tk.BOTH, padx=40, pady=30)

        # ── Header ───────────────────────────────────────────────────────
        tk.Label(
            self._frame,
            text="⚠  P1 INCIDENT  ⚠",
            font=("Arial Black", 52, "bold"),
            fg=config.ALERT_FG,
            bg=config.ALERT_BG_1,
        ).pack(pady=(20, 10))

        # ── Separator ────────────────────────────────────────────────────
        self._sep = tk.Frame(self._frame, height=4, bg=config.ALERT_FG)
        self._sep.pack(fill=tk.X, pady=10)

        # ── Alert details grid ───────────────────────────────────────────
        detail_frame = tk.Frame(self._frame, bg=config.ALERT_BG_1)
        detail_frame.pack(fill=tk.X, pady=10)

        self._detail_rows: list[tk.Label] = []
        rows = [
            ("Ticket", self.alert.ticket_id),
            ("Client", self.alert.client),
            ("Summary", self.alert.summary),
            ("Source", self.alert.source),
            ("Priority", self.alert.priority),
            ("Severity", self.alert.severity or "—"),
            ("Team", self.alert.assigned_team or "—"),
            ("Received", self.alert.display_received()),
        ]
        for label_text, value_text in rows:
            row = tk.Frame(detail_frame, bg=config.ALERT_BG_1)
            row.pack(fill=tk.X, pady=2)
            tk.Label(
                row,
                text=f"{label_text}:",
                font=("Arial", 16, "bold"),
                fg="#FFCCCC",
                bg=config.ALERT_BG_1,
                width=12,
                anchor="e",
            ).pack(side=tk.LEFT, padx=(10, 8))
            lbl = tk.Label(
                row,
                text=value_text,
                font=("Arial", 18),
                fg=config.ALERT_FG,
                bg=config.ALERT_BG_1,
                anchor="w",
                wraplength=900,
                justify="left",
            )
            lbl.pack(side=tk.LEFT, fill=tk.X, expand=True)
            self._detail_rows.append(lbl)

        # ── Queue indicator ───────────────────────────────────────────────
        self._queue_lbl = tk.Label(
            self._frame,
            text=self._queue_text(),
            font=("Arial", 14, "bold"),
            fg="#FFFF88",
            bg=config.ALERT_BG_1,
        )
        self._queue_lbl.pack(pady=8)

        # ── Button row ────────────────────────────────────────────────────
        btn_frame = tk.Frame(self._frame, bg=config.ALERT_BG_1)
        btn_frame.pack(pady=20)

        btn_cfg = dict(
            font=("Arial", 16, "bold"),
            width=16,
            height=2,
            relief=tk.RAISED,
            bd=3,
            cursor="hand2",
        )

        self._ack_btn = tk.Button(
            btn_frame, text="✔ ACKNOWLEDGE\n[Esc]",
            bg="#00AA00", fg="white",
            command=self._do_acknowledge, **btn_cfg
        )
        self._ack_btn.grid(row=0, column=0, padx=10)

        self._sil_btn = tk.Button(
            btn_frame, text="🔇 SILENCE\n[S]",
            bg="#AA6600", fg="white",
            command=self._do_silence, **btn_cfg
        )
        self._sil_btn.grid(row=0, column=1, padx=10)

        self._next_btn = tk.Button(
            btn_frame, text="⏭ NEXT ALERT\n[N]",
            bg="#005588", fg="white",
            command=self._do_next, **btn_cfg,
            state=tk.NORMAL if self.queue_count > 0 else tk.DISABLED,
        )
        self._next_btn.grid(row=0, column=2, padx=10)

        tk.Button(
            btn_frame, text="🔍 DETAILS\n[D]",
            bg="#444466", fg="white",
            command=self._do_details, **btn_cfg
        ).grid(row=0, column=3, padx=10)

        tk.Button(
            btn_frame, text="🔊 TEST\n[T]",
            bg="#224422", fg="white",
            command=self._do_test, **btn_cfg
        ).grid(row=0, column=4, padx=10)

        # ── Keyboard hint ────────────────────────────────────────────────
        tk.Label(
            self._frame,
            text="Esc=Acknowledge  S=Silence  N=Next  D=Details  T=Test",
            font=("Arial", 11),
            fg="#BBBBBB",
            bg=config.ALERT_BG_1,
        ).pack(side=tk.BOTTOM, pady=10)

        # ── Start flashing ────────────────────────────────────────────────
        self._schedule_flash()

    # ------------------------------------------------------------------ flash

    def _schedule_flash(self) -> None:
        if self._destroyed or self._win is None:
            return
        self._flash_job = self._win.after(self.flash_interval_ms, self._do_flash)

    def _do_flash(self) -> None:
        if self._destroyed or self._win is None:
            return
        self._flash_state = not self._flash_state
        bg = config.ALERT_BG_2 if self._flash_state else config.ALERT_BG_1
        self._apply_bg(bg)
        self._schedule_flash()

    def _apply_bg(self, bg: str) -> None:
        """Recursively update background on all flash-aware widgets."""
        if self._win is None:
            return
        for widget in (self._win, self._frame, self._sep, self._queue_lbl):
            try:
                widget.configure(bg=bg)
            except Exception:
                pass
        # Update all labels in detail rows
        for lbl in self._detail_rows:
            try:
                lbl.configure(bg=bg)
            except Exception:
                pass
        # Also update their parent frames
        try:
            for child in self._frame.winfo_children():
                _recursive_bg(child, bg)
        except Exception:
            pass

    # ------------------------------------------------------------------ actions

    def _do_acknowledge(self) -> None:
        log.info(f"Alert acknowledged: {self.alert.ticket_id}")
        self._cleanup()
        self.on_acknowledge()

    def _do_silence(self) -> None:
        log.info(f"Alert silence toggled: {self.alert.ticket_id}")
        self.on_silence()
        # Update button text
        # (AppState.sound_silenced toggled by on_silence callback)
        # We can't read AppState directly here, so just toggle the label
        current = self._sil_btn.cget("text")
        if "SILENCE" in current and "UN-" not in current:
            self._sil_btn.configure(text="🔊 UN-SILENCE\n[S]", bg="#006600")
        else:
            self._sil_btn.configure(text="🔇 SILENCE\n[S]", bg="#AA6600")

    def _do_next(self) -> None:
        if self.queue_count > 0:
            log.info("Next queued alert requested from AlertWindow")
            self._cleanup()
            self.on_next()

    def _do_details(self) -> None:
        """Show raw payload in a popup."""
        import json as _json
        raw = _json.dumps(self.alert.raw_payload, indent=2, ensure_ascii=False)
        _show_details_popup(self._win or self.root, self.alert, raw)

    def _do_test(self) -> None:
        self.on_test()

    def _on_close(self) -> None:
        """Window X button pressed – treat as acknowledge."""
        log.info("AlertWindow closed via X – treating as acknowledge")
        self._cleanup()
        self.on_acknowledge()

    # ------------------------------------------------------------------ lifecycle

    def _cleanup(self) -> None:
        self._destroyed = True
        if self._flash_job and self._win:
            try:
                self._win.after_cancel(self._flash_job)
            except Exception:
                pass
        self._flash_job = None
        if self._win:
            try:
                self._win.destroy()
            except Exception:
                pass
        self._win = None

    def destroy(self) -> None:
        """External call to forcefully close this window."""
        self._cleanup()

    def update_queue_count(self, count: int) -> None:
        self.queue_count = count
        if self._queue_lbl and not self._destroyed:
            try:
                self._queue_lbl.configure(text=self._queue_text())
                state = tk.NORMAL if count > 0 else tk.DISABLED
                self._next_btn.configure(state=state)
            except Exception:
                pass

    def _queue_text(self) -> str:
        if self.queue_count > 0:
            return f"⚠  {self.queue_count} more alert(s) waiting in queue"
        return "No other alerts queued"


# ---------------------------------------------------------------------------
# Details popup
# ---------------------------------------------------------------------------

def _show_details_popup(parent: tk.Widget, alert: Alert, raw_json: str) -> None:
    popup = tk.Toplevel(parent)
    popup.title(f"Alert Details – {alert.ticket_id}")
    popup.configure(bg="#1E1E2E")
    popup.attributes("-topmost", True)
    popup.geometry("700x500")

    tk.Label(
        popup,
        text=f"Alert: {alert.ticket_id}  |  {alert.client}",
        font=("Arial", 13, "bold"),
        fg="#CDD6F4",
        bg="#1E1E2E",
    ).pack(pady=(12, 4), padx=12, anchor="w")

    st = scrolledtext.ScrolledText(
        popup,
        font=("Consolas", 11),
        bg="#11111B",
        fg="#A6ADC8",
        wrap=tk.WORD,
        relief=tk.FLAT,
    )
    st.pack(expand=True, fill=tk.BOTH, padx=12, pady=8)
    st.insert(tk.END, raw_json)
    st.configure(state=tk.DISABLED)

    tk.Button(
        popup, text="Close",
        command=popup.destroy,
        bg="#313244", fg="#CDD6F4",
        font=("Arial", 11), relief=tk.FLAT,
    ).pack(pady=8)


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _recursive_bg(widget: tk.Widget, bg: str) -> None:
    """Set background on widget and all descendants (best-effort)."""
    try:
        widget.configure(bg=bg)
    except Exception:
        pass
    try:
        for child in widget.winfo_children():
            _recursive_bg(child, bg)
    except Exception:
        pass
