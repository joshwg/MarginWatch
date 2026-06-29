"""Web UI entry point for MarginWatch.

Run:
    export MARGIN_PWD=yourpassword
    export PYTHONPATH=.
    python main_web.py
"""

from __future__ import annotations

import csv
import dataclasses
import hashlib
import io
import logging
import os
import threading
from datetime import datetime, timedelta

logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)

from flask import (Flask, Response, jsonify, redirect, render_template,
                   request, session, send_from_directory, url_for)

import constants
import db
import repositories.config_repository as cfg_repo
import repositories.positions_repository as pos_repo
import services.position_service as ps
import ui_styles as styles
import utils
from models import Position
from services.cache_service import CacheService


def _require_password() -> str:
    pwd = os.environ.get("MARGIN_PWD", "")
    if not pwd:
        raise RuntimeError(
            "MARGIN_PWD environment variable must be set before starting the web server."
        )
    return pwd


_password = _require_password()

app = Flask(
    __name__,
    template_folder="ui_web/templates",
    static_folder="ui_web/static",
)
app.secret_key = hashlib.sha256(_password.encode()).digest()

db.init_db()
_r_default = 0.045
try:
    _cfg_startup = cfg_repo.load()
    _r_default = float(_cfg_startup.get("RiskFreeRate", 4.5)) / 100.0
except Exception:
    pass
_cache = CacheService(r=_r_default)   # use_extended always starts False

# Warm the cache in the background so the first page load is not blocked by
# network calls.  Positions are visible immediately; prices fill in as the
# thread completes.
def _startup_prefetch() -> None:
    try:
        _cache.fetch_all(pos_repo.get_open_positions())
    except Exception:
        pass

threading.Thread(target=_startup_prefetch, daemon=True).start()


# ---------------------------------------------------------------------------
# Display helper (mirrors ui/position_row.py without the tkinter dependency)
# ---------------------------------------------------------------------------

def _compute_display(pos: Position, cache: CacheService) -> dict:
    price = cache.price(pos.symbol)

    if ps.is_straddle(pos):
        put_strike = pos.strike2
        call_key = (pos.symbol, pos.expiration, pos.strike,     'CALL')
        put_key  = (pos.symbol, pos.expiration, put_strike,     'PUT')
        call_opt   = cache.opt_price(call_key)
        put_opt    = cache.opt_price(put_key)
        call_theta = cache.theta(call_key)
        put_theta  = cache.theta(put_key)
        net_opt = (call_opt + put_opt) if (call_opt is not None and put_opt is not None) else None
        opt_str = f"{net_opt:.2f}" if net_opt is not None else "—"
        if call_theta is not None and put_theta is not None:
            td = (-call_theta - put_theta) * 100 * pos.quantity
        elif call_theta is not None:
            td = -call_theta * 100 * pos.quantity
        elif put_theta is not None:
            td = -put_theta * 100 * pos.quantity
        else:
            td = None
        abbrev, abbrev2 = ps.straddle_leg_abbrevs(pos)
        # Worst-case leg drives the risk ball
        call_delta = cache.delta(call_key)
        put_delta  = cache.delta(put_key)
        deltas = [d for d in (call_delta, put_delta) if d is not None]
        delta = max(deltas) if deltas else None

    else:
        ot = ps.pricing_option_type(pos)
        key = (pos.symbol, pos.expiration, pos.strike, ot)
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
            abbrev, abbrev2 = (short_line, long_line) if ps.is_credit_spread(pos) else (long_line, short_line)
        else:
            abbrev2 = None
            opt_str = f"{opt_price:.2f}" if opt_price is not None else "—"
            td = ps.theta_dollars(pos, theta)
            abbrev = ps.position_abbrev(pos)

        delta = cache.delta(key) if pos.strike else None

    days = ps.days_to_expiry(pos)
    bg = styles.expiry_color(days)
    return {
        "abbrev": abbrev,
        "abbrev2": abbrev2,
        "qty": ps.display_quantity(pos),
        "margin": ps.margin_k(pos),
        "bg": bg,
        "fg": styles.text_color(bg),
        "itm": ps.is_itm(pos, price),
        "itm_amount": ps.itm_amount(pos, price),
        "opt_str": opt_str,
        "theta_dollars": td,
        "theta_str": f"${round(td):,d}" if td is not None else "—",
        "is_stock_row": ps.is_stock(pos),
        "is_profitable": ps.is_profitable(pos, price),
        "delta": round(delta, 3) if delta is not None else None,
    }


