"""Add / Edit position dialog."""

import re
import tkinter as tk
from tkinter import ttk, messagebox
from datetime import date, timedelta

from tkcalendar import Calendar

import constants
import utils


class PositionDialog(tk.Toplevel):
    """Modal dialog for adding or editing a position."""

    def __init__(self, parent, row=None):
        super().__init__(parent)
        self.title("Add Position" if row is None else "Edit Position")
        self.resizable(True, True)
        self.result = None
        self._row = row
        self._build(row)
        self.transient(parent)
        self.update()
        w = max(self.winfo_reqwidth(), 320)
        h = self.winfo_reqheight()
        self.minsize(w, h)
        x = parent.winfo_rootx() + 100
        y = parent.winfo_rooty() + 100
        self.geometry(f"{w}x{h}+{x}+{y}")
        self.focus_force()
        self._sym_entry.focus_set()
        self.grab_set()
        self.wait_window(self)

    def _build(self, row):
        px, py = 6, 3
        r = row or {}

        def lbl(text, gr):
            ttk.Label(self, text=text).grid(row=gr, column=0, sticky=tk.W, padx=px, pady=py)

        # Error label (row 0)
        self._err_lbl = ttk.Label(self, text="", foreground="red", wraplength=220)
        self._err_lbl.grid(row=0, column=0, columnspan=2, sticky=tk.W, padx=px, pady=(4, 0))

        # Symbol (row 1)
        lbl("Symbol:", 1)
        self._sym = tk.StringVar(value=r.get("symbol", ""))
        self._sym.trace_add("write", lambda *_: (
            self._sym.set(re.sub(r'[^A-Z0-9.\-]', '', self._sym.get().upper())[:8]),
            self._validate()))
        self._sym_entry = ttk.Entry(self, textvariable=self._sym, width=12)
        self._sym_entry.grid(row=1, column=1, padx=px, pady=py)

        # Type (row 2)
        lbl("Type:", 2)
        self._type = tk.StringVar(value=r.get("option_type", "PUT"))
        type_cb = ttk.Combobox(self, textvariable=self._type,
                               values=["CALL", "PUT", "CALL_SPREAD", "PUT_SPREAD", "STOCK"],
                               width=14, state="readonly")
        type_cb.grid(row=2, column=1, padx=px, pady=py)
        type_cb.bind("<<ComboboxSelected>>", self._on_type_change)

        # Expiration (row 3)
        lbl("Expiration:", 3)
        existing_exp = r.get("expiration", "")
        if existing_exp and existing_exp != constants.NO_EXPIRATION:
            init_date = date.fromisoformat(existing_exp)
        else:
            init_date = utils.next_option_friday()
        self._exp_var = tk.StringVar(value=init_date.isoformat())
        exp_frame = ttk.Frame(self)
        exp_frame.grid(row=3, column=1, padx=px, pady=py)
        exp_entry = ttk.Entry(exp_frame, textvariable=self._exp_var, width=10)
        exp_entry.pack(side=tk.LEFT)
        ttk.Button(exp_frame, text="…", width=2,
                   command=self._pick_date).pack(side=tk.LEFT, padx=(2, 0))
        exp_entry.bind("<equal>", self._exp_plus_day)
        exp_entry.bind("<minus>", self._exp_minus_day)

        # Strike (row 4)
        lbl("Strike (short):", 4)
        self._strike = tk.StringVar(value=str(r.get("strike", "")))
        self._strike.trace_add("write", lambda *_: self._validate())
        self._strike_entry = ttk.Entry(self, textvariable=self._strike, width=12)
        self._strike_entry.grid(row=4, column=1, padx=px, pady=py)

        # Long strike (row 5, CALL/PUT spread only)
        self._lstrike_lbl = ttk.Label(self, text="Long Strike:")
        self._lstrike_lbl.grid(row=5, column=0, sticky=tk.W, padx=px, pady=py)
        raw_ls = r.get("strike2")
        self._lstrike = tk.StringVar(value=str(raw_ls) if raw_ls else "")
        self._lstrike.trace_add("write", lambda *_: self._validate())
        self._lstrike_entry = ttk.Entry(self, textvariable=self._lstrike, width=12)
        self._lstrike_entry.grid(row=5, column=1, padx=px, pady=py)

        # Quantity (row 6)
        self._qty_lbl = ttk.Label(self, text="Qty:")
        self._qty_lbl.grid(row=6, column=0, sticky=tk.W, padx=px, pady=py)
        self._qty = tk.StringVar(value=str(r.get("quantity", "")))
        self._qty.trace_add("write", lambda *_: self._validate())
        ttk.Entry(self, textvariable=self._qty, width=12).grid(row=6, column=1, padx=px, pady=py)

        # Long shares (row 7, STOCK only)
        self._lshares_lbl = ttk.Label(self, text="Long Shares:")
        self._lshares_lbl.grid(row=7, column=0, sticky=tk.W, padx=px, pady=py)
        self._lshares = tk.StringVar(value=str(r.get("long_shares") or ""))
        self._lshares.trace_add("write", lambda *_: self._validate())
        self._lshares_entry = ttk.Entry(self, textvariable=self._lshares, width=12)
        self._lshares_entry.grid(row=7, column=1, padx=px, pady=py)

        # Long cost (row 8, STOCK only)
        lbl("Long Cost ($/shr):", 8)
        self._lcost = tk.StringVar(value=str(r.get("long_cost") or ""))
        self._lcost.trace_add("write", lambda *_: self._validate())
        self._lcost_entry = ttk.Entry(self, textvariable=self._lcost, width=12)
        self._lcost_entry.grid(row=8, column=1, padx=px, pady=py)

        # Action buttons (row 9)
        btn_frame = ttk.Frame(self)
        btn_frame.grid(row=9, column=0, columnspan=2, pady=(6, 8))
        self._save_btn = ttk.Button(btn_frame, text="Save", command=self._save)
        self._save_btn.pack(side=tk.LEFT, padx=4)
        ttk.Button(btn_frame, text="Cancel", command=self.destroy).pack(side=tk.LEFT, padx=4)

        self._assigned_btn = ttk.Button(btn_frame, text="Assigned",
                                        command=self._assigned)
        self._clear_cover_btn = ttk.Button(btn_frame, text="Clear Cover",
                                           command=self._clear_cover)

        self._on_type_change()
        self.bind("<Return>", lambda _: self._save())

    def _pick_date(self):
        try:
            init = date.fromisoformat(self._exp_var.get())
        except ValueError:
            init = utils.next_option_friday()
        self.grab_release()
        dlg = tk.Toplevel(self)
        dlg.title("Pick date")
        dlg.resizable(False, False)
        dlg.transient(self)
        cal = Calendar(dlg, selectmode="day", date_pattern="yyyy-mm-dd",
                       year=init.year, month=init.month, day=init.day)
        cal.pack(padx=6, pady=6)
        ttk.Button(dlg, text="OK",
                   command=lambda: [self._exp_var.set(cal.get_date()), dlg.destroy()]
                   ).pack(pady=(0, 8))
        dlg.update_idletasks()
        x = self.winfo_rootx() + (self.winfo_width()  - dlg.winfo_reqwidth())  // 2
        y = self.winfo_rooty() + (self.winfo_height() - dlg.winfo_reqheight()) // 2
        dlg.geometry(f"+{x}+{y}")
        dlg.focus_force()
        dlg.grab_set()
        dlg.wait_window()
        self.grab_set()

    def _exp_plus_day(self, _event=None):
        try:
            self._exp_var.set((date.fromisoformat(self._exp_var.get()) + timedelta(days=1)).isoformat())
        except ValueError:
            pass
        return "break"

    def _exp_minus_day(self, _event=None):
        try:
            self._exp_var.set((date.fromisoformat(self._exp_var.get()) - timedelta(days=1)).isoformat())
        except ValueError:
            pass
        return "break"

    def _validate(self) -> str:
        """Return the first error string, or '' if valid. Also updates UI."""
        ot = self._type.get()
        is_stock = ot == "STOCK"
        error = ""

        if not self._sym.get().strip():
            error = "Symbol is required."
        elif not is_stock:
            s = self._strike.get().strip()
            if not s:
                error = "Strike is required."
            else:
                try:
                    float(s)
                except ValueError:
                    error = "Strike must be a number."
            if not error:
                is_spread = ot in ("CALL_SPREAD", "PUT_SPREAD")
                ls = self._lstrike.get().strip()
                if is_spread and not ls:
                    error = "Long strike is required for a spread."
                elif ls:
                    try:
                        lsv = float(ls)
                        if lsv == float(s):
                            error = "Long strike must differ from short strike."
                    except ValueError:
                        error = "Long strike must be a number."
            if not error:
                q = self._qty.get().strip()
                if not q:
                    error = "Contracts is required."
                else:
                    try:
                        int(q)
                    except ValueError:
                        error = "Contracts must be a whole number."
        else:
            # STOCK: long_shares and long_cost are required and must be > 0
            ls = self._lshares.get().strip()
            if not ls:
                error = "Long Shares is required for STOCK."
            else:
                try:
                    if int(ls) <= 0:
                        error = "Long Shares must be greater than zero."
                except ValueError:
                    error = "Long Shares must be a whole number."
            if not error:
                lc = self._lcost.get().strip()
                if not lc:
                    error = "Long Cost is required for STOCK."
                else:
                    try:
                        if float(lc) <= 0:
                            error = "Long Cost must be greater than zero."
                    except ValueError:
                        error = "Long Cost must be a number."
            if not error:
                s = self._strike.get().strip()
                if s:
                    try:
                        float(s)
                    except ValueError:
                        error = "Strike must be a number."
            if not error:
                q = self._qty.get().strip()
                if q:
                    try:
                        int(q)
                    except ValueError:
                        error = "Contracts must be a whole number."

        self._err_lbl.config(text=error)
        self._save_btn.config(state="disabled" if error else "normal")
        return error

    def _on_type_change(self, _event=None):
        ot = self._type.get()
        is_stock = ot == "STOCK"
        is_put = ot == "PUT"
        is_spread = ot in ("CALL_SPREAD", "PUT_SPREAD")
        stock_state = "normal" if is_stock else "disabled"
        spread_state = "normal" if is_spread else "disabled"
        self._lshares_entry.config(state=stock_state)
        self._lcost_entry.config(state=stock_state)
        self._lstrike_entry.config(state=spread_state)
        if is_stock:
            self._qty_lbl.config(text="Contracts (0=no cover):")
            self._lshares_lbl.config(text="Long Shares (e.g. 500):")
        else:
            self._qty_lbl.config(text="Contracts:")
            self._lshares_lbl.config(text="Long Shares:")
        if not is_spread:
            self._lstrike.set("")

        # Show "Assigned" only when editing a PUT
        editing = self._row is not None
        if editing and is_put:
            self._assigned_btn.pack(side=tk.LEFT, padx=4)
        else:
            self._assigned_btn.pack_forget()

        # Show "Clear Cover" only when editing a STOCK with cover (strike > 0)
        has_cover = is_stock and utils.parse_float(self._strike.get(), 0.0) > 0
        if editing and has_cover:
            self._clear_cover_btn.pack(side=tk.LEFT, padx=4)
        else:
            self._clear_cover_btn.pack_forget()

        self._validate()

    def _assigned(self):
        """Convert a naked PUT to a STOCK (long stock, no cover) position."""
        sym = self._sym.get().strip().upper()
        if not sym:
            messagebox.showerror("Error", "Symbol is required.", parent=self)
            return
        try:
            qty = int(self._qty.get() or 0)
            strike = float(self._strike.get() or 0)
        except ValueError:
            messagebox.showerror("Error", "Invalid numeric value.", parent=self)
            return
        self.result = {
            "symbol": sym,
            "option_type": "STOCK",
            "strike": 0,
            "expiration": constants.NO_EXPIRATION,
            "quantity": 0,
            "long_shares": qty * 100,
            "long_cost": strike,
            "strike2": None,
        }
        self.destroy()

    def _clear_cover(self):
        """Remove the covered call from a STOCK position (strike → 0, qty → 0)."""
        sym = self._sym.get().strip().upper()
        if not sym:
            messagebox.showerror("Error", "Symbol is required.", parent=self)
            return
        try:
            ls = self._lshares.get().strip()
            lc = self._lcost.get().strip()
            long_shares = int(ls) if ls else None
            long_cost = float(lc) if lc else None
        except ValueError:
            messagebox.showerror("Error", "Invalid long shares/cost.", parent=self)
            return
        self.result = {
            "symbol": sym,
            "option_type": "STOCK",
            "strike": 0,
            "expiration": constants.NO_EXPIRATION,
            "quantity": 0,
            "long_shares": long_shares,
            "long_cost": long_cost,
            "strike2": None,
        }
        self.destroy()

    def _save(self):
        if self._validate():
            return  # shouldn't happen (button disabled), but guard anyway
        ot = self._type.get()
        sym = self._sym.get().strip().upper()
        strike = utils.parse_float(self._strike.get(), 0.0)
        qty = utils.parse_int(self._qty.get(), 0)
        exp = (self._exp_var.get()
               if ot != "STOCK" or utils.parse_float(self._strike.get(), 0.0)
               else constants.NO_EXPIRATION)
        long_shares = None
        long_cost = None
        if ot == "STOCK":
            ls = self._lshares.get().strip()
            lc = self._lcost.get().strip()
            long_shares = utils.parse_int(ls, None)
            long_cost = utils.parse_float(lc, None)

        ls_str = self._lstrike.get().strip()
        strike2 = utils.parse_float(ls_str, None) if ls_str else None
        # Straddle: put strike defaults to call strike if not specified
        if ot == "STRADDLE" and not strike2:
            strike2 = strike

        self.result = {
            "symbol": sym,
            "option_type": ot,
            "strike": strike,
            "expiration": exp,
            "quantity": qty,
            "long_shares": long_shares,
            "long_cost": long_cost,
            "strike2": strike2,
        }
        self.destroy()
