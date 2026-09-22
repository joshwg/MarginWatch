"""Build openpyxl workbooks from position data."""

from __future__ import annotations

from models import Position
from services.cache_service import CacheService
import repositories.portfolios_repository as pf_repo
import services.position_service as ps


def _t10k(td, margin_k):
    """θ/10k as a cell value: the figure, or an empty cell when unpriceable."""
    v = ps.theta_per_10k(td, margin_k)
    return "" if v is None else v


def build_workbook(positions: list[Position], cache: CacheService) -> tuple:
    """Return (workbook, row_count) for the given positions.

    Columns: A=Position  B=Margin($k)  C=Qty  D=Position Theta($)  E=Expiration
             F=θ/10k  G=Portfolio

    F is the position's daily theta per $10,000 of margin — the same figure as
    the web table's θ/10k column — and sits on the row that carries the
    position's margin (blank on second leg rows).  Theta values are written as
    numbers, not formulas: they used to be derived from a per-share theta
    column that no longer exists.

    Note: GOOGLEFINANCE is a Google Sheets-only function and is not included
    here because it produces errors when the file is opened in Excel.
    Use the CSV export instead if you need the GOOGLEFINANCE price column.
    """
    import openpyxl
    wb = openpyxl.Workbook()
    ws = wb.active
    assert ws is not None
    ws.title = "Positions"
    ws.append(["Position", "Margin ($k)", "Qty", "Position Theta ($)", "Expiration",
               "θ/10k", "Portfolio"])

    pf_names = {pf.id: pf.name for pf in pf_repo.list_portfolios()}
    row_count = 0

    for pos in positions:
        pf_name = pf_names.get(getattr(pos, "portfolio_id", None), "")
        _add = lambda row: ws.append(row + [pf_name])   # noqa: E731
        if ps.is_stock(pos):
            stock_label = f"{pos.symbol} stock ({pos.long_shares or 0} sh)"
            stock_margin = round(ps.margin_k(pos), 2)
            if ps.has_covered_call(pos):
                key = (pos.symbol, pos.expiration, pos.strike, "CALL")
                raw_theta = cache.theta(key)
                td = -raw_theta * pos.quantity * 100 if raw_theta is not None else None
                _add([stock_label, stock_margin, pos.long_shares or 0, 0, 0,
                      _t10k(td, stock_margin)])
                row_count += 1
                _add([
                    ps.position_abbrev(pos),
                    0,
                    pos.quantity,
                    round(td, 2) if td is not None else "",
                    pos.expiration or "",
                    "",
                ])
                row_count += 1
            else:
                _add([stock_label, stock_margin, pos.long_shares or 0, 0, 0, ""])
                row_count += 1
        elif ps.is_straddle(pos):
            put_strike = pos.strike2
            call_key = (pos.symbol, pos.expiration, pos.strike,  'CALL')
            put_key  = (pos.symbol, pos.expiration, put_strike,  'PUT')
            call_theta = cache.theta(call_key)
            put_theta  = cache.theta(put_key)
            call_abbrev, put_abbrev = ps.straddle_leg_abbrevs(pos)
            margin = round(ps.margin_k(pos), 2)
            # Position theta is whatever legs have priced, same as the table.
            leg_tds = [-t * pos.quantity * 100 for t in (call_theta, put_theta) if t is not None]
            total_td = sum(leg_tds) if leg_tds else None
            _add([
                call_abbrev,
                margin,
                pos.quantity,
                round(-call_theta * pos.quantity * 100, 2) if call_theta is not None else "",
                pos.expiration or "",
                _t10k(total_td, margin),
            ])
            row_count += 1
            _add([
                put_abbrev,
                0,
                pos.quantity,
                round(-put_theta * pos.quantity * 100, 2) if put_theta is not None else "",
                pos.expiration or "",
                "",
            ])
            row_count += 1
        elif ps.is_spread(pos):
            ot = ps.pricing_option_type(pos)
            short_key = (pos.symbol, pos.expiration, pos.strike, ot)
            long_key  = (pos.symbol, pos.expiration, pos.strike2, ot)
            short_theta = cache.theta(short_key)
            long_theta  = cache.theta(long_key)
            short_abbrev, long_abbrev = ps.spread_leg_abbrevs(pos)
            margin = round(ps.margin_k(pos), 2)
            _add([
                short_abbrev,
                margin,
                pos.quantity,
                round(-short_theta * pos.quantity * 100, 2) if short_theta is not None else "",
                pos.expiration or "",
                _t10k(ps.theta_dollars(pos, short_theta, long_theta), margin),
            ])
            row_count += 1
            _add([
                long_abbrev,
                0,
                pos.quantity,
                round(long_theta * pos.quantity * 100, 2) if long_theta is not None else "",
                pos.expiration or "",
                "",
            ])
            row_count += 1
        else:
            ot = ps.pricing_option_type(pos)
            key = (pos.symbol, pos.expiration, pos.strike, ot)
            raw_theta = cache.theta(key) if pos.strike else None
            td = ps.theta_dollars(pos, raw_theta)
            margin = round(ps.margin_k(pos), 2)
            _add([
                ps.position_abbrev(pos),
                margin,
                pos.quantity,
                round(td, 2) if td is not None else "",
                pos.expiration or "",
                _t10k(td, margin),
            ])
            row_count += 1

    return wb, row_count