# ---------------------------------------------------------------------------
# Debug guard
# ---------------------------------------------------------------------------

def _debug_enabled():
    return os.environ.get("MARGIN_DEBUG") == "1"


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------

@app.errorhandler(Exception)
def handle_exception(e):
    import traceback
    from werkzeug.exceptions import HTTPException
    # Always log method + URL so 404s are diagnosable.
    app.logger.error(
        "%s %s → %s\n%s",
        request.method, request.path, type(e).__name__, traceback.format_exc(),
    )
    if isinstance(e, HTTPException):
        # Preserve the original HTTP status code (e.g. 404, 405) instead of
        # always returning 500.
        return jsonify({"error": e.description}), e.code
    return jsonify({"error": str(e)}), 500


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

def _is_authenticated() -> bool:
    if not session.get("authenticated"):
        return False
    last_str = session.get("last_activity")
    if not last_str:
        return False
    cutoff = datetime.now() - timedelta(minutes=constants.SESSION_TIMEOUT_MINUTES)
    try:
        if datetime.fromisoformat(last_str) < cutoff:
            session.clear()
            return False
    except ValueError:
        session.clear()
        return False
    return True


def _touch_session() -> None:
    session["last_activity"] = datetime.now().isoformat()


@app.before_request
def check_auth():
    if request.endpoint in ("login", "static", "favicon", "api_price", "api_optprice"):
        return
    if not _is_authenticated():
        if request.path.startswith("/api/") or request.path == "/export/csv":
            return jsonify({"error": "unauthorized"}), 401
        return redirect(url_for("login"))
    _touch_session()


# ---------------------------------------------------------------------------
# Login / logout
# ---------------------------------------------------------------------------

@app.route("/login", methods=["GET", "POST"])
def login():
    error = None
    if request.method == "POST":
        if request.form.get("password") == _password:
            session.clear()
            session["authenticated"] = True
            _touch_session()
            _cache.set_extended_hours(False)   # reset to default on each login
            return redirect(url_for("index"))
        error = "Incorrect password."
    return render_template("login.html", error=error)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


# ---------------------------------------------------------------------------
# Favicon
# ---------------------------------------------------------------------------

@app.route("/favicon.ico")
def favicon():
    assert app.static_folder is not None
    return send_from_directory(app.static_folder, "favicon.ico",
                               mimetype="image/vnd.microsoft.icon")


# ---------------------------------------------------------------------------
# Main page
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    return render_template("index.html")


# ---------------------------------------------------------------------------
# Positions API
# ---------------------------------------------------------------------------

def _prefetch_symbol(symbol: str) -> None:
    """Fetch fresh price and option data for *symbol* in a daemon thread.

    Called after a position is saved so the next GET /api/positions response
    has data ready without blocking the save response on network calls.
    Any exception is silently swallowed — a background prefetch failure is
    non-fatal; the table will just show '—' until the next manual refresh.
    """
    try:
        positions = [p for p in pos_repo.get_open_positions() if p.symbol == symbol]
        if positions:
            _cache.fetch_all(positions)
        else:
            _cache.fetch_price(symbol)
    except Exception:
        pass


def _effective_expiration(r) -> str:
    """Sort key for expiration: plain stock (no cover) always sorts last."""
    if ps.is_stock(r) and not r.strike:
        return constants.NO_EXPIRATION
    return r.expiration or constants.NO_EXPIRATION


def _sorted_positions(sort: str) -> list:
    rows = pos_repo.get_open_positions()
    if sort == "alpha":
        return sorted(rows, key=lambda r: (r.symbol, _effective_expiration(r), r.strike or 0.0))
    if sort == "type":
        def _type_key(r):
            if r.option_type == "CALL" or (r.option_type == "STOCK" and r.strike):
                t = 0
            elif r.option_type == "PUT":
                t = 1
            else:
                t = 2
            return (t, r.symbol, _effective_expiration(r), r.strike or 0.0)
        return sorted(rows, key=_type_key)
    return sorted(rows, key=lambda r: (_effective_expiration(r), r.symbol, r.strike or 0.0))


