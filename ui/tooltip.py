"""Hover tooltip widget."""

import tkinter as tk

import constants


class Tooltip:
    """Show a small popup label when hovering over a widget."""

    def __init__(self, widget: tk.Widget, text: str):
        self._widget = widget
        self._text = text
        self._tip: tk.Toplevel | None = None
        widget.bind("<Enter>", self._show, add="+")
        widget.bind("<Leave>", self._hide, add="+")

    def _show(self, event=None):
        if self._tip:
            return
        self._tip = tk.Toplevel(self._widget)
        self._tip.wm_overrideredirect(True)
        lbl = tk.Label(self._tip, text=self._text, background=constants.TOOLTIP_BG,
                       relief="solid", borderwidth=1,
                       font=("TkDefaultFont", 8))
        lbl.pack()
        self._tip.update_idletasks()
        tip_h = self._tip.winfo_reqheight()
        x = event.x_root + 10 if event else self._widget.winfo_rootx() + 10
        y = (event.y_root if event else self._widget.winfo_rooty()) - tip_h - 4
        self._tip.wm_geometry(f"+{x}+{y}")

    def _hide(self, event=None):
        if self._tip:
            self._tip.destroy()
            self._tip = None
