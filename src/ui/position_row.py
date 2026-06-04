"""Builds a single position row widget and computes its display values."""

from __future__ import annotations

import tkinter as tk
from typing import Callable

import constants
from models import Position
from services.cache_service import CacheService
import services.position_service as ps
import ui_styles as styles
from ui.tooltip import Tooltip


def compute_display(pos: Position, cache: CacheService) -> dict:
    """Compute all display values for one position row."""
    ot = ps.pricing_option_type(pos)
    key = (pos.symbol, pos.expiration, pos.strike, ot)
    price = cache.price(pos.symbol)
    opt_price = cache.opt_price(key) if pos.strike else None
    theta = cache.theta(key) if pos.strike else None

    if ps.is_spread(pos):
        long_key = (pos.symbol, pos.expiration, pos.strike2, ot)
        long_opt = cache.opt_price(long_key)
        long_theta = cache.theta(long_key)
        net_opt = (opt_price - long_opt) if (opt_price is not None and long_opt is not None) else None
        opt_str = f"{net_opt:.2f}" if net_opt is not None else "—"
        td = ps.theta_dollars(pos, theta, long_theta)
        short_line, long_line = ps.spread_leg_abbrevs(pos)
        short_line, long_line = (short_line, long_line) if ps.is_credit_spread(pos) else (long_line, short_line)
    else:
        long_line = None
        opt_str = f"{opt_price:.2f}" if opt_price is not None else "—"
        td = ps.theta_dollars(pos, theta)
        short_line = ps.position_abbrev(pos)

    days = ps.days_to_expiry(pos)
    bg = styles.expiry_color(days)
    return {
        "abbrev": short_line,
        "abbrev2": long_line,
        "qty": ps.display_quantity(pos),
        "margin": ps.margin_k(pos),
        "bg": bg,
        "fg": styles.text_color(bg),
        "price": price,
        "itm": ps.is_itm(pos, price),
        "opt_str": opt_str,
        "theta_dollars": td,
        "theta_str": f"${round(td):,d}" if td is not None else "—",
        "is_stock_row": ps.is_stock(pos),
        "is_profitable": ps.is_profitable(pos, price),
    }


def build_row(
    parent: tk.Frame,
    pos: Position,
    display: dict,
    cache: CacheService,
    mergeable_groups: set[tuple],
    seen_merge_groups: set[tuple],
    on_edit: Callable[[int], None],
    on_delete: Callable[[int], None],
    on_merge: Callable[[tuple], None],
) -> tk.Frame:
    """Create and return a tk.Frame representing one position row."""
    bg = display["bg"]
    fg = display["fg"]
    row_frame = tk.Frame(parent, bg=bg)
    row_frame.pack(fill=tk.X, pady=1)

    def _price_text(sym=pos.symbol):
        p = cache.price(sym)
        return f"{sym} last: ${p:.2f}" if p is not None else f"{sym} last: N/A"
    Tooltip(row_frame, _price_text)

    # ITM indicator swatch
    itm_canvas = tk.Canvas(row_frame, width=6, height=16, bg=bg, highlightthickness=0)
    if display["itm"]:
        itm_canvas.create_rectangle(1, 2, 5, 14, fill=constants.ITM_INDICATOR, outline="")
    itm_canvas.pack(side=tk.LEFT)

    # Profit indicator swatch
    profit_canvas = tk.Canvas(row_frame, width=6, height=16, bg=bg, highlightthickness=0)
    if display["is_profitable"]:
        profit_canvas.create_rectangle(1, 2, 5, 14, fill=constants.STOCK_GAIN_INDICATOR, outline="")
    profit_canvas.pack(side=tk.LEFT)

    pos_font = ("TkDefaultFont", 8, "underline") if display["is_stock_row"] else ("TkDefaultFont", 8)
    if display["abbrev2"]:
        pos_cell = tk.Frame(row_frame, bg=bg)
        pos_cell.pack(side=tk.LEFT)
        tk.Label(pos_cell, text=display["abbrev"],  bg=bg, fg=fg,
                 anchor=tk.W, width=17, font=pos_font).pack(side=tk.TOP, fill=tk.X)
        tk.Label(pos_cell, text=display["abbrev2"], bg=bg, fg=fg,
                 anchor=tk.W, width=17, font=("TkDefaultFont", 7)).pack(side=tk.TOP, fill=tk.X)
    else:
        tk.Label(row_frame, text=display["abbrev"], bg=bg, fg=fg,
                 anchor=tk.W, width=17, font=pos_font).pack(side=tk.LEFT)
    tk.Label(row_frame, text=str(display["qty"]), bg=bg, fg=fg,
             width=5, anchor=tk.CENTER).pack(side=tk.LEFT)
    tk.Label(row_frame, text=f"{display['margin']:.1f}", bg=bg, fg=fg,
             width=7, anchor=tk.E).pack(side=tk.LEFT)
    tk.Label(row_frame, text=display["opt_str"], bg=bg, fg=fg,
             width=6, anchor=tk.E).pack(side=tk.LEFT)
    tk.Label(row_frame, text=display["theta_str"], bg=bg, fg=fg,
             width=6, anchor=tk.E).pack(side=tk.LEFT)

    row_id = pos.id
    sym = pos.symbol
    btn_frame = tk.Frame(row_frame, bg=bg)
    btn_frame.pack(side=tk.LEFT, padx=2)
    tk.Button(btn_frame, text="✎", width=1, pady=0, font=("TkDefaultFont", 7),
              command=lambda rid=row_id: on_edit(rid)).pack(side=tk.LEFT)
    tk.Button(btn_frame, text="✕", width=1, pady=0, font=("TkDefaultFont", 7),
              command=lambda rid=row_id: on_delete(rid)).pack(side=tk.LEFT)
    merge_key = (pos.symbol, pos.expiration or "", pos.strike or 0.0)
    if ps.is_stock(pos) and merge_key in mergeable_groups:
        if merge_key not in seen_merge_groups:
            tk.Button(btn_frame, text="⊕", width=1, pady=0, font=("TkDefaultFont", 7),
                      command=lambda k=merge_key: on_merge(k)).pack(side=tk.LEFT)
        seen_merge_groups.add(merge_key)

    return row_frame