@app.route("/api/positions")
def api_positions():
    """Phase 1: return position list from the database immediately.

    Does NOT block on market-data fetches — uses whatever is already in the
    cache (populated by the startup prefetch or a previous load).  The client
    renders the table right away, then calls /api/prices as phase 2 to fill in
    live prices and greeks.
    """
    config = cfg_repo.load()
    sort = request.args.get("sort", config.get("SortOrder", "alpha"))
    positions = _sorted_positions(sort)

    max_margin = utils.parse_float(config.get("MaximumMarginBasis", "250000"), 250000.0)
    multiplier = utils.parse_float(config.get("MarginMultiplier", "1.5"), 1.5)

    mergeable_groups = ps.mergeable_stock_groups(positions)
    seen_merge_groups: set[tuple] = set()

    items = []
    total_margin = 0.0
    total_theta_day = 0.0

    for pos in positions:
        display = _compute_display(pos, _cache)
        total_margin += display["margin"]
        if display["theta_dollars"] is not None:
            total_theta_day += display["theta_dollars"]

        merge_key = (pos.symbol, pos.expiration or "", pos.strike or 0.0)
        can_merge = ps.is_stock(pos) and merge_key in mergeable_groups
        show_merge = False
        if can_merge and merge_key not in seen_merge_groups:
            show_merge = True
            seen_merge_groups.add(merge_key)

        exp_display = pos.expiration if pos.expiration != constants.NO_EXPIRATION else None
        stock_price = _cache.price(pos.symbol)
        items.append({
            "id": pos.id,
            "symbol": pos.symbol,
            "price": round(stock_price, 2) if stock_price is not None else None,
            "option_type": pos.option_type,
            "strike": pos.strike,
            "expiration": exp_display,
            "quantity": pos.quantity,
            "long_shares": pos.long_shares,
            "long_cost": pos.long_cost,
            "strike2": pos.strike2,
            "abbrev": display["abbrev"],
            "abbrev2": display["abbrev2"],
            "qty": display["qty"],
            "margin": round(display["margin"], 1),
            "bg": display["bg"],
            "fg": display["fg"],
            "itm": display["itm"],
            "itm_amount": display["itm_amount"],
            "opt_str": display["opt_str"],
            "theta_str": display["theta_str"],
            "theta_dollars": display["theta_dollars"],
            "theta_norm": round(display["theta_dollars"] / display["margin"] * 10, 1)
                          if display["theta_dollars"] is not None and display["margin"] else None,
            "is_stock_row": display["is_stock_row"],
            "is_profitable": display["is_profitable"],
            "delta": display["delta"],
            "show_merge": show_merge,
            "merge_key": list(merge_key),
        })

    avail = (max_margin / 1000) * multiplier - total_margin

    return jsonify({
        "positions": items,
        "summary": {
            "total_margin": round(total_margin, 1),
            "avail_margin": round(avail, 1),
            "total_theta": round(total_theta_day),
        },
        "fetch_errors": _cache.fetch_errors(),
    })


@app.route("/api/prices")
def api_prices():
    """Phase 2: fetch live market data and return per-position pricing updates.

    This is the slow call that hits Yahoo Finance / Massive.  The client calls
    it after the position table is already rendered, using the progress bar to
    show which symbol is being fetched.  Returns only the price-dependent fields
    keyed by position id so the client can merge them without a full re-render.
    """
    positions = pos_repo.get_open_positions()
    _cache.fetch_all(positions)

    updates: dict[int, dict] = {}
    total_theta_day = 0.0

    for pos in positions:
        display = _compute_display(pos, _cache)
        if display["theta_dollars"] is not None:
            total_theta_day += display["theta_dollars"]
        stock_price = _cache.price(pos.symbol)
        updates[pos.id] = {
            "price":         round(stock_price, 2) if stock_price is not None else None,
            "itm":           display["itm"],
            "itm_amount":    display["itm_amount"],
            "opt_str":       display["opt_str"],
            "theta_str":     display["theta_str"],
            "theta_dollars": display["theta_dollars"],
            "theta_norm":    round(display["theta_dollars"] / display["margin"] * 10, 1)
                             if display["theta_dollars"] is not None and display["margin"] else None,
            "is_profitable": display["is_profitable"],
            "delta":         display["delta"],
        }

    return jsonify({
        "updates": updates,
        "total_theta": round(total_theta_day),
        "fetch_errors": _cache.fetch_errors(),
    })


