"""Main application window."""

from __future__ import annotations

import dataclasses
import threading
import tkinter as tk
from tkinter import ttk, messagebox, filedialog

import db
import constants
import utils
import repositories.positions_repository as pos_repo
import repositories.config_repository as cfg_repo
from services.cache_service import CacheService
import services.export_service as export_service
import services.position_service as ps
from ui.position_dialog import PositionDialog
from ui.position_row import compute_display, build_row


class MarginWatchApp(tk.Tk):
    def __init__(self):
        print("Starting...")
        super().__init__()
        self.title("MarginWatch")
        self.geometry("460x800")
        self.resizable(False, False)

        db.init_db()
        self._config = cfg_repo.load()
        _r = utils.parse_float(self._config.get("RiskFreeRate", "4.5"), 4.5) / 100.0
        self._cache = CacheService(r=_r)
        self._col_sort: tuple[str, str] | None = None  # (col_key, "asc"|"desc")
        self._refreshing = False  # re-entrancy guard
        self._refresh_pending = False

        print("Exposing GUI...")
        self._build_ui()
        self.bind("<Map>", self._on_first_map, add="+")
        self.after(0, self._deferred_load)

    # ------------------------------------------------------------------
    # Config
    # ------------------------------------------------------------------

    def _save_config(self):
        try:
            margin        = int(self._margin_var.get())
            multiplier    = float(self._multiplier_var.get())
            risk_free_pct = float(self._risk_free_var.get())
        except ValueError:
            messagebox.showerror("Invalid input", "Please enter valid numeric values.")
            return
        if not (0.5 <= multiplier <= 4.0):
            messagebox.showerror("Invalid multiplier",
                                 "Multiplier must be between 0.5 and 4.0.")
            return
        if not (0.0 <= risk_free_pct <= 20.0):
            messagebox.showerror("Invalid rate", "Risk-free rate must be between 0 and 20%.")
            return
        cfg_repo.save(margin, multiplier, risk_free_pct)
        self._config["MaximumMarginBasis"] = str(margin)
        self._config["MarginMultiplier"]   = str(multiplier)
        self._config["RiskFreeRate"]       = str(risk_free_pct)
        self._cache._r = risk_free_pct / 100.0
        self._config_saved_lbl.config(text="Requirements Saved")
        self.after(3000, lambda: self._config_saved_lbl.config(text=""))

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
                        value="alpha", command=self._on_sort_change).pack(side=tk.LEFT)
        ttk.Radiobutton(sort_frame, text="Exp", variable=self._sort_var,
                        value="expiry", command=self._on_sort_change).pack(side=tk.LEFT)
        ttk.Radiobutton(sort_frame, text="Type", variable=self._sort_var,
                        value="type", command=self._on_sort_change).pack(side=tk.LEFT)

        # Right: +, refresh, and export buttons
        plus_frame = ttk.Frame(top_bar)
        plus_frame.pack(side=tk.RIGHT, padx=6)
        ttk.Button(plus_frame, text="+", width=3,
                   command=self._add_position).pack(side=tk.LEFT, expand=True, pady=4)
        ttk.Button(plus_frame, text="↻", width=3,
                   command=self._force_refresh).pack(side=tk.LEFT, expand=True, pady=4)
        ttk.Button(plus_frame, text="💾", width=3,
                   command=self._export_xlsx).pack(side=tk.LEFT, expand=True, pady=4)

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
        _COL_DEFS = [
            ("Position", 125, "position"),
            ("#", 28, "qty"),
            ("Margin", 58, "margin"),
            ("$/shr", 52, "opt"),
            ("Theta", 42, "theta"),
            ("", 44, None),
        ]
        hdr = ttk.Frame(self._rows_frame)
        hdr.pack(fill=tk.X)
        self._hdr_labels: dict[str, ttk.Label] = {}
        for text, w, col_key in _COL_DEFS:
            lbl = ttk.Label(hdr, text=text, width=w // 7, relief="groove",
                            anchor=tk.CENTER)
            lbl.pack(side=tk.LEFT)
            if col_key:
                lbl.bind("<Button-1>", lambda e, k=col_key: self._on_col_header_click(k))
                lbl.configure(cursor="hand2")
                self._hdr_labels[col_key] = lbl

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

        ttk.Label(config_frame, text="Risk-Free Rate % (0 – 20):").grid(
            row=2, column=0, sticky=tk.W, padx=6, pady=(2, 4))
        initial_rf = utils.parse_float(
            self._config.get("RiskFreeRate", "4.5"), 4.5)
        self._risk_free_var = tk.StringVar(value=f"{initial_rf:.1f}")
        ttk.Spinbox(config_frame, from_=0.0, to=20.0, increment=0.1,
                    textvariable=self._risk_free_var, width=6,
                    format="%.1f").grid(row=2, column=1, padx=6, pady=(2, 4), sticky=tk.W)

        save_row = ttk.Frame(config_frame)
        save_row.grid(row=3, column=0, columnspan=2, padx=6, pady=(0, 8), sticky=tk.E)
        ttk.Button(save_row, text="Save", command=self._save_config).pack(side=tk.LEFT)
        self._config_saved_lbl = ttk.Label(save_row, text="", foreground="green")
        self._config_saved_lbl.pack(side=tk.LEFT, padx=(8, 0))

    def _deferred_load(self):
        print("Loading positions...")
        self._refresh_positions()
        print("Positions loaded.")

    def _on_first_map(self, _event=None):
        self.unbind("<Map>")
        self._ensure_on_screen()

    def _ensure_on_screen(self):
        self.update_idletasks()
        sw = self.winfo_screenwidth()
        sh = self.winfo_screenheight()
        x = self.winfo_x()
        y = self.winfo_y()
        w = self.winfo_width()
        h = self.winfo_height()
        if x + w < 50 or x > sw - 50 or y < 0 or y > sh - 50:
            self.geometry(f"+{(sw - w) // 2}+{(sh - h) // 2}")

    def _on_frame_configure(self, _event=None):
        self._rows_canvas.configure(scrollregion=self._rows_canvas.bbox("all"))

    def _on_canvas_configure(self, event):
        self._rows_canvas.itemconfig(self._rows_frame_id, width=event.width)

    # ------------------------------------------------------------------
    # Positions refresh
    # ------------------------------------------------------------------

    def _on_sort_change(self):
        self._col_sort = None
        self._update_col_headers()
        cfg_repo.save_sort(self._sort_var.get())
        self._refresh_positions()

    def _on_col_header_click(self, col_key: str):
        if self._col_sort and self._col_sort[0] == col_key:
            self._col_sort = None if self._col_sort[1] == "desc" else (col_key, "desc")
        else:
            self._col_sort = (col_key, "asc")
        self._update_col_headers()
        self._refresh_positions()

    def _update_col_headers(self):
        _BASE = {"position": "Position", "qty": "#", "margin": "Margin",
                 "opt": "$/shr", "theta": "Theta"}
        for key, lbl in self._hdr_labels.items():
            base = _BASE[key]
            if self._col_sort and self._col_sort[0] == key:
                indicator = " ▲" if self._col_sort[1] == "asc" else " ▼"
                lbl.config(text=base + indicator)
            else:
                lbl.config(text=base)

    def _load_sorted_positions(self) -> list:
        rows = pos_repo.get_open_positions()
        sort = self._sort_var.get()
        if sort == "alpha":
            return sorted(rows, key=lambda r: (r.symbol, r.expiration or "", r.strike or 0.0))
        if sort == "type":
            def _type_key(r):
                # CALL=0, STOCK-with-cover=0, PUT=1, STOCK-no-cover=2
                if r.option_type == "CALL" or (r.option_type == "STOCK" and r.strike):
                    t = 0
                elif r.option_type == "PUT":
                    t = 1
                else:
                    t = 2
                return (t, r.symbol, r.expiration or "", r.strike or 0.0)
            return sorted(rows, key=_type_key)
        return sorted(rows, key=lambda r: (r.expiration or "", r.symbol, r.strike or 0.0))

    def _update_summary(self, total_margin: float, total_theta_day: float):
        self._total_lbl.config(text=f"${total_margin:.1f}k")
        self._theta_lbl.config(text=f"${round(total_theta_day):,d}/d")
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

    def _force_refresh(self):
        self._cache.__init__()
        self._refresh_positions()

    def _refresh_positions(self):
        if self._refreshing:
            self._refresh_pending = True
            return
        self._refreshing = True
        self._refresh_pending = False
        positions = self._load_sorted_positions()
        self._do_refresh_positions(positions)
        threading.Thread(target=self._bg_fetch, args=(positions,), daemon=True).start()

    def _bg_fetch(self, positions):
        self._cache.fetch_all(positions)
        self.after(0, lambda: self._finish_refresh(positions))

    def _finish_refresh(self, positions):
        try:
            self._do_refresh_positions(positions)
        finally:
            self._refreshing = False
            if self._refresh_pending:
                self._refresh_pending = False
                self._refresh_positions()

    def _do_refresh_positions(self, positions):
        for w in self._row_widgets:
            w.destroy()
        self._row_widgets.clear()

        items = [(pos, compute_display(pos, self._cache)) for pos in positions]

        if self._col_sort:
            col, direction = self._col_sort
            reverse = direction == "desc"

            def _col_key(item):
                pos, disp = item
                if col == "position":
                    return (0, pos.symbol, pos.expiration or "", pos.strike or 0.0)
                if col == "qty":
                    return (0, pos.quantity or 0)
                if col == "margin":
                    return (0, disp["margin"])
                if col == "opt":
                    try:
                        return (0, float(disp["opt_str"]))
                    except (ValueError, TypeError):
                        return (1, 0.0)
                if col == "theta":
                    td = disp["theta_dollars"]
                    return (0, td) if td is not None else (1, 0.0)
                return (0,)

            items.sort(key=_col_key, reverse=reverse)

        mergeable_groups = ps.mergeable_stock_groups([pos for pos, _ in items])
        seen_merge_groups: set[tuple] = set()
        total_margin = 0.0
        total_theta_day = 0.0

        for pos, display in items:
            total_margin += display["margin"]
            if display["theta_dollars"] is not None:
                total_theta_day += display["theta_dollars"]
            frame = build_row(
                self._rows_frame, pos, display, self._cache,
                mergeable_groups, seen_merge_groups,
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

    def _confirm(self, title: str, message: str) -> bool:
        """Yes/No dialog centered over the main window."""
        result = tk.BooleanVar(value=False)
        dlg = tk.Toplevel(self)
        dlg.title(title)
        dlg.resizable(False, False)
        dlg.transient(self)
        ttk.Label(dlg, text=message, wraplength=260, padding=(12, 10)).pack()
        bf = ttk.Frame(dlg)
        bf.pack(pady=(0, 10))
        ttk.Button(bf, text="Yes", command=lambda: [result.set(True), dlg.destroy()]).pack(side=tk.LEFT, padx=6)
        ttk.Button(bf, text="No",  command=dlg.destroy).pack(side=tk.LEFT, padx=6)
        dlg.update_idletasks()
        x = self.winfo_rootx() + (self.winfo_width()  - dlg.winfo_reqwidth())  // 2
        y = self.winfo_rooty() + (self.winfo_height() - dlg.winfo_reqheight()) // 2
        dlg.geometry(f"+{x}+{y}")
        dlg.focus_force()
        dlg.grab_set()
        dlg.wait_window()
        return result.get()

    def _merge_stock(self, key: tuple):
        symbol, expiration, strike = key
        if not self._confirm("Merge Positions",
                             f"Merge {symbol} STOCK positions into one?"):
            return
        pos_repo.merge_stock_positions(symbol, expiration, strike)
        self._refresh_positions()

    def _delete_position(self, row_id: int):
        if not self._confirm("Delete", "Delete this position?"):
            return
        pos_repo.delete_position(row_id)
        self._refresh_positions()

    def _export_xlsx(self):
        downloads = utils.windows_downloads_dir()
        path = filedialog.asksaveasfilename(
            title="Export positions",
            initialdir=downloads,
            initialfile="positions.xlsx",
            defaultextension=".xlsx",
            filetypes=[("Excel workbook", "*.xlsx")],
        )
        if not path:
            return

        positions = sorted(
            pos_repo.get_open_positions(),
            key=lambda r: (r.symbol, r.expiration or "", r.strike or 0.0),
        )
        self._cache.fetch_all(positions)
        wb, row_count = export_service.build_workbook(positions, self._cache)
        wb.save(path)
        messagebox.showinfo("Export", f"Saved {row_count} rows to:\n{path}")
