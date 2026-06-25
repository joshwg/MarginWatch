"""Build openpyxl workbooks from position data."""

from __future__ import annotations

from models import Position
from services.cache_service import CacheService
import services.position_service as ps


def build_workbook(positions: list[Position], cache: CacheService) -> tuple:
    """Return (workbook, row_count) for the given positions.

    Columns: A=Position  B=Margin($k)  C=Qty  D=Position Theta($)  E=Expiration  F=Per-Share Theta

    Note: GOOGLEFINANCE is a Google Sheets-only function and is not included
    here because it produces errors when the file is opened in Excel.
    Use the CSV export instead if you need the GOOGLEFINANCE price column.
    """
    import openpyxl
    wb = openpyxl.Workbook()
    ws = wb.active
    assert ws is not None
    ws.title = "Positions"
    ws.append(["Position", "Margin ($k)", "Qty", "Position Theta ($)", "Expiration", "Per-Share Theta"])

    excel_row = 2  # header is row 1; data starts at row 2
    row_count = 0

    for pos in positions:
        if ps.is_stock(pos):
            stock_label = f"{pos.symbol} stock ({pos.long_shares or 0} sh)"
            stock_margin = round(ps.margin_k(pos), 2)
            if ps.has_covered_call(pos):
                ws.append([stock_label, stock_margin, pos.long_shares or 0, 0, 0, ""])
                excel_row += 1
                row_count += 1
                key = (pos.symbol, pos.expiration, pos.strike, "CALL")
                raw_theta = cache.theta(key)
                ws.append([
                    ps.position_abbrev(pos),
                    0,
                    pos.quantity,
                    f"=-F{excel_row}*C{excel_row}*100" if raw_theta is not None else "",
                    pos.expiration or "",
                    round(raw_theta, 4) if raw_theta is not None else "",
                ])
                excel_row += 1
                row_count += 1
            else:
                ws.append([stock_label, stock_margin, pos.long_shares or 0, 0, 0, ""])
                excel_row += 1
                row_count += 1
        elif ps.is_straddle(pos):
            put_strike = pos.strike2
            call_key = (pos.symbol, pos.expiration, pos.strike,  'CALL')
            put_key  = (pos.symbol, pos.expiration, put_strike,  'PUT')
            call_theta = cache.theta(call_key)
            put_theta  = cache.theta(put_key)
            call_abbrev, put_abbrev = ps.straddle_leg_abbrevs(pos)
            ws.append([
                call_abbrev,
                round(ps.margin_k(pos), 2),
                pos.quantity,
                f"=-F{excel_row}*C{excel_row}*100" if call_theta is not None else "",
                pos.expiration or "",
                round(call_theta, 4) if call_theta is not None else "",
            ])
            excel_row += 1
            row_count += 1
            ws.append([
                put_abbrev,
                0,
                pos.quantity,
                f"=-F{excel_row}*C{excel_row}*100" if put_theta is not None else "",
                pos.expiration or "",
                round(put_theta, 4) if put_theta is not None else "",
            ])
            excel_row += 1
            row_count += 1
        elif ps.is_spread(pos):
            ot = ps.pricing_option_type(pos)
            short_key = (pos.symbol, pos.expiration, pos.strike, ot)
            long_key  = (pos.symbol, pos.expiration, pos.strike2, ot)
            short_theta = cache.theta(short_key)
            long_theta  = cache.theta(long_key)
            short_abbrev, long_abbrev = ps.spread_leg_abbrevs(pos)
            ws.append([
                short_abbrev,
                round(ps.margin_k(pos), 2),
                pos.quantity,
                f"=-F{excel_row}*C{excel_row}*100" if short_theta is not None else "",
                pos.expiration or "",
                round(short_theta, 4) if short_theta is not None else "",
            ])
            excel_row += 1
            row_count += 1
            ws.append([
                long_abbrev,
                0,
                pos.quantity,
                f"=F{excel_row}*C{excel_row}*100" if long_theta is not None else "",
                pos.expiration or "",
                round(long_theta, 4) if long_theta is not None else "",
            ])
            excel_row += 1
            row_count += 1
        else:
            ot = ps.pricing_option_type(pos)
            key = (pos.symbol, pos.expiration, pos.strike, ot)
            raw_theta = cache.theta(key) if pos.strike else None
            ws.append([
                ps.position_abbrev(pos),
                round(ps.margin_k(pos), 2),
                pos.quantity,
                f"=-F{excel_row}*C{excel_row}*100" if raw_theta is not None else "",
                pos.expiration or "",
                round(raw_theta, 4) if raw_theta is not None else "",
            ])
            excel_row += 1
            row_count += 1

    return wb, row_count