@app.route("/api/price/<symbol>")
def api_price(symbol: str):
    """Debug endpoint — requires MARGIN_DEBUG=1."""
    if not _debug_enabled():
        return jsonify({"error": "not found"}), 404
    sym = symbol.upper()
    try:
        from option_lib.data_provider import get_provider
        info = get_provider().get_stock_info(sym)
        price = info.get("current_price") if info.get("success") else None
        return jsonify({"symbol": sym, "price": price})
    except Exception as e:
        return jsonify({"symbol": sym, "price": None, "error": str(e)})


@app.route("/api/optprice/<symbol>/<expiration>/<strike>/<otype>")
def api_optprice(symbol: str, expiration: str, strike: str, otype: str):
    """Debug endpoint — requires MARGIN_DEBUG=1.

    Example: /api/optprice/AAPL/2025-06-20/200/PUT
    """
    if not _debug_enabled():
        return jsonify({"error": "not found"}), 404
    sym = symbol.upper()
    ot  = otype.upper()
    try:
        k = float(strike)
    except ValueError:
        return jsonify({"error": f"invalid strike: {strike}"}), 400
    try:
        from option_lib.data_provider import get_provider
        p = get_provider()
        price = p.fetch_option_theoretical_price(sym, expiration, k, ot)
        theta = p.fetch_option_theta(sym, expiration, k, ot)
        return jsonify({"symbol": sym, "expiration": expiration,
                        "strike": k, "option_type": ot,
                        "price": price, "theta": theta})
    except Exception as e:
        return jsonify({"symbol": sym, "expiration": expiration,
                        "strike": k, "option_type": ot,
                        "price": None, "theta": None, "error": str(e)})


@app.route("/api/fetch-progress")
def api_fetch_progress():
    """Return the symbol currently being fetched, for live loading status."""
    return jsonify({"symbol": _cache.current_fetch})


@app.route("/api/refresh", methods=["POST"])
def api_refresh():
    _cache.__init__()
    return jsonify({"ok": True})


@app.route("/api/clear-errors", methods=["POST"])
def api_clear_errors():
    """Clear accumulated fetch errors so they stop appearing after the user dismisses them."""
    _cache._failed.clear()
    return jsonify({"ok": True})


@app.route("/api/positions/merge", methods=["POST"])
def api_merge_positions():
    d = request.json
    pos_repo.merge_stock_positions(
        d["symbol"],
        d["expiration"] or constants.NO_EXPIRATION,
        float(d["strike"]),
    )
    return jsonify({"ok": True})


@app.route("/api/positions/<int:row_id>")
def api_get_position(row_id: int):
    pos = pos_repo.get_position(row_id)
    if not pos:
        return jsonify({"error": "not found"}), 404
    d = dataclasses.asdict(pos)
    if d.get("expiration") == constants.NO_EXPIRATION:
        d["expiration"] = None
    return jsonify(d)


@app.route("/api/positions", methods=["POST"])
def api_add_position():
    d = request.get_json(silent=True)
    if not d:
        return jsonify({"error": "missing or invalid JSON body"}), 400
    _normalize_position_data(d)
    pos_repo.insert_position(d)
    symbol = d["symbol"]
    _cache.invalidate(symbol)
    threading.Thread(target=_prefetch_symbol, args=(symbol,), daemon=True).start()
    return jsonify({"ok": True})


@app.route("/api/positions/<int:row_id>", methods=["PUT"])
def api_update_position(row_id: int):
    d = request.get_json(silent=True)
    if not d:
        return jsonify({"error": "missing or invalid JSON body"}), 400
    _normalize_position_data(d)
    pos_repo.update_position(row_id, d)
    symbol = d["symbol"]
    _cache.invalidate(symbol)
    threading.Thread(target=_prefetch_symbol, args=(symbol,), daemon=True).start()
    return jsonify({"ok": True})


