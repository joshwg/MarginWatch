"""Main application window."""

from __future__ import annotations

import dataclasses
import tkinter as tk
from tkinter import ttk, messagebox
from collections import Counter

import db
import constants
import utils
import repositories.positions_repository as pos_repo
import repositories.config_repository as cfg_repo
from services.cache_service import CacheService
import services.position_service as ps
from ui.position_dialog import PositionDialog
from ui.position_row import compute_display, build_row


class MarginWatchApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("MarginWatch")
        self.geometry("460x800")
        self.resizable(False, False)

        db.init_db()
        self._config = cfg_repo.load()
        self._cache = CacheService()

        self._build_ui()
        self._refresh_positions()

    # ------------------------------------------------------------------
    # Config
    # ------------------------------------------------------------------

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
        cfg_repo.save(margin, multiplier)
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

        # Left: Total / Avail / Theta stacked
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
        initial_sort = self._config.get("SortOrder", "alpha")
        self._sort_var = tk.StringVar(value=initial_sort)
        ttk.Radiobutton(sort_frame, text="A-Z", variable=self._sort_var,
                        value="alpha", command=self._on_sort_change).pack(anchor=tk.W)
        ttk.Radiobutton(sort_frame, text="Exp", variable=self._sort_var,
                        value="expiry", command=self._on_sort_change).pack(anchor=tk.W)

        # Right: + button
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
        initial_margin = utils.parse_int(
            self._config.get("MaximumMarginBasis", "250000"), 250000)
        self._margin_var = tk.StringVar(value=str(initial_margin))
        ttk.Spinbox(config_frame, from_=0, to=10_000_000, increment=1000,
                    textvariable=self._margin_var, width=10).grid(
            row=0, column=1, padx=6, pady=(6, 4), sticky=tk.W)

        ttk.Label(config_frame, text="Margin Multiplier (0.5 – 4.0):").grid(
            row=1, column=0, sticky=tk.W, padx=6, pady=(2, 4))
        initial_mult = utils.parse_float(
            self._config.get("MarginMultiplier", "1.5"), 1.5)
        self._multiplier_var = tk.StringVar(value=f"{initial_mult:.1f}")
        ttk.Spinbox(config_frame, from_=0.5, to=4.0, increment=0.1,
                    textvariable=self._multiplier_var, width=6,
                    format="%.1f").grid(row=1, column=1, padx=6, pady=(2, 4), sticky=tk.W)

        ttk.Button(config_frame, text="Save",
                   command=self._save_config).grid(
            row=2, column=0, columnspan=2, padx=6, pady=(0, 8), sticky=tk.E)

    def _on_frame_configure(self, _event=None):
        self._rows_canvas.configure(scrollregion=self._rows_canvas.bbox("all"))

    def _on_canvas_configure(self, event):
        self._rows_canvas.itemconfig(self._rows_frame_id, width=event.width)

    # ------------------------------------------------------------------
    # Positions refresh
    # ------------------------------------------------------------------

    def _on_sort_change(self):
        cfg_repo.save_sort(self._sort_var.get())
        self._refresh_positions()

    def _load_sorted_positions(self) -> list:
        rows = pos_repo.get_open_positions()
        if self._sort_var.get() == "alpha":
            return sorted(rows, key=lambda r: r.symbol)
        return sorted(rows, key=lambda r: (r.expiration, r.symbol))

    def _update_summary(self, total_margin: float, total_theta_day: float):
        self._total_lbl.config(text=f"${total_margin:.1f}k")
        self._theta_lbl.config(text=f"${round(total_theta_day):d}/d")
        max_margin = utils.parse_float(
            self._config.get("MaximumMarginBasis", "250000"), 250000.0)
        multiplier = utils.parse_float(
            self._config.get("MarginMultiplier", "1.5"), 1.5)
        avail = (max_margin / 1000) * multiplier - total_margin
        self._avail_lbl.config(
            text=f"${avail:.1f}k",
            foreground="red" if avail < 0 else "",
            font=("TkDefaultFont", 9, "bold") if avail < 0 else ("TkDefaultFont", 9),
        )

    def _refresh_positions(self):
        positions = self._load_sorted_positions()
        self._cache.fetch_all(positions)

        for w in self._row_widgets:
            w.destroy()
        self._row_widgets.clear()

        mergeable_symbols: set[str] = {
            sym for sym, cnt in Counter(
                p.symbol for p in positions if ps.is_stock(p)
            ).items() if cnt >= 2
        }
        seen_stock_symbols: set[str] = set()
        total_margin = 0.0
        total_theta_day = 0.0

        for pos in positions:
            display = compute_display(pos, self._cache)
            total_margin += display["margin"]
            if display["theta_dollars"] is not None:
                total_theta_day += display["theta_dollars"]
            frame = build_row(
                self._rows_frame, pos, display,
                mergeable_symbols, seen_stock_symbols,
                on_edit=self._edit_position,
                on_delete=self._delete_position,
                on_merge=self._merge_stock,
            )
            self._row_widgets.append(frame)

        self._update_summary(total_margin, total_theta_day)

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    def _add_position(self):
        dlg = PositionDialog(self)
        if dlg.result:
            d = dlg.result
            pos_repo.insert_position(d)
            self._cache.invalidate(d["symbol"])
            self._refresh_positions()

    def _edit_position(self, row_id: int):
        pos = pos_repo.get_position(row_id)
        if not pos:
            return
        dlg = PositionDialog(self, row=dataclasses.asdict(pos))
        if dlg.result:
            d = dlg.result
            pos_repo.update_position(row_id, d)
            self._cache.invalidate(d["symbol"])
            self._refresh_positions()

    def _merge_stock(self, symbol: str):
        """Merge all OPEN STOCK rows for symbol into one, weighted-avg cost basis."""
        if not messagebox.askyesno("Merge Positions",
                                   f"Merge all {symbol} STOCK positions into one?"):
            return
        pos_repo.merge_stock_positions(symbol)
        self._refresh_positions()

    def _delete_position(self, row_id: int):
        if not messagebox.askyesno("Delete", "Delete this position?"):
            return
        pos_repo.delete_position(row_id)
        self._refresh_positions()
