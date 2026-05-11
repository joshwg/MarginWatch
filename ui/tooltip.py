"""Hover tooltip widget."""

import tkinter as tk

import constants

_HOVER_DELAY_MS = 2000   # ms to wait before showing
_MOVE_THRESHOLD = 8      # pixels of movement that resets the timer


class Tooltip:
    """Show a small popup label after hovering still over a widget for 3 seconds."""

    def __init__(self, widget: tk.Widget, text: str):
        self._widget = widget
        self._text = text
        self._tip: tk.Toplevel | None = None
        self._after_id = None
        self._anchor_x = 0
        self._anchor_y = 0
        widget.bind("<Enter>", self._on_enter, add="+")
        widget.bind("<Motion>", self._on_motion, add="+")
        widget.bind("<Leave>", self._hide, add="+")

    def _on_enter(self, event):
        self._anchor_x = event.x_root
        self._anchor_y = event.y_root
        self._schedule(event.x_root, event.y_root)

    def _on_motion(self, event):
        dx = abs(event.x_root - self._anchor_x)
        dy = abs(event.y_root - self._anchor_y)
        if dx > _MOVE_THRESHOLD or dy > _MOVE_THRESHOLD:
            self._cancel()
            self._anchor_x = event.x_root
            self._anchor_y = event.y_root
            self._schedule(event.x_root, event.y_root)

    def _schedule(self, x_root, y_root):
        self._cancel()
        self._after_id = self._widget.after(
            _HOVER_DELAY_MS, lambda: self._show(x_root, y_root)
        )

    def _cancel(self):
        if self._after_id is not None:
            self._widget.after_cancel(self._after_id)
            self._after_id = None

    def _show(self, x_root, y_root):
        if self._tip:
            return
        self._tip = tk.Toplevel(self._widget)
        self._tip.wm_overrideredirect(True)
        self._tip.wm_withdraw()
        lbl = tk.Label(self._tip, text=self._text, background=constants.TOOLTIP_BG,
                       relief="solid", borderwidth=1,
                       font=("TkDefaultFont", 8))
        lbl.pack()
        self._tip.update_idletasks()
        tip_h = self._tip.winfo_reqheight()
        x = x_root + 10
        y = y_root - tip_h - 4
        self._tip.wm_geometry(f"+{x}+{y}")
        self._tip.wm_deiconify()

    def _hide(self, event=None):
        self._cancel()
        if self._tip:
            self._tip.destroy()
            self._tip = None