@app.route("/api/positions/<int:row_id>", methods=["DELETE"])
def api_delete_position(row_id: int):
    pos_repo.delete_position(row_id)
    return jsonify({"ok": True})


def _normalize_position_data(d: dict) -> None:
    d["symbol"] = str(d.get("symbol", "")).strip().upper()
    d["strike"] = float(d.get("strike") or 0)
    d["quantity"] = int(d.get("quantity") or 1)
    d["long_shares"] = int(d["long_shares"]) if d.get("long_shares") else None
    d["long_cost"] = float(d["long_cost"]) if d.get("long_cost") else None
    d["strike2"] = float(d["strike2"]) if d.get("strike2") else None
    # Straddle: put strike defaults to call strike (true straddle if not specified)
    if d.get("option_type") == "STRADDLE" and not d.get("strike2"):
        d["strike2"] = d["strike"]
    if not d.get("expiration"):
        d["expiration"] = constants.NO_EXPIRATION


# ---------------------------------------------------------------------------
# Config API
# ---------------------------------------------------------------------------

@app.route("/api/config")
def api_get_config():
    return jsonify(cfg_repo.load())


@app.route("/api/extended-hours", methods=["POST"])
def api_set_extended_hours():
    """Toggle extended-hours pricing without a full config save.

    Clears cached stock prices so the next /api/prices call re-fetches
    with the new setting.  Not persisted — resets to False on login.
    """
    d = request.get_json(silent=True) or {}
    _cache.set_extended_hours(bool(d.get("enabled", False)))
    return jsonify({"ok": True})


@app.route("/api/config", methods=["POST"])
def api_save_config():
    d = request.get_json(silent=True) or {}
    try:
        margin        = int(d["MaximumMarginBasis"])
        multiplier    = float(d["MarginMultiplier"])
        risk_free_pct = float(d["RiskFreeRate"])
    except (ValueError, KeyError, TypeError):
        return jsonify({"error": "invalid values"}), 400
    if not (0.5 <= multiplier <= 4.0):
        return jsonify({"error": "Multiplier must be 0.5–4.0"}), 400
    if not (0.0 <= risk_free_pct <= 20.0):
        return jsonify({"error": "Risk-free rate must be 0–20%"}), 400
    use_extended = bool(d.get("UseExtendedHours", False))
    cfg_repo.save(margin, multiplier, risk_free_pct)
    _cache._r = risk_free_pct / 100.0        # take effect on the next cache refresh
    _cache.set_extended_hours(use_extended)  # session-only; not persisted to config
    sort = d.get("SortOrder")
    if sort:
        cfg_repo.save_sort(sort)
    return jsonify({"ok": True})


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------

_EXPORT_HEADERS = ["Position", "Price", "Margin ($k)", "Qty", "Position Theta ($)", "Expiration", "Per-Share Theta"]


@app.route("/export")
def export_page():
    positions = sorted(
        pos_repo.get_open_positions(),
        key=lambda r: (r.symbol, r.expiration or "", r.strike or 0.0),
    )
    _cache.fetch_all(positions)
    rows = _build_csv_rows(positions)
    return render_template("export.html", headers=_EXPORT_HEADERS, rows=rows)


@app.route("/export/csv")
def export_csv():
    positions = sorted(
        pos_repo.get_open_positions(),
        key=lambda r: (r.symbol, r.expiration or "", r.strike or 0.0),
    )
    _cache.fetch_all(positions)
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(_EXPORT_HEADERS)
    writer.writerows(_build_csv_rows(positions))
    output.seek(0)
    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=positions.csv"},
    )


@app.route("/export/xlsx")
def export_xlsx():
    from services.export_service import build_workbook
    import tempfile, os
    positions = sorted(
        pos_repo.get_open_positions(),
        key=lambda r: (r.symbol, r.expiration or "", r.strike or 0.0),
    )
    _cache.fetch_all(positions)
    wb, _ = build_workbook(positions, _cache)
    with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as f:
        tmp_path = f.name
    try:
        wb.save(tmp_path)
        with open(tmp_path, "rb") as f:
            data = f.read()
    finally:
        os.unlink(tmp_path)
    return Response(
        data,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=positions.xlsx"},
    )


