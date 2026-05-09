import tkinter as tk
from tkinter import ttk, messagebox
from collections import Counter
from datetime import date, timedelta

from tkcalendar import DateEntry

import db
import positions_data as pd_


def _next_option_friday() -> date:
    """Return the default expiration Friday.
    Sun–Thu  → this coming Friday.
    Fri      → next week's Friday (skip today).
    Sat      → next week's Friday (this week's already passed).
    """
    today = date.today()
    wd = today.weekday()   # Mon=0 … Sun=6
    if wd == 4:            # Friday
        days = 7
    elif wd == 5:          # Saturday
        days = 6
    else:                  # Sun(6) through Thu(0-3)
        days = (4 - wd) % 7
    return today + timedelta(days=days)


# ---------------------------------------------------------------------------
# Tooltip helper
# ---------------------------------------------------------------------------

class _Tooltip:
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
        lbl = tk.Label(self._tip, text=self._text, background="#ffffe0",
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


# ---------------------------------------------------------------------------
# Add / Edit dialog
# ---------------------------------------------------------------------------

class PositionDialog(tk.Toplevel):
    """Modal dialog for adding or editing a position."""

    def __init__(self, parent, row=None):
        super().__init__(parent)
        self.title("Add Position" if row is None else "Edit Position")
        self.resizable(False, False)
        self.result = None
        self._row = row
        self._build(row)
        self.transient(parent)
        self.update_idletasks()
        x = parent.winfo_rootx() + 100
        y = parent.winfo_rooty() + 100
        self.geometry(f"+{x}+{y}")
        self.focus_force()
        self.grab_set()
        self._sym_entry.focus_set()
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
        self._sym.trace_add("write", lambda *_: (self._sym.set(self._sym.get().upper()), self._validate()))
        self._sym_entry = ttk.Entry(self, textvariable=self._sym, width=12)
        self._sym_entry.grid(row=1, column=1, padx=px, pady=py)

        # Type (row 2)
        lbl("Type:", 2)
        self._type = tk.StringVar(value=r.get("option_type", "PUT"))
        type_cb = ttk.Combobox(self, textvariable=self._type,
                               values=["CALL", "PUT", "STOCK"], width=12, state="readonly")
        type_cb.grid(row=2, column=1, padx=px, pady=py)
        type_cb.bind("<<ComboboxSelected>>", self._on_type_change)

        # Expiration (row 3)
        lbl("Expiration:", 3)
        existing_exp = r.get("expiration", "")
        if existing_exp and existing_exp != "9999-12-31":
            init_date = date.fromisoformat(existing_exp)
        else:
            init_date = _next_option_friday()
        self._date_entry = DateEntry(
            self, width=12, date_pattern="yyyy-mm-dd",
            year=init_date.year, month=init_date.month, day=init_date.day
        )
        self._date_entry.grid(row=3, column=1, padx=px, pady=py)
        self._date_entry.bind("<equal>", self._exp_plus_day)
        self._date_entry.bind("<minus>", self._exp_minus_day)

        # Strike (row 4)
        lbl("Strike:", 4)
        self._strike = tk.StringVar(value=str(r.get("strike", "")))
        self._strike.trace_add("write", lambda *_: self._validate())
        self._strike_entry = ttk.Entry(self, textvariable=self._strike, width=12)
        self._strike_entry.grid(row=4, column=1, padx=px, pady=py)

        # Quantity (row 5)
        self._qty_lbl = ttk.Label(self, text="Qty:")
        self._qty_lbl.grid(row=5, column=0, sticky=tk.W, padx=px, pady=py)
        self._qty = tk.StringVar(value=str(r.get("quantity", "")))
        self._qty.trace_add("write", lambda *_: self._validate())
        ttk.Entry(self, textvariable=self._qty, width=12).grid(row=5, column=1, padx=px, pady=py)

        # Long shares (row 6, STOCK only)
        self._lshares_lbl = ttk.Label(self, text="Long Shares:")
        self._lshares_lbl.grid(row=6, column=0, sticky=tk.W, padx=px, pady=py)
        self._lshares = tk.StringVar(value=str(r.get("long_shares") or ""))
        self._lshares.trace_add("write", lambda *_: self._validate())
        self._lshares_entry = ttk.Entry(self, textvariable=self._lshares, width=12)
        self._lshares_entry.grid(row=6, column=1, padx=px, pady=py)

        # Long cost (row 7, STOCK only)
        lbl("Long Cost ($/shr):", 7)
        self._lcost = tk.StringVar(value=str(r.get("long_cost") or ""))
        self._lcost.trace_add("write", lambda *_: self._validate())
        self._lcost_entry = ttk.Entry(self, textvariable=self._lcost, width=12)
        self._lcost_entry.grid(row=7, column=1, padx=px, pady=py)

        # Action buttons (row 8)
        btn_frame = ttk.Frame(self)
        btn_frame.grid(row=8, column=0, columnspan=2, pady=(6, 8))
        self._save_btn = ttk.Button(btn_frame, text="Save", command=self._save)
        self._save_btn.pack(side=tk.LEFT, padx=4)
        ttk.Button(btn_frame, text="Cancel", command=self.destroy).pack(side=tk.LEFT, padx=4)

        self._assigned_btn = ttk.Button(btn_frame, text="Assigned",
                                        command=self._assigned)
        self._clear_cover_btn = ttk.Button(btn_frame, text="Clear Cover",
                                           command=self._clear_cover)

        self._on_type_change()
        self.bind("<Return>", lambda _: self._save())

    def _exp_plus_day(self, _event=None):
        self._date_entry.set_date(self._date_entry.get_date() + timedelta(days=1))
        return "break"

    def _exp_minus_day(self, _event=None):
        self._date_entry.set_date(self._date_entry.get_date() - timedelta(days=1))
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
        state = "disabled" if error else "normal"
        self._save_btn.config(state=state)
        return error

    def _on_type_change(self, _event=None):
        is_stock = self._type.get() == "STOCK"
        is_put = self._type.get() == "PUT"
        stock_state = "normal" if is_stock else "disabled"
        self._lshares_entry.config(state=stock_state)
        self._lcost_entry.config(state=stock_state)
        if is_stock:
            self._qty_lbl.config(text="Contracts (0=no cover):")
            self._lshares_lbl.config(text="Long Shares (e.g. 500):")
        else:
            self._qty_lbl.config(text="Contracts:")
            self._lshares_lbl.config(text="Long Shares:")

        # Show "Assigned" only when editing a PUT
        editing = self._row is not None
        if editing and is_put:
            self._assigned_btn.pack(side=tk.LEFT, padx=4)
        else:
            self._assigned_btn.pack_forget()

        # Show "Clear Cover" only when editing a STOCK with cover (strike > 0)
        has_cover = is_stock and float(self._strike.get() or 0) > 0
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
        shares = qty * 100
        self.result = {
            "symbol": sym,
            "option_type": "STOCK",
            "strike": 0,
            "expiration": "9999-12-31",
            "quantity": 0,
            "long_shares": shares,
            "long_cost": strike,   # cost basis = put strike
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
            "expiration": "9999-12-31",
            "quantity": 0,
            "long_shares": long_shares,
            "long_cost": long_cost,
        }
        self.destroy()

    def _save(self):
        if self._validate():
            return  # shouldn't happen (button disabled), but guard anyway
        ot = self._type.get()
        sym = self._sym.get().strip().upper()
        strike = float(self._strike.get() or 0)
        qty = int(self._qty.get() or 0)
        exp = self._date_entry.get_date().isoformat() if ot != "STOCK" or float(self._strike.get() or 0) else "9999-12-31"
        long_shares = None
        long_cost = None
        if ot == "STOCK":
            ls = self._lshares.get().strip()
            lc = self._lcost.get().strip()
            long_shares = int(ls) if ls else None
            long_cost = float(lc) if lc else None

        self.result = {
            "symbol": sym,
            "option_type": ot,
            "strike": strike,
            "expiration": exp,
            "quantity": qty,
            "long_shares": long_shares,
            "long_cost": long_cost,
        }
        self.destroy()


# ---------------------------------------------------------------------------
# Main application
# ---------------------------------------------------------------------------

class MarginWatchApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("MarginWatch")
        self.geometry("460x800")
        self.resizable(False, False)

        db.init_db()
        self._load_config()

        # Price cache: symbol -> last price
        self._price_cache: dict = {}
        # Theta cache: (symbol, expiration, strike, option_type) -> theta
        self._theta_cache: dict = {}

        self._build_ui()
        self._refresh_positions()

    # ------------------------------------------------------------------
    # Config
    # ------------------------------------------------------------------

    def _load_config(self):
        with db.get_connection() as conn:
            rows = conn.execute("SELECT name, value FROM config").fetchall()
        self._config = {row["name"]: row["value"] for row in rows}

    def _save_config(self):
        try:
            margin = int(self._margin_var.get())
            multiplier = float(self._multiplier_var.get())
        except ValueError:
            messagebox.showerror("Invalid input", "Please enter valid numeric values.")
            return
        if not (0.5 <= multiplier <= 4.0):
            messagebox.showerror("Invalid multiplier",
                                 "Multiplier must be between 0.5 and 4.0.")
            return
        with db.get_connection() as conn:
            conn.execute("INSERT OR REPLACE INTO config (name, value) VALUES (?, ?)",
                         ("MaximumMarginBasis", str(margin)))
            conn.execute("INSERT OR REPLACE INTO config (name, value) VALUES (?, ?)",
                         ("MarginMultiplier", str(multiplier)))
            conn.commit()
        self._config["MaximumMarginBasis"] = str(margin)
        self._config["MarginMultiplier"] = str(multiplier)

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self):
        # ── Positions panel ──────────────────────────────────────────
        pos_outer = ttk.Frame(self)
        pos_outer.pack(fill=tk.BOTH, expand=True, padx=4, pady=(4, 0))

        # Top bar: [Total/Avail] [sort radios] [+ button]
        top_bar = ttk.Frame(pos_outer)
        top_bar.pack(fill=tk.X, padx=4, pady=(0, 2))

        # Left: Total / Avail stacked
        info_frame = ttk.Frame(top_bar)
        info_frame.pack(side=tk.LEFT)
        ttk.Label(info_frame, text="Total:", anchor=tk.W).grid(row=0, column=0, sticky=tk.W)
        self._total_lbl = ttk.Label(info_frame, text="$0.0k", anchor=tk.E, width=8)
        self._total_lbl.grid(row=0, column=1, sticky=tk.E)
        ttk.Label(info_frame, text="Avail:", anchor=tk.W).grid(row=1, column=0, sticky=tk.W)
        self._avail_lbl = ttk.Label(info_frame, text="$0.0k", anchor=tk.E, width=8)
        self._avail_lbl.grid(row=1, column=1, sticky=tk.E)
        ttk.Label(info_frame, text="Theta:", anchor=tk.W).grid(row=2, column=0, sticky=tk.W)
        self._theta_lbl = ttk.Label(info_frame, text="$0/day", anchor=tk.E, width=8)
        self._theta_lbl.grid(row=2, column=1, sticky=tk.E)

        # Middle: sort radio buttons
        sort_frame = ttk.Frame(top_bar)
        sort_frame.pack(side=tk.LEFT, padx=(20, 0))
        self._sort_var = tk.StringVar(value="alpha")
        ttk.Radiobutton(sort_frame, text="A-Z", variable=self._sort_var,
                        value="alpha", command=self._refresh_positions).pack(anchor=tk.W)
        ttk.Radiobutton(sort_frame, text="Exp", variable=self._sort_var,
                        value="expiry", command=self._refresh_positions).pack(anchor=tk.W)

        # Right: + button centered in its own frame
        plus_frame = ttk.Frame(top_bar)
        plus_frame.pack(side=tk.RIGHT, padx=6)
        ttk.Button(plus_frame, text="+", width=3,
                   command=self._add_position).pack(expand=True, pady=4)

        # Canvas + scrollbar for rows
        self._rows_canvas = tk.Canvas(pos_outer, borderwidth=0, highlightthickness=0)
        vscroll = ttk.Scrollbar(pos_outer, orient=tk.VERTICAL,
                                command=self._rows_canvas.yview)
        self._rows_canvas.configure(yscrollcommand=vscroll.set)
        vscroll.pack(side=tk.RIGHT, fill=tk.Y)
        self._rows_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        self._rows_frame = ttk.Frame(self._rows_canvas)
        self._rows_frame_id = self._rows_canvas.create_window(
            (0, 0), window=self._rows_frame, anchor="nw"
        )
        self._rows_frame.bind("<Configure>", self._on_frame_configure)
        self._rows_canvas.bind("<Configure>", self._on_canvas_configure)

        # Column headers
        hdr = ttk.Frame(self._rows_frame)
        hdr.pack(fill=tk.X)
        for text, w in [("Position", 125), ("#", 28), ("Margin", 58),
                        ("$/shr", 52), ("Theta", 42), ("", 44)]:
            ttk.Label(hdr, text=text, width=w // 7, relief="groove",
                      anchor=tk.CENTER).pack(side=tk.LEFT)

        self._row_widgets: list[tk.Frame] = []

        # ── Config panel ─────────────────────────────────────────────
        config_frame = ttk.LabelFrame(self, text="Configuration")
        config_frame.pack(fill=tk.X, padx=4, pady=(4, 6))

        ttk.Label(config_frame, text="Max Margin ($1k increments):").grid(
            row=0, column=0, sticky=tk.W, padx=6, pady=(6, 4))
        initial_margin = int(self._config.get("MaximumMarginBasis", "250000"))
        self._margin_var = tk.StringVar(value=str(initial_margin))
        ttk.Spinbox(config_frame, from_=0, to=10_000_000, increment=1000,
                    textvariable=self._margin_var, width=10).grid(
            row=0, column=1, padx=6, pady=(6, 4), sticky=tk.W)

        ttk.Label(config_frame, text="Margin Multiplier (0.5 – 4.0):").grid(
            row=1, column=0, sticky=tk.W, padx=6, pady=(2, 4))
        initial_mult = float(self._config.get("MarginMultiplier", "1.5"))
        self._multiplier_var = tk.StringVar(value=f"{initial_mult:.1f}")
        ttk.Spinbox(config_frame, from_=0.5, to=4.0, increment=0.1,
                    textvariable=self._multiplier_var, width=6,
                    format="%.1f").grid(row=1, column=1, padx=6, pady=(2, 4), sticky=tk.W)

        ttk.Button(config_frame, text="Save",
                   command=self._save_config).grid(row=2, column=0, columnspan=2, padx=6,
                                                   pady=(0, 8), sticky=tk.E)

    def _on_frame_configure(self, _event=None):
        self._rows_canvas.configure(
            scrollregion=self._rows_canvas.bbox("all"))

    def _on_canvas_configure(self, event):
        self._rows_canvas.itemconfig(self._rows_frame_id, width=event.width)

    # ------------------------------------------------------------------
    # Positions
    # ------------------------------------------------------------------

    def _fetch_prices(self, rows):
        symbols = {r["symbol"] for r in rows}
        for sym in symbols:
            if sym not in self._price_cache:
                self._price_cache[sym] = pd_.fetch_last_price(sym)

    def _fetch_theta(self, rows):
        for r in rows:
            # Skip STOCK rows with no covered call written
            if r["option_type"] == "STOCK" and not r["strike"]:
                continue
            ot = "CALL" if r["option_type"] == "STOCK" else r["option_type"]
            key = (r["symbol"], r["expiration"], r["strike"], ot)
            if key not in self._theta_cache:
                self._theta_cache[key] = pd_.fetch_option_theta(
                    r["symbol"], r["expiration"], r["strike"], ot)

    def _refresh_positions(self):
        with db.get_connection() as conn:
            pd_.cleanup_expired(conn)
            rows = conn.execute(
                "SELECT * FROM positions WHERE status='OPEN'"
            ).fetchall()

        # Sort
        if self._sort_var.get() == "alpha":
            rows = sorted(rows, key=lambda r: r["symbol"])
        else:
            rows = sorted(rows, key=lambda r: (r["expiration"], r["symbol"]))

        self._fetch_prices(rows)
        self._fetch_theta(rows)

        # Clear existing row widgets (skip header frame at index 0)
        for w in self._row_widgets:
            w.destroy()
        self._row_widgets.clear()

        total_margin = 0.0
        total_theta_day = 0.0  # daily $ theta (per contract = theta * 100)

        # Find symbols with 2+ OPEN STOCK rows
        stock_symbol_counts = Counter(
            r["symbol"] for r in rows if r["option_type"] == "STOCK"
        )
        mergeable_symbols: set[str] = {
            sym for sym, cnt in stock_symbol_counts.items() if cnt >= 2
        }
        seen_stock_symbols: set[str] = set()

        for row in rows:
            abbrev = pd_.position_abbrev(row)
            qty = pd_.display_quantity(row)
            margin = pd_.margin_k(row)
            total_margin += margin
            days = pd_.days_to_expiry(row)
            bg = pd_.expiry_color(days)
            price = self._price_cache.get(row["symbol"])
            itm = pd_.is_itm(row, price)

            # Fetch option last price and theta for display
            opt_price = None
            theta = None
            ot = "CALL" if row["option_type"] == "STOCK" else row["option_type"]
            if row["strike"]:
                opt_price = pd_.fetch_option_last_price(
                    row["symbol"], row["expiration"],
                    row["strike"], ot)
                key = (row["symbol"], row["expiration"], row["strike"], ot)
                theta = self._theta_cache.get(key)
            opt_str = f"{opt_price:.2f}" if opt_price is not None else "—"
            # Theta per day in dollars for this position (theta is per share, * 100 * contracts)
            # Negate because we are short these contracts (positive quantity = short)
            theta_dollars = None
            if theta is not None:
                theta_dollars = -theta * 100 * row["quantity"]
                total_theta_day += theta_dollars
            theta_str = f"${round(theta_dollars):d}" if theta_dollars is not None else "—"

            row_frame = tk.Frame(self._rows_frame, bg=bg)
            row_frame.pack(fill=tk.X, pady=1)
            self._row_widgets.append(row_frame)

            # Tooltip with current stock price
            if price is not None:
                tip_text = f"{row['symbol']} last: ${price:.2f}"
                _Tooltip(row_frame, tip_text)

            fg = pd_.text_color(bg)

            # ITM indicator: small yellow rectangle canvas
            itm_canvas = tk.Canvas(row_frame, width=6, height=16,
                                   bg=bg, highlightthickness=0)
            if itm:
                #  fill="#ffd700" old color
                itm_canvas.create_rectangle(1, 2, 5, 14, fill="#8A2BE2", outline="")
            itm_canvas.pack(side=tk.LEFT)

            # Profit indicator: green bar when stock price > cost basis
            profit_canvas = tk.Canvas(row_frame, width=6, height=16,
                                      bg=bg, highlightthickness=0)
            is_profitable = (
                row["option_type"] == "STOCK"
                and price is not None
                and (row["long_cost"] or 0.0) > 0
                and price > row["long_cost"]
            )
            if is_profitable:
                profit_canvas.create_rectangle(1, 2, 5, 14, fill="#d6109b", outline="")
            profit_canvas.pack(side=tk.LEFT)

            is_stock_row = row["option_type"] == "STOCK"
            pos_font = ("TkDefaultFont", 8, "underline") if is_stock_row else ("TkDefaultFont", 8)
            tk.Label(row_frame, text=abbrev, bg=bg, fg=fg, anchor=tk.W,
                     width=17, font=pos_font).pack(side=tk.LEFT)
            tk.Label(row_frame, text=str(qty), bg=bg, fg=fg, width=5,
                     anchor=tk.CENTER).pack(side=tk.LEFT)
            tk.Label(row_frame, text=f"{margin:.1f}", bg=bg, fg=fg, width=7,
                     anchor=tk.E).pack(side=tk.LEFT)
            tk.Label(row_frame, text=opt_str, bg=bg, fg=fg, width=6,
                     anchor=tk.E).pack(side=tk.LEFT)
            tk.Label(row_frame, text=theta_str, bg=bg, fg=fg, width=6,
                     anchor=tk.E).pack(side=tk.LEFT)

            row_id = row["id"]
            sym = row["symbol"]
            btn_frame = tk.Frame(row_frame, bg=bg)
            btn_frame.pack(side=tk.LEFT, padx=2)
            tk.Button(btn_frame, text="✎", width=1, pady=0, font=("TkDefaultFont", 7),
                      command=lambda rid=row_id: self._edit_position(rid)
                      ).pack(side=tk.LEFT)
            tk.Button(btn_frame, text="✕", width=1, pady=0, font=("TkDefaultFont", 7),
                      command=lambda rid=row_id: self._delete_position(rid)
                      ).pack(side=tk.LEFT)
            # Merge button on first occurrence of a STOCK symbol with duplicates
            if row["option_type"] == "STOCK" and sym in mergeable_symbols:
                if sym not in seen_stock_symbols:
                    tk.Button(btn_frame, text="⊕", width=1, pady=0,
                              font=("TkDefaultFont", 7),
                              command=lambda s=sym: self._merge_stock(s)
                              ).pack(side=tk.LEFT)
                seen_stock_symbols.add(sym)

        self._total_lbl.config(text=f"${total_margin:.1f}k")
        self._theta_lbl.config(text=f"${round(total_theta_day):d}/d")
        max_margin = float(self._config.get("MaximumMarginBasis", "250000"))
        multiplier = float(self._config.get("MarginMultiplier", "1.5"))
        avail = (max_margin / 1000) * multiplier - total_margin
        self._avail_lbl.config(
            text=f"${avail:.1f}k",
            foreground="red" if avail < 0 else "",
            font=("TkDefaultFont", 9, "bold") if avail < 0 else ("TkDefaultFont", 9),
        )

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    def _add_position(self):
        dlg = PositionDialog(self)
        if dlg.result:
            d = dlg.result
            today = date.today().isoformat()
            with db.get_connection() as conn:
                conn.execute(
                    "INSERT INTO positions"
                    " (symbol, option_type, strike, expiration, quantity,"
                    "  open_date, long_shares, long_cost)"
                    " VALUES (?,?,?,?,?,?,?,?)",
                    (d["symbol"], d["option_type"], d["strike"], d["expiration"],
                     d["quantity"], today,
                     d["long_shares"], d["long_cost"]),
                )
                conn.commit()
            self._price_cache.pop(d["symbol"], None)
            self._theta_cache = {k: v for k, v in self._theta_cache.items() if k[0] != d["symbol"]}
            self._refresh_positions()

    def _edit_position(self, row_id: int):
        with db.get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM positions WHERE id=?", (row_id,)
            ).fetchone()
        if not row:
            return
        dlg = PositionDialog(self, row=dict(row))
        if dlg.result:
            d = dlg.result
            with db.get_connection() as conn:
                conn.execute(
                    "UPDATE positions SET symbol=?, option_type=?, strike=?,"
                    " expiration=?, quantity=?, long_shares=?, long_cost=?"
                    " WHERE id=?",
                    (d["symbol"], d["option_type"], d["strike"], d["expiration"],
                     d["quantity"], d["long_shares"], d["long_cost"],
                     row_id),
                )
                conn.commit()
            self._price_cache.pop(d["symbol"], None)
            self._theta_cache = {k: v for k, v in self._theta_cache.items() if k[0] != d["symbol"]}
            self._refresh_positions()

    def _merge_stock(self, symbol: str):
        """Merge all OPEN STOCK rows for symbol into one, weighted-avg cost basis."""
        if not messagebox.askyesno("Merge Positions",
                                   f"Merge all {symbol} STOCK positions into one?"):
            return
        with db.get_connection() as conn:
            stock_rows = conn.execute(
                "SELECT id, long_shares, long_cost FROM positions"
                " WHERE status='OPEN' AND option_type='STOCK' AND symbol=?",
                (symbol,)
            ).fetchall()
            if len(stock_rows) < 2:
                return
            total_shares = sum(r["long_shares"] or 0 for r in stock_rows)
            total_cost = sum((r["long_shares"] or 0) * (r["long_cost"] or 0.0)
                             for r in stock_rows)
            avg_cost = total_cost / total_shares if total_shares else 0.0
            keep_id = stock_rows[0]["id"]
            drop_ids = [r["id"] for r in stock_rows[1:]]
            conn.execute(
                "UPDATE positions SET long_shares=?, long_cost=? WHERE id=?",
                (total_shares, avg_cost, keep_id)
            )
            conn.executemany(
                "DELETE FROM positions WHERE id=?",
                [(rid,) for rid in drop_ids]
            )
            conn.commit()
        self._refresh_positions()

    def _delete_position(self, row_id: int):
        if not messagebox.askyesno("Delete", "Delete this position?"):
            return
        with db.get_connection() as conn:
            conn.execute("DELETE FROM positions WHERE id=?", (row_id,))
            conn.commit()
        self._refresh_positions()


if __name__ == "__main__":
    app = MarginWatchApp()
    app.mainloop()
