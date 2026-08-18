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
import hmac
import io
import logging
import os
import threading
from datetime import date, datetime, timedelta

logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)

from flask import (Flask, Response, jsonify, redirect, render_template,
                   request, session, send_from_directory, url_for)

import constants
import db
import repositories.config_repository as cfg_repo
import repositories.portfolios_repository as pf_repo
import repositories.positions_repository as pos_repo
import services.position_service as ps
import ui_styles as styles
from models import Portfolio, Position
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
_cache = CacheService(r=_r_default)   # extended-hours mode follows the clock

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
        opt_str = ps.format_opt_price(net_opt)
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
        # Only one leg of a straddle can be ITM; that leg's price is the one the
        # ITM badge's intrinsic/time split refers to.
        if price is None:
            itm_leg_opt = None
        elif price > pos.strike:
            itm_leg_opt = call_opt
        elif put_strike is not None and price < put_strike:
            itm_leg_opt = put_opt
        else:
            itm_leg_opt = None

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
            opt_str = ps.format_opt_price(net_opt)
            td = ps.theta_dollars(pos, theta, long_theta)
            short_line, long_line = ps.spread_leg_abbrevs(pos)
            abbrev, abbrev2 = (short_line, long_line) if ps.is_credit_spread(pos) else (long_line, short_line)
        else:
            abbrev2 = None
            opt_str = ps.format_opt_price(opt_price)
            td = ps.theta_dollars(pos, theta)
            abbrev = ps.position_abbrev(pos)

        delta = cache.delta(key) if pos.strike else None
        # The ITM badge always describes the short leg, so its price — not the
        # spread's net — is what the intrinsic/time split is taken from.
        itm_leg_opt = opt_price

    days = ps.days_to_expiry(pos)
    bg = styles.expiry_color(days)

    earnings = cache.earnings_date(pos.symbol)
    after_earnings = False
    if earnings and pos.expiration and pos.expiration != constants.NO_EXPIRATION:
        try:
            today = date.today()
            after_earnings = today < date.fromisoformat(earnings) <= date.fromisoformat(pos.expiration)
        except ValueError:
            pass

    # Intrinsic and extrinsic halves of the ITM leg's premium, per share.  A
    # small negative time value is real — deep ITM American options trade at or
    # just under parity — so it is reported rather than clamped to zero.
    # An implausible leg price makes the split meaningless too, so drop it
    # rather than report a time value derived from a blown-up quote.
    itm_amt = ps.itm_amount(pos, price)
    time_prem = (round(itm_leg_opt - itm_amt, 2)
                 if ps.plausible_opt_price(itm_leg_opt) and itm_amt is not None else None)

    return {
        "abbrev": abbrev,
        "abbrev2": abbrev2,
        "qty": ps.display_quantity(pos),
        "margin": ps.margin_k(pos),
        "bg": bg,
        "fg": styles.text_color(bg),
        "itm": ps.is_itm(pos, price),
        "itm_amount": itm_amt,
        "time_premium": time_prem,
        "opt_str": opt_str,
        "theta_dollars": td,
        "theta_str": f"${round(td):,d}" if td is not None else "—",
        "is_stock_row": ps.is_stock(pos),
        "is_profitable": ps.is_profitable(pos, price),
        "delta": round(delta, 3) if delta is not None else None,
        "after_earnings": after_earnings,
        "earnings_date": earnings if after_earnings else None,
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
    if request.endpoint == "api_snapshot":
        # Token-authenticated (see _snapshot_authorized); no cookie session.
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
    return render_template("index.html", version=constants.__version__)


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


def _portfolio_summary(portfolios: list[Portfolio], items: list[dict]) -> dict:
    """Total / available margin for the whole book and for each portfolio.

    Available margin is capacity (max_margin × multiplier) less the margin in
    use; the "All" figure is the sum of every portfolio's capacity less every
    position's margin, so it is the aggregate rather than any one account.
    """
    used: dict[int, float] = {}
    counts: dict[int, int] = {}
    for item in items:
        pid = item["portfolio_id"]
        used[pid] = used.get(pid, 0.0) + item["margin"]
        counts[pid] = counts.get(pid, 0) + 1
    rows = []
    for pf in portfolios:
        rows.append({
            "id": pf.id,
            "name": pf.name,
            "abbrev": pf.abbrev,
            "is_default": pf.is_default,
            "max_margin": pf.max_margin,
            "multiplier": pf.multiplier,
            "position_count": counts.get(pf.id, 0),
            "total_margin": round(used.get(pf.id, 0.0), 1),
            "avail_margin": round(pf.capacity_k - used.get(pf.id, 0.0), 1),
        })
    total_margin = sum(used.values())
    capacity = sum(pf.capacity_k for pf in portfolios)
    return {
        "total_margin": round(total_margin, 1),
        "avail_margin": round(capacity - total_margin, 1),
        "portfolios": rows,
    }


def _build_items(positions: list) -> tuple[list[dict], dict]:
    """Build the per-position rows plus the summary block from warm cache data.

    Shared by the browser's phase-1 call and the token-authenticated snapshot
    endpoint so both describe a position identically.
    """
    portfolios = pf_repo.list_portfolios()
    by_id = {pf.id: pf for pf in portfolios}
    default_pf = next((pf for pf in portfolios if pf.is_default), portfolios[0])

    mergeable_groups = ps.mergeable_stock_groups(positions)
    seen_merge_groups: set[tuple] = set()

    items = []
    total_theta_day = 0.0

    for pos in positions:
        display = _compute_display(pos, _cache)
        if display["theta_dollars"] is not None:
            total_theta_day += display["theta_dollars"]

        merge_key = ps.merge_key(pos)
        can_merge = ps.is_stock(pos) and merge_key in mergeable_groups
        show_merge = False
        if can_merge and merge_key not in seen_merge_groups:
            show_merge = True
            seen_merge_groups.add(merge_key)

        # A position whose portfolio is unknown counts against the default —
        # the summary must never lose margin down a crack.
        pf = by_id.get(getattr(pos, "portfolio_id", None), default_pf)

        exp_display = pos.expiration if pos.expiration != constants.NO_EXPIRATION else None
        stock_price = _cache.price(pos.symbol)
        items.append({
            "id": pos.id,
            "portfolio_id": pf.id,
            "portfolio": pf.name,
            "portfolio_abbrev": pf.abbrev,
            "symbol": pos.symbol,
            # Warm only — phase 1 never fetches, so this is None until the
            # prefetch or a /api/prices pass has run.  See api_prices().
            "sector": _cache.sector(pos.symbol),
            "company_name": _cache.company_name(pos.symbol),
            "price": round(stock_price, 2) if stock_price is not None else None,
            "price_session": _cache.price_session(pos.symbol),
            "price_age_s": _cache.price_age(pos.symbol),
            "price_extended": _cache.price_extended(pos.symbol),
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
            "time_premium": display["time_premium"],
            "opt_str": display["opt_str"],
            "theta_str": display["theta_str"],
            "theta_dollars": display["theta_dollars"],
            "theta_norm": round(display["theta_dollars"] / display["margin"] * 10, 1)
                          if display["theta_dollars"] is not None and display["margin"] else None,
            "is_stock_row": display["is_stock_row"],
            "is_profitable": display["is_profitable"],
            "delta": display["delta"],
            "after_earnings": display["after_earnings"],
            "earnings_date":  display["earnings_date"],
            "show_merge": show_merge,
            "merge_key": list(merge_key),
        })

    summary = _portfolio_summary(portfolios, items)
    summary["total_theta"] = round(total_theta_day)
    return items, summary


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
    items, summary = _build_items(_sorted_positions(sort))
    return jsonify({
        "positions": items,
        "summary": summary,
        "fetch_errors": _cache.fetch_errors(),
    })


# ---------------------------------------------------------------------------
# Snapshot API (for external dashboards such as Glance)
# ---------------------------------------------------------------------------

def _snapshot_authorized() -> bool:
    """Accept the site password as a bearer token or API-key header.

    Headers only — a ``?key=`` query parameter would land in access logs.
    """
    supplied = ""
    auth = request.headers.get("Authorization", "")
    if auth.lower().startswith("bearer "):
        supplied = auth[7:].strip()
    if not supplied:
        supplied = request.headers.get("X-Api-Key", "")
    return bool(supplied) and hmac.compare_digest(supplied, _password)


def _weekly_summary(items: list[dict]) -> list[dict]:
    """Roll the position rows up by expiration week, soonest first.

    Each bucket is labelled by the Friday of its ISO week ("Aug 21"), so a
    Thursday expiry in a holiday week still lands with the rest of that week.
    Positions with no expiration (uncovered stock) are not counted.
    """
    buckets: dict[date, dict] = {}
    for item in items:
        exp = item.get("expiration")
        # Uncovered stock never expires, whatever date the row happens to carry.
        if not exp or (item.get("is_stock_row") and not item.get("strike")):
            continue
        try:
            d = date.fromisoformat(exp)
        except ValueError:
            continue
        friday = d + timedelta(days=4 - d.weekday())
        b = buckets.setdefault(friday, {
            "week_ending": friday.isoformat(),
            "week_label": f"{friday.strftime('%b')} {friday.day}",
            "position_count": 0,
            "total_margin": 0.0,
            "itm_count": 0,
        })
        b["position_count"] += 1
        b["total_margin"] += item["margin"] or 0.0
        if item["itm"]:
            b["itm_count"] += 1
    out = []
    for friday in sorted(buckets):
        b = buckets[friday]
        b["total_margin"] = round(b["total_margin"], 1)
        out.append(b)
    return out


@app.route("/api/snapshot")
def api_snapshot():
    """Everything the main page shows, fully priced, in one JSON document.

    Authentication is by the MARGIN_PWD password rather than a login session,
    sent in a header (never a query parameter, which would be logged):
        Authorization: Bearer <password>   (preferred)
        X-Api-Key: <password>

    By default this refreshes any stale market data first (same as the browser's
    phase-2 call), so it can take a few seconds when the cache is cold.  Pass
    ?cached=1 to return whatever is already in the cache without fetching.
    ?sort=alpha|type|expiration overrides the configured sort order.
    """
    if not _snapshot_authorized():
        return jsonify({"error": "unauthorized"}), 401

    if request.args.get("cached") not in ("1", "true", "yes"):
        _cache.fetch_all(pos_repo.get_open_positions())

    config = cfg_repo.load()
    sort = request.args.get("sort", config.get("SortOrder", "alpha"))
    items, summary = _build_items(_sorted_positions(sort))
    for item in items:
        item.pop("show_merge", None)
        item.pop("merge_key", None)

    return jsonify({
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "version": constants.__version__,
        "positions": items,
        "summary": summary,
        "weekly_summary": _weekly_summary(items),
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
            # Resent every pass even though it never changes: on a cold start
            # phase 1 had nothing to send, and this is the only payload that
            # runs after the fetch.  It is one short string per row.
            "sector":        _cache.sector(pos.symbol),
            "company_name":  _cache.company_name(pos.symbol),
            "price":         round(stock_price, 2) if stock_price is not None else None,
            "price_session": _cache.price_session(pos.symbol),
            "price_age_s":   _cache.price_age(pos.symbol),
            "price_extended": _cache.price_extended(pos.symbol),
            "itm":           display["itm"],
            "itm_amount":    display["itm_amount"],
            "time_premium":  display["time_premium"],
            "opt_str":       display["opt_str"],
            "theta_str":     display["theta_str"],
            "theta_dollars": display["theta_dollars"],
            "theta_norm":    round(display["theta_dollars"] / display["margin"] * 10, 1)
                             if display["theta_dollars"] is not None and display["margin"] else None,
            "is_profitable":  display["is_profitable"],
            "delta":          display["delta"],
            "after_earnings": display["after_earnings"],
            "earnings_date":  display["earnings_date"],
        }

    return jsonify({
        "updates": updates,
        "total_theta": round(total_theta_day),
        "fetch_errors": _cache.fetch_errors(),
    })


@app.route("/api/quote/<symbol>")
def api_quote(symbol: str):
    """Return the cached (or freshly fetched) stock price and earnings date for use in the add-position form."""
    sym = symbol.strip().upper()
    price = _cache.fetch_price(sym)
    earnings = _cache.fetch_earnings_date(sym)
    return jsonify({"symbol": sym, "price": price, "earnings_date": earnings})


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
        int(d["portfolio_id"]) if d.get("portfolio_id") else None,
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
    if d["portfolio_id"] is not None and pf_repo.get_portfolio(d["portfolio_id"]) is None:
        return jsonify({"error": "unknown portfolio"}), 400
    pos_repo.insert_position(d)
    # The Add form's portfolio choice sticks: picking a portfolio there makes
    # it the default, so the user can work one account at a time.
    if d["portfolio_id"] is not None and d["portfolio_id"] != pf_repo.get_default().id:
        pf_repo.set_default(d["portfolio_id"])
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
    if d["portfolio_id"] is not None and pf_repo.get_portfolio(d["portfolio_id"]) is None:
        return jsonify({"error": "unknown portfolio"}), 400
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
    d["portfolio_id"] = int(d["portfolio_id"]) if d.get("portfolio_id") else None
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


@app.route("/api/config", methods=["POST"])
def api_save_config():
    d = request.get_json(silent=True) or {}
    try:
        risk_free_pct = float(d["RiskFreeRate"])
    except (ValueError, KeyError, TypeError):
        return jsonify({"error": "invalid values"}), 400
    if not (0.0 <= risk_free_pct <= 20.0):
        return jsonify({"error": "Risk-free rate must be 0–20%"}), 400
    cfg_repo.save(risk_free_pct)
    _cache._r = risk_free_pct / 100.0        # take effect on the next cache refresh
    sort = d.get("SortOrder")
    if sort:
        cfg_repo.save_sort(sort)
    return jsonify({"ok": True})


# ---------------------------------------------------------------------------
# Portfolios API
# ---------------------------------------------------------------------------

def _portfolio_dict(pf: Portfolio) -> dict:
    return {
        "id": pf.id,
        "name": pf.name,
        "abbrev": pf.abbrev,
        "max_margin": pf.max_margin,
        "multiplier": pf.multiplier,
        "is_default": pf.is_default,
    }


@app.route("/api/portfolios")
def api_list_portfolios():
    return jsonify({
        "portfolios": [_portfolio_dict(pf) for pf in pf_repo.list_portfolios()],
        "max_portfolios": pf_repo.MAX_PORTFOLIOS,
    })


@app.route("/api/portfolios", methods=["POST"])
def api_add_portfolio():
    d = request.get_json(silent=True) or {}
    try:
        pid = pf_repo.create(d.get("name", ""), d.get("max_margin"), d.get("multiplier"),
                             make_default=bool(d.get("is_default")))
    except pf_repo.PortfolioError as e:
        return jsonify({"error": str(e)}), 400
    return jsonify({"ok": True, "id": pid})


@app.route("/api/portfolios/<int:pid>", methods=["PUT"])
def api_update_portfolio(pid: int):
    d = request.get_json(silent=True) or {}
    try:
        pf_repo.update(pid, d.get("name", ""), d.get("max_margin"), d.get("multiplier"),
                       make_default=bool(d.get("is_default")))
    except pf_repo.PortfolioError as e:
        return jsonify({"error": str(e)}), 400
    return jsonify({"ok": True})


@app.route("/api/portfolios/<int:pid>/default", methods=["POST"])
def api_default_portfolio(pid: int):
    try:
        pf_repo.set_default(pid)
    except pf_repo.PortfolioError as e:
        return jsonify({"error": str(e)}), 400
    return jsonify({"ok": True})


@app.route("/api/portfolios/<int:pid>", methods=["DELETE"])
def api_delete_portfolio(pid: int):
    """Delete a portfolio; its positions move to the default portfolio."""
    try:
        moved = pf_repo.delete(pid)
    except pf_repo.PortfolioError as e:
        return jsonify({"error": str(e)}), 400
    return jsonify({"ok": True, "moved": moved})


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------

_EXPORT_HEADERS = ["Portfolio", "Position", "Price", "Margin ($k)", "Qty", "Position Theta ($)", "Expiration", "Per-Share Theta"]


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
    names = {pf.id: pf.name for pf in pf_repo.list_portfolios()}
    rows = []
    for pos in positions:
        gf = f'=GOOGLEFINANCE("{pos.symbol}")'
        pf_name = names.get(getattr(pos, "portfolio_id", None), "")
        rows_before = len(rows)
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
        for i in range(rows_before, len(rows)):
            rows[i].insert(0, pf_name)
    return rows


if __name__ == "__main__":
    # threaded=True lets the dev server handle /api/fetch-progress polls
    # concurrently while /api/prices is blocking on market-data fetches.
    app.run(debug=True, host='0.0.0.0', port=constants.WEB_PORT, threaded=True)