def _build_csv_rows(positions: list) -> list[list]:
    rows = []
    for pos in positions:
        gf = f'=GOOGLEFINANCE("{pos.symbol}")'
        if ps.is_stock(pos):
            stock_label = f"{pos.symbol} stock ({pos.long_shares or 0} sh)"
            stock_margin = round(ps.margin_k(pos), 2)
            if ps.has_covered_call(pos):
                rows.append([stock_label, gf, stock_margin, pos.long_shares or 0, "", "", ""])
                key = (pos.symbol, pos.expiration, pos.strike, "CALL")
                raw_theta = _cache.theta(key)
                theta_dollars = round(-raw_theta * pos.quantity * 100, 2) if raw_theta is not None else ""
                rows.append([
                    ps.position_abbrev(pos),
                    gf,
                    0,
                    pos.quantity,
                    theta_dollars,
                    pos.expiration or "",
                    round(raw_theta, 4) if raw_theta is not None else "",
                ])
            else:
                rows.append([stock_label, gf, stock_margin, pos.long_shares or 0, "", "", ""])
        elif ps.is_straddle(pos):
            put_strike = pos.strike2
            call_key = (pos.symbol, pos.expiration, pos.strike,  'CALL')
            put_key  = (pos.symbol, pos.expiration, put_strike,  'PUT')
            call_theta = _cache.theta(call_key)
            put_theta  = _cache.theta(put_key)
            call_abbrev, put_abbrev = ps.straddle_leg_abbrevs(pos)
            rows.append([
                call_abbrev, gf, round(ps.margin_k(pos), 2), pos.quantity,
                round(-call_theta * pos.quantity * 100, 2) if call_theta is not None else "",
                pos.expiration or "",
                round(call_theta, 4) if call_theta is not None else "",
            ])
            rows.append([
                put_abbrev, gf, 0, pos.quantity,
                round(-put_theta * pos.quantity * 100, 2) if put_theta is not None else "",
                pos.expiration or "",
                round(put_theta, 4) if put_theta is not None else "",
            ])
        elif ps.is_spread(pos):
            ot = ps.pricing_option_type(pos)
            short_key = (pos.symbol, pos.expiration, pos.strike, ot)
            long_key  = (pos.symbol, pos.expiration, pos.strike2, ot)
            short_theta = _cache.theta(short_key)
            long_theta  = _cache.theta(long_key)
            short_abbrev, long_abbrev = ps.spread_leg_abbrevs(pos)
            short_td = round(-short_theta * pos.quantity * 100, 2) if short_theta is not None else ""
            long_td  = round(long_theta  * pos.quantity * 100, 2) if long_theta  is not None else ""
            rows.append([
                short_abbrev, gf,
                round(ps.margin_k(pos), 2),
                pos.quantity,
                short_td,
                pos.expiration or "",
                round(short_theta, 4) if short_theta is not None else "",
            ])
            rows.append([
                long_abbrev, gf,
                0,
                pos.quantity,
                long_td,
                pos.expiration or "",
                round(long_theta, 4) if long_theta is not None else "",
            ])
        else:
            ot = ps.pricing_option_type(pos)
            key = (pos.symbol, pos.expiration, pos.strike, ot)
            raw_theta = _cache.theta(key) if pos.strike else None
            theta_dollars = round(-raw_theta * pos.quantity * 100, 2) if raw_theta is not None else ""
            rows.append([
                ps.position_abbrev(pos),
                gf,
                round(ps.margin_k(pos), 2),
                pos.quantity,
                theta_dollars,
                pos.expiration or "",
                round(raw_theta, 4) if raw_theta is not None else "",
            ])
    return rows


if __name__ == "__main__":
    # threaded=True lets the dev server handle /api/fetch-progress polls
    # concurrently while /api/prices is blocking on market-data fetches.
    app.run(debug=True, host='0.0.0.0', port=constants.WEB_PORT, threaded=True)
