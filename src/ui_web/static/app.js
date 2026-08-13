'use strict';

let _positions = [];
let _colSort = null;   // { col: string, dir: 'asc'|'desc' } | null
let _editId = null;    // null = adding new, number = editing existing
let _posModal = null;
let _confirmModal = null;
let _progressPoller = null;    // interval handle for fetch-progress polling
let _loadCtrl = null;          // AbortController for the in-flight loadPositions() (both phases)
let _loadSeq  = 0;             // generation counter — only the newest load may touch the table
let _sortQueued = false;       // sort changed mid-load: re-sort once the pull finishes
let _lastFetchSymbol = '';     // symbol the progress poller last reported

// ---------------------------------------------------------------------------
// App-wide state
// ---------------------------------------------------------------------------
// Earnings date (YYYY-MM-DD) for the symbol currently entered in the Add form.
let _earningsDate = null;

// ---------------------------------------------------------------------------
// Init
// ---------------------------------------------------------------------------

document.addEventListener('DOMContentLoaded', async () => {
    _posModal     = new bootstrap.Modal(document.getElementById('positionModal'));
    _confirmModal = new bootstrap.Modal(document.getElementById('confirmModal'));

    buildLegend();
    await loadConfig();
    loadPositions();

    document.querySelectorAll('input[name="sort"]').forEach(r =>
        r.addEventListener('change', () => {
            _colSort = null;
            updateColHeaders();
            // A price pull already running must not be thrown away — every
            // symbol it has fetched would have to be fetched again. Queue the
            // reload instead; loadPositions() reads the radio when it starts,
            // so the drain picks up whatever is selected by then.
            if (_loadCtrl) {
                _sortQueued = true;
                _setFetchStatus(_loadingLabel(_lastFetchSymbol));
                return;
            }
            loadPositions();
        })
    );

    document.querySelectorAll('#positionsTable thead th.sortable').forEach(th =>
        th.addEventListener('click', () => {
            const col = th.dataset.col;
            if (_colSort?.col === col) {
                _colSort = _colSort.dir === 'asc' ? { col, dir: 'desc' } : null;
            } else {
                _colSort = { col, dir: 'asc' };
            }
            updateColHeaders();
            renderTable();
        })
    );

    document.getElementById('fSymbol').addEventListener('input', function () {
        const pos = this.selectionStart;
        this.value = this.value.toUpperCase();
        this.setSelectionRange(pos, pos);
    });

    document.getElementById('fSymbol').addEventListener('blur', async function () {
        if (_editId !== null) return;
        const ot = document.getElementById('fType').value;
        if (ot === 'STOCK') return;
        const sym = this.value.trim().toUpperCase();
        if (!sym) return;
        const strikeEl = document.getElementById('fStrike');
        try {
            const resp = await fetch(`/api/quote/${sym}`);
            if (!resp.ok) return;
            const data = await resp.json();
            if (data.price && (!strikeEl.value || parseFloat(strikeEl.value) === 0))
                strikeEl.value = Math.round(data.price * 0.97);
            _earningsDate = data.earnings_date || null;
            updateEarningsWarning();
        } catch (_) { /* ignore fetch errors */ }
    });

    document.getElementById('fStrike').addEventListener('keydown', function (e) {
        if (e.key !== '-' && e.key !== '=') return;
        e.preventDefault();
        const val = parseFloat(this.value) || 0;
        this.value = Math.max(0, val + (e.key === '=' ? 1 : -1));
    });

    document.getElementById('fQty').addEventListener('keydown', function (e) {
        if (e.key !== '-' && e.key !== '=') return;
        e.preventDefault();
        const val = parseInt(this.value) || 1;
        this.value = Math.max(1, val + (e.key === '=' ? 1 : -1));
    });

    document.getElementById('fShares').addEventListener('keydown', function (e) {
        if (e.key !== '-' && e.key !== '=' && e.key !== 'ArrowUp' && e.key !== 'ArrowDown') return;
        e.preventDefault();
        const val = parseInt(this.value) || 0;
        const delta = (e.key === '=' || e.key === 'ArrowUp') ? 100 : -100;
        this.value = Math.max(0, val + delta);
    });

    // -/= step one day, </> step one week.
    document.getElementById('fExpiration').addEventListener('keydown', function (e) {
        const days = { '-': -1, '=': 1, '<': -7, '>': 7 }[e.key];
        if (days === undefined) return;
        e.preventDefault();
        const next = shiftDateStr(this.value, days);
        if (next === null) return;
        this.value = next;
        // Assigning .value fires no 'change', so refresh the badge by hand.
        updateEarningsWarning();
    });

    document.getElementById('fExpiration').addEventListener('change', function () {
        updateEarningsWarning();
        if (document.getElementById('fType').value !== 'STOCK') return;
        if (!this.value) return;
        // Quantity = shares ÷ 100 (contracts to cover the position)
        const shares = parseInt(document.getElementById('fShares').value) || 0;
        if (shares > 0) document.getElementById('fQty').value = Math.round(shares / 100);
        // Strike = nearest dollar at or above long cost (covered call default)
        const strikeEl = document.getElementById('fStrike');
        if (!strikeEl.value || parseFloat(strikeEl.value) === 0) {
            const cost = parseFloat(document.getElementById('fCost').value) || 0;
            if (cost > 0) strikeEl.value = Math.ceil(cost);
        }
    });

    document.getElementById('confirmModal').addEventListener('hide.bs.modal', () => {
        if (document.activeElement?.closest('#confirmModal')) document.activeElement.blur();
    });

    document.getElementById('fetchErrorDismiss').addEventListener('click', () => {
        const banner = document.getElementById('fetchErrorBanner');
        banner.classList.add('d-none');
        document.getElementById('fetchErrorList').innerHTML = '';
        fetch('/api/clear-errors', { method: 'POST' });   // stop re-reporting on next load
    });

    document.getElementById('btnAdd').addEventListener('click', openAddModal);
    document.getElementById('btnRefresh').addEventListener('click', refreshPrices);
    document.getElementById('btnSaveConfig').addEventListener('click', saveConfig);
    document.getElementById('positionForm').addEventListener('submit', savePosition);
    document.getElementById('fType').addEventListener('change', updateFormFields);
    document.getElementById('btnAssigned').addEventListener('click', applyAssigned);
    document.getElementById('btnClearCover').addEventListener('click', applyClearCover);

    // Touch: a tap outside the table dismisses a visible tooltip.
    if (_isTouch) {
        document.addEventListener('click', e => {
            if (!e.target.closest('#positionsTable tbody tr')) hideTooltip();
        });
    }
});

// ---------------------------------------------------------------------------
// Data loading
// ---------------------------------------------------------------------------

async function refreshPrices() {
    const btn = document.getElementById('btnRefresh');
    btn.disabled = true;
    try {
        await fetch('/api/refresh', { method: 'POST' });
        await loadPositions();
    } finally {
        btn.disabled = false;
    }
}

async function loadPositions() {
    const sort = document.querySelector('input[name="sort"]:checked').value;
    // This load asks the server for whatever sort is selected right now, so any
    // change queued before it started is already satisfied. Clearing here also
    // keeps an unrelated reload (a delete, say) from being followed by a second,
    // redundant one just to re-apply the same order.
    _sortQueued = false;

    // Supersede whatever load is still running: abort its requests and take a
    // new generation number, so a reply already on the wire is discarded rather
    // than applied. Both phases need this — a late phase-1 reply carries the
    // *previous* sort order and would silently reinstate it, which is the
    // classic "sort doesn't stick". Aborting alone is not enough: a request
    // that has already been answered still resolves.
    if (_loadCtrl) _loadCtrl.abort();
    _stopProgressPolling();
    const ctrl = new AbortController();
    const seq  = ++_loadSeq;
    _loadCtrl  = ctrl;
    const isCurrent = () => seq === _loadSeq;
    // Only the newest load may clear the shared handle — otherwise an older one
    // finishing late would drop the newer one's controller and make it
    // un-abortable.
    const release = () => { if (isCurrent()) _loadCtrl = null; };
    // Run a sort change that arrived while this load was in flight. Only the
    // newest load drains: a superseded one returns early and leaves the queue
    // for whichever load took over, so the sort is applied exactly once.
    const drain = () => {
        if (!isCurrent() || !_sortQueued) return;
        _sortQueued = false;
        loadPositions();
    };

    // ── Phase 1: positions from the database (fast) ──────────────────────────
    try {
        const resp = await fetch(`/api/positions?sort=${sort}`, { signal: ctrl.signal });
        if (resp.status === 401) { location.href = '/login'; return; }
        const data = await resp.json();
        if (!isCurrent()) return;            // a newer load owns the table now
        _positions = data.positions || [];   // guard: never assign undefined
        _stampFetchTimes(_positions);
        if (data.summary) updateSummary(data.summary);
        showFetchErrors(data.fetch_errors || []);
    } catch (e) {
        release();
        if (e.name === 'AbortError') return;  // superseded — silent
        console.error('[MarginWatch] loadPositions failed:', e);
        _setFetchStatus(`⚠ Load failed: ${e.message || e}`, true);
        drain();   // a queued sort must not be stranded by a failed load
        return;
    }
    renderTable();   // show the table immediately with whatever is cached

    // ── Phase 2: live market prices (slow, with progress bar) ────────────────
    _startProgressPolling();
    try {
        const resp = await fetch('/api/prices', { signal: ctrl.signal });
        if (resp.status === 401) { release(); _stopProgressPolling(); location.href = '/login'; return; }
        const data = await resp.json();
        if (!isCurrent()) return;
        release();
        // Merge price-dependent fields into the existing position objects.
        const upd = data.updates || {};
        for (const pos of _positions) {
            if (upd[pos.id]) Object.assign(pos, upd[pos.id]);
        }
        _stampFetchTimes(_positions);
        // Update theta in the summary (the only summary field that needs prices).
        const sumEl = document.getElementById('totalTheta');
        if (sumEl && data.total_theta != null)
            sumEl.textContent = `$${data.total_theta.toLocaleString()}/d`;
        showFetchErrors(data.fetch_errors || []);
    } catch (e) {
        release();
        if (e.name === 'AbortError') return;  // superseded by a newer loadPositions() — silent
        console.error('[MarginWatch] price fetch failed:', e);
        _stopProgressPolling();
        _setFetchStatus(`⚠ Price fetch failed: ${e.message || e}`, true);
        drain();   // a queued sort must not be stranded by a failed fetch
        return;
    }
    _stopProgressPolling();
    renderTable();   // re-render with live prices filled in
    _refreshTooltipText();   // a hover-triggered refresh updates the open tooltip
    drain();         // prices are in — now apply any sort that arrived meanwhile
}

/** Progress text: the symbol being fetched, plus a note when a sort change is
 *  waiting on this pull so the user knows the click was not swallowed. */
function _loadingLabel(symbol) {
    return (symbol ? `Loading ${symbol}…` : 'Loading…') +
           (_sortQueued ? '  (re-sorting when done)' : '');
}

function _startProgressPolling() {
    _lastFetchSymbol = '';
    _setFetchStatus(_loadingLabel(''));
    if (_progressPoller) clearInterval(_progressPoller);
    _progressPoller = setInterval(async () => {
        // Snapshot the handle so we can detect if the poller was stopped while
        // the fetch was in-flight and discard stale results.
        const handle = _progressPoller;
        try {
            const r = await fetch('/api/fetch-progress');
            if (!r.ok || _progressPoller !== handle) return;
            const { symbol } = await r.json();
            if (_progressPoller !== handle) return;   // stopped while parsing JSON
            _lastFetchSymbol = symbol || '';
            _setFetchStatus(_loadingLabel(_lastFetchSymbol));
        } catch { /* ignore poll errors */ }
    }, 300);
}

function _stopProgressPolling() {
    if (_progressPoller) { clearInterval(_progressPoller); _progressPoller = null; }
    _lastFetchSymbol = '';
    _setFetchStatus('');
}

function _setFetchStatus(msg, isError = false) {
    const el = document.getElementById('fetchStatus');
    if (!el) return;
    // The line keeps its height when empty (see .mw-status-line) — never hide
    // it, or the table below shifts every time a load starts and finishes.
    el.textContent = msg;
    el.classList.toggle('text-danger', isError);
    el.classList.toggle('text-muted', !isError);
}

function showFetchErrors(errors) {
    const banner = document.getElementById('fetchErrorBanner');
    const list   = document.getElementById('fetchErrorList');
    if (!banner || !list) return;
    if (!errors.length) {
        banner.classList.add('d-none');
        return;
    }
    list.innerHTML = errors.map(e => `<li>${e}</li>`).join('');
    banner.classList.remove('d-none');
}

async function loadConfig() {
    const resp = await fetch('/api/config');
    if (!resp.ok) return;
    const cfg = await resp.json();
    document.getElementById('cfgMargin').value = cfg.MaximumMarginBasis || 250000;
    document.getElementById('cfgMultiplier').value =
        parseFloat(cfg.MarginMultiplier || 1.5).toFixed(1);
    document.getElementById('cfgRiskFree').value =
        parseFloat(cfg.RiskFreeRate || 4.5).toFixed(1);
    const radio = document.querySelector(
        `input[name="sort"][value="${cfg.SortOrder || 'alpha'}"]`
    );
    if (radio) radio.checked = true;
}

// ---------------------------------------------------------------------------
// Summary
// ---------------------------------------------------------------------------

function updateSummary(s) {
    document.getElementById('totalMargin').textContent = `$${s.total_margin.toFixed(1)}k`;
    document.getElementById('totalTheta').textContent =
        `$${s.total_theta.toLocaleString()}/d`;
    const el = document.getElementById('availMargin');
    el.textContent = `$${s.avail_margin.toFixed(1)}k`;
    el.className = 'mw-val' + (s.avail_margin < 0 ? ' mw-danger' : '');
}

// ---------------------------------------------------------------------------
// Table rendering
// ---------------------------------------------------------------------------

/** Sort key for the $/shr column.
 *  The capped label carries a thousands separator, which parseFloat truncates
 *  at ("1,000+" would read as 1), so strip the commas first.  '—' and 'n/a'
 *  have no numeric value and sort to the bottom. */
function _optSortValue(optStr) {
    const n = parseFloat(String(optStr).replace(/,/g, ''));
    return isNaN(n) ? -Infinity : n;
}

function renderTable() {
    let items = [..._positions];

    if (_colSort) {
        const { col, dir } = _colSort;
        items.sort((a, b) => {
            let va, vb;
            if      (col === 'position')   { va = a.abbrev;       vb = b.abbrev; }
            // Unknown sectors group together at the end rather than under ''.
            else if (col === 'sector')     { va = a.sector || '￿';
                                             vb = b.sector || '￿'; }
            else if (col === 'qty')        { va = a.qty;          vb = b.qty; }
            else if (col === 'margin')     { va = a.margin;       vb = b.margin; }
            else if (col === 'opt')        { va = _optSortValue(a.opt_str);
                                             vb = _optSortValue(b.opt_str); }
            else if (col === 'theta')      { va = a.theta_dollars ?? -Infinity;
                                             vb = b.theta_dollars ?? -Infinity; }
            else if (col === 'theta_norm') { va = a.theta_norm ?? -Infinity;
                                             vb = b.theta_norm ?? -Infinity; }
            else return 0;  // unknown column — preserve existing order
            if (va < vb) return dir === 'asc' ? -1 : 1;
            if (va > vb) return dir === 'asc' ?  1 : -1;
            return 0;
        });
    }

    const tbody = document.getElementById('positionsBody');
    tbody.innerHTML = '';

    if (items.length === 0) {
        tbody.innerHTML =
            '<tr><td colspan="8" class="text-center text-muted py-3">No open positions.</td></tr>';
        return;
    }

    for (const [i, pos] of items.entries()) {
        const tr = document.createElement('tr');
        if ((i + 1) % ROW_RULE_INTERVAL === 0) tr.classList.add('mw-row-rule');
        tr.style.backgroundColor = pos.bg;
        tr.style.color = pos.fg;

        // Position cell: optional indicator swatches + name.  Classed so the
        // tooltip handler can tell it apart — it is the one column that shows
        // the company name rather than the underlier price.
        const posCell = document.createElement('td');
        posCell.className = 'mw-position-cell';

        // Risk indicator: coloured ball showing probability of assignment
        const rc = riskColor(pos.delta);
        if (rc !== null) {
            const risk = mkIndBadge('mw-ind', '', `δ ${(pos.delta * 100).toFixed(0)}%`);
            risk.style.backgroundColor = rc;
            posCell.appendChild(risk);
        }

        if (pos.itm) {
            const dot = mkIndBadge('mw-ind mw-ind-itm', 'I', _itmBadgeText(pos));
            // Yellow when barely ITM (< $1 or < 3% of strike) — close to the edge.
            // Green for covered calls clearly ITM (profitable assignment likely).
            // Red for short options clearly ITM (losing position).
            const barelyItm = pos.itm_amount != null && pos.strike != null &&
                              (pos.itm_amount < 1.0 || pos.itm_amount < 0.03 * pos.strike);
            dot.style.backgroundColor = barelyItm
                ? '#FACC15'
                : pos.is_stock_row ? COLOR_ITM_GOOD : COLOR_ITM_BAD;
            dot.style.color = barelyItm ? '#000' : '#fff';
            posCell.appendChild(dot);
            if (pos.itm_amount != null) {
                const lbl = document.createElement('span');
                lbl.className = 'mw-itm-inline';
                // intrinsic + time value, e.g. [5.00+1.20]
                lbl.textContent = pos.time_premium != null
                    ? `[${pos.itm_amount.toFixed(2)}${pos.time_premium < 0 ? '−' : '+'}` +
                      `${Math.abs(pos.time_premium).toFixed(2)}]`
                    : `[${pos.itm_amount.toFixed(2)}]`;
                posCell.appendChild(lbl);
            }
        }
        if (pos.is_profitable) {
            const arrow = document.createElement('span');
            arrow.className = 'mw-profit-arrow';
            arrow.textContent = ICON_PROFIT;
            posCell.appendChild(arrow);
        }
        if (pos.after_earnings) {
            let earnText;
            if (pos.earnings_date) {
                const [, m, d] = pos.earnings_date.split('-');
                earnText = `Earnings ${m}-${d}`;
            } else {
                earnText = 'Option expires after earnings';
            }
            posCell.appendChild(mkIndBadge('mw-ind mw-ind-earn', 'E', earnText));
        }
        const nameSpan = document.createElement('span');
        nameSpan.textContent = pos.abbrev;
        // The company name is shown by the cell's tooltip, not by a native
        // title here: a title would fire on top of the cell tooltip and put two
        // overlapping popups on screen saying different things.
        if (pos.is_stock_row) nameSpan.className = 'mw-stock-pos';
        posCell.appendChild(nameSpan);
        if (pos.abbrev2) {
            const line2 = document.createElement('div');
            line2.textContent = pos.abbrev2;
            line2.style.cssText = 'font-size:0.78em;opacity:0.75';
            posCell.appendChild(line2);
        }

        // No native title with the unabbreviated sector: this column is one of
        // the six that show the price, and a title would stack a second popup
        // on top of that one.
        const sectorCell = mkTd(sectorLabel(pos.sector), 'mw-sector-col');

        const qtyCell    = mkTd(pos.qty,                   'text-center');
        const marginCell = mkTd(pos.margin.toFixed(1),     'text-end');
        const optCell    = mkTd(pos.opt_str,               'text-end');
        const thetaCell     = mkTd(pos.theta_str,                                            'text-end');
        const thetaNormCell = mkTd(pos.theta_norm != null ? pos.theta_norm.toFixed(1) : '—', 'text-end');

        const actCell = document.createElement('td');
        actCell.className = 'text-center mw-actions-cell';

        const editBtn = mkRowBtn(ICON_EDIT,   () => editPosition(pos.id));
        const delBtn  = mkRowBtn(ICON_DELETE, () => deletePosition(pos.id));
        actCell.append(editBtn, delBtn);

        if (pos.show_merge) {
            const [sym, exp, strike] = pos.merge_key;
            const mergeBtn = mkRowBtn(ICON_MERGE, () => mergePositions(sym, exp, strike));
            actCell.appendChild(mergeBtn);
        }

        tr.append(posCell, sectorCell, qtyCell, marginCell, optCell, thetaCell, thetaNormCell, actCell);
        _addRowInteractions(tr, pos);
        tbody.appendChild(tr);
    }
}

// ---------------------------------------------------------------------------
// Row tooltips (hover on desktop, tap on mobile)
// ---------------------------------------------------------------------------
// Which column the pointer is over decides what the tooltip says: the company
// name over Position, the underlier price over the six data columns, nothing
// at all over the unlabelled actions column.  The two used to arrive together —
// a native title on the symbol plus the custom tooltip on the whole row — so
// hovering Position produced two overlapping popups saying different things.

const TIP_NAME  = 'name';    // company name — the Position column
const TIP_PRICE = 'price';   // underlier price — the six data columns

let _hoverTimer = null;   // hover delay timer handle
let _hideTimer  = null;   // auto-dismiss timer handle (touch)
let _tipPosId   = null;   // id of the position the tooltip is currently showing
let _tipKind    = null;   // TIP_NAME / TIP_PRICE — which datum, so a refresh
                          // that lands while it is open rewrites the right one

/** Which tooltip a cell owns, or null when it should stay quiet. */
function _cellTipKind(td) {
    if (!td)                                       return null;
    if (td.classList.contains('mw-actions-cell'))  return null;
    if (td.classList.contains('mw-position-cell')) return TIP_NAME;
    return TIP_PRICE;
}

/** Text for one kind of row tooltip, or null when there is nothing to say.
 *  The company name arrives with the price pass, so it is absent on a cold
 *  paint — and some symbols have no name at all. */
function _tipTextFor(pos, kind) {
    if (kind === TIP_NAME)  return pos.company_name || null;
    if (kind === TIP_PRICE) return _priceTipText(pos);
    return null;
}

/** Touch-style device: no hover, so the row/badge tooltips are tap-driven. */
const _isTouch = window.matchMedia('(hover: none)').matches;

/** True when the US market is outside its regular 9:30–16:00 ET session.
 *  Mirrors market_data_service.in_extended_hours(); ET is read from the
 *  Intl API so it stays correct regardless of the viewer's own timezone. */
function _isExtendedHoursNow() {
    const parts = new Intl.DateTimeFormat('en-US', {
        timeZone: 'America/New_York',
        weekday: 'short', hour: 'numeric', minute: 'numeric', hourCycle: 'h23',
    }).formatToParts(new Date());
    const get = t => parts.find(p => p.type === t).value;
    const weekday = get('weekday');
    if (weekday === 'Sat' || weekday === 'Sun') return true;
    const mins = parseInt(get('hour'), 10) * 60 + parseInt(get('minute'), 10);
    return mins < 9 * 60 + 30 || mins >= 16 * 60;
}

/** True when a position's cached price should be re-fetched: either it has aged
 *  past the TTL, or the market has crossed a session boundary since the fetch
 *  (in either direction, so the pre/post basis is re-derived). */
function _priceIsStale(pos) {
    if (pos._fetchedAt == null) return false;   // never priced — nothing to refresh
    if (Date.now() - pos._fetchedAt > STALE_PRICE_MS) return true;
    return pos.price_extended != null && pos.price_extended !== _isExtendedHoursNow();
}

/** Record when each position's price was fetched, from the server-supplied age.
 *  Sending an age rather than a timestamp keeps the two clocks independent. */
function _stampFetchTimes(positions) {
    const now = Date.now();
    for (const pos of positions) {
        pos._fetchedAt = pos.price_age_s == null ? null : now - pos.price_age_s * 1000;
    }
}

function _priceTipText(pos) {
    if (pos.price == null) return `${pos.symbol} —`;
    const suffix = pos.price_session === 'pre' ? ' (pre)'
                  : pos.price_session === 'post' ? ' (post)' : '';
    return `${pos.symbol} $${pos.price.toFixed(2)}${suffix}`;
}

/** Label for the ITM badge: how much of the short leg's premium is intrinsic
 *  (the ITM amount) and how much is still time value.  Both are per share.
 *  Time value goes negative on deep ITM American options trading under parity,
 *  so the sign is carried on the number rather than assumed positive. */
function _itmBadgeText(pos) {
    if (pos.itm_amount == null) return 'In the money';
    const itm = `ITM $${pos.itm_amount.toFixed(2)}`;
    if (pos.time_premium == null) return itm;
    const t = pos.time_premium;
    const sign = t < 0 ? '−' : '';
    return `${itm} · time ${sign}$${Math.abs(t).toFixed(2)}`;
}

/** Rewrite a visible tooltip after a refresh, so a hover that triggered the
 *  fetch shows the new price without the user moving the cursor. */
function _refreshTooltipText() {
    const tip = document.getElementById('posTooltip');
    if (_tipPosId == null || tip.style.display === 'none') return;
    const pos = _positions.find(p => p.id === _tipPosId);
    if (!pos) return;
    const text = _tipTextFor(pos, _tipKind);
    if (text != null) tip.textContent = text;
}

/** Show the tooltip a cell owns.  Returns false when it has nothing to say —
 *  an unnamed symbol, or the actions column. */
function showCellTooltip(pos, kind, clientX, clientY) {
    // A hover on a stale price kicks off a refresh; the tooltip shows what we
    // have now and _refreshTooltipText() updates it in place when the fetch
    // lands.  Only the price goes stale, so only the price rung triggers it.
    if (kind === TIP_PRICE && _priceIsStale(pos) && !_loadCtrl) loadPositions();
    const text = _tipTextFor(pos, kind);
    if (text == null) return false;
    showTipText(text, clientX, clientY);
    _tipPosId = pos.id;
    _tipKind  = kind;
    return true;
}

/** Show arbitrary text in the tooltip, anchored near (clientX, clientY). */
function showTipText(text, clientX, clientY) {
    _tipPosId = null;
    _tipKind  = null;
    const tip = document.getElementById('posTooltip');
    tip.textContent = text;
    tip.style.display = 'block';

    const tw = tip.offsetWidth, th = tip.offsetHeight;
    const vw = window.innerWidth,  vh = window.innerHeight;

    let x = clientX + TOOLTIP_OFFSET_X;
    if (x + tw > vw - TOOLTIP_EDGE_GAP) x = clientX - tw - TOOLTIP_OFFSET_X;

    let y = clientY - th / 2;
    if (y + th > vh - TOOLTIP_EDGE_GAP) y = vh - th - TOOLTIP_EDGE_GAP;
    y = Math.max(TOOLTIP_EDGE_GAP, y);

    tip.style.left = `${x}px`;
    tip.style.top  = `${y}px`;

    // On touch there is no mouseleave to close it, so it times out on its own.
    if (_hideTimer) clearTimeout(_hideTimer);
    if (_isTouch) _hideTimer = setTimeout(hideTooltip, TOOLTIP_DISMISS_MS);
}

function hideTooltip() {
    if (_hideTimer) { clearTimeout(_hideTimer); _hideTimer = null; }
    document.getElementById('posTooltip').style.display = 'none';
    _tipPosId = null;
    _tipKind  = null;
}

function _addRowInteractions(tr, pos) {
    const cancelPending = () => {
        if (_hoverTimer) { clearTimeout(_hoverTimer); _hoverTimer = null; }
    };

    if (_isTouch) {
        // Touch: a plain tap. Long-press was unreliable — the browser's own
        // text-selection gesture wins. A tap on an indicator badge shows that
        // badge's text (no native title tooltips on touch); otherwise the tap
        // shows whatever the column it landed in owns, which is the same split
        // the desktop hover makes.
        tr.addEventListener('click', e => {
            if (e.target.closest('.mw-row-btn')) return;   // edit/delete/merge keep their action
            const x = e.clientX, y = e.clientY;
            const badge = e.target.closest('.mw-ind');
            if (badge) { showTipText(badge.title, x, y); return; }

            const kind = _cellTipKind(e.target.closest('td'));
            if (kind === null) { hideTooltip(); return; }  // actions column: nothing to say
            // Tapping the same column of the same row again dismisses it;
            // tapping across to a different column swaps the content instead.
            if (_tipPosId === pos.id && _tipKind === kind) { hideTooltip(); return; }
            showCellTooltip(pos, kind, x, y);
        });
        return;
    }

    // Desktop: hover with delay, re-evaluated whenever the pointer crosses into
    // a cell that owns a different tooltip.  activeKind is what this row has on
    // screen or on its way there, so sweeping across the six price columns does
    // not flicker the same text off and on again at every boundary.
    let hoverCell  = null;
    let activeKind = null;

    // mouseover bubbles from child elements, so cell-to-cell moves land here —
    // mouseenter would only fire once, on entering the row.
    tr.addEventListener('mouseover', e => {
        // An indicator badge (risk / ITM / earnings) carries its own native
        // title tooltip; suppress ours so the two never stack up.
        if (e.target.closest('.mw-ind')) {
            hoverCell = activeKind = null;
            cancelPending();
            hideTooltip();
            return;
        }
        const td = e.target.closest('td');
        if (td === hoverCell) return;             // still in the same cell
        hoverCell = td;

        const kind = _cellTipKind(td);
        if (kind !== null && kind === activeKind) return;   // same text already up

        cancelPending();
        hideTooltip();
        activeKind = kind;
        if (kind === null) return;                // actions column: stay quiet

        const x = e.clientX, y = e.clientY;
        _hoverTimer = setTimeout(() => showCellTooltip(pos, kind, x, y), HOVER_DELAY_MS);
    });

    tr.addEventListener('mouseleave', () => {
        hoverCell = activeKind = null;
        cancelPending();
        hideTooltip();
    });
}

function mkTd(text, cls) {
    const td = document.createElement('td');
    if (cls) td.className = cls;
    td.textContent = text;
    return td;
}

/** Indicator swatch (risk / ITM / earnings). A real button so touch users get a
 *  proper tap target — the row click handler shows `text` in the shared
 *  tooltip, since native title tooltips never appear on touch.  On desktop the
 *  native title does the work and the cell tooltip stands aside. */
function mkIndBadge(className, label, text) {
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = className;
    btn.textContent = label;
    btn.title = text;
    return btn;
}

function mkRowBtn(label, handler) {
    const btn = document.createElement('button');
    btn.textContent = label;
    btn.className = 'btn btn-sm py-0 px-1 mw-row-btn';
    btn.addEventListener('click', handler);
    return btn;
}

function updateColHeaders() {
    const labels = {
        position: 'Position', sector: 'Sector', qty: '#', margin: 'Margin',
        opt: '$/shr', theta: 'Theta', theta_norm: 'θ/10k',
    };
    document.querySelectorAll('#positionsTable thead th.sortable').forEach(th => {
        const col = th.dataset.col;
        let text = labels[col];
        if (_colSort?.col === col) text += _colSort.dir === 'asc' ? ' ▲' : ' ▼';
        th.textContent = text;
    });
}

// ---------------------------------------------------------------------------
// Config
// ---------------------------------------------------------------------------

async function saveConfig() {
    const margin      = parseInt(document.getElementById('cfgMargin').value);
    const multiplier  = parseFloat(document.getElementById('cfgMultiplier').value);
    const riskFree    = parseFloat(document.getElementById('cfgRiskFree').value);
    const sort        = document.querySelector('input[name="sort"]:checked').value;
    if (isNaN(margin) || isNaN(multiplier) || isNaN(riskFree)) {
        alert('Enter valid numeric values.'); return;
    }
    if (multiplier < 0.5 || multiplier > 4.0) { alert('Multiplier must be 0.5–4.0.'); return; }
    if (riskFree < 0 || riskFree > 20) { alert('Risk-free rate must be 0–20%.'); return; }

    const btn = document.getElementById('btnSaveConfig');
    const msg = document.getElementById('cfgStatusMsg');

    const showMsg = (text, cssClass, durationMs) => {
        msg.textContent = text;
        msg.className = `ms-2 small ${cssClass}`;
        msg.style.display = 'inline';
        setTimeout(() => { msg.style.display = 'none'; }, durationMs);
    };

    btn.disabled = true;
    showMsg('Saving…', 'text-muted', 60000);   // placeholder; replaced on completion

    try {
        const resp = await fetch('/api/config', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                MaximumMarginBasis: margin,
                MarginMultiplier: multiplier,
                RiskFreeRate: riskFree,
                SortOrder: sort,
            }),
        });
        if (resp.ok) {
            loadPositions();
            showMsg('Saved', 'text-success', SAVED_MSG_DISMISS_MS);
        } else {
            const body = await resp.json().catch(() => ({}));
            showMsg('Error: ' + (body.error || `server returned ${resp.status}`),
                    'text-danger', SAVED_MSG_DISMISS_MS * 2);
        }
    } catch (err) {
        showMsg('Error: ' + err.message, 'text-danger', SAVED_MSG_DISMISS_MS * 2);
    } finally {
        btn.disabled = false;
    }
}

// ---------------------------------------------------------------------------
// CRUD
// ---------------------------------------------------------------------------

/** Format a Date as yyyy-mm-dd in *local* time.
 *  toISOString() converts to UTC first, which lands on the wrong calendar day
 *  for any viewer east of Greenwich (local midnight is the previous day in UTC). */
function toDateStr(d) {
    const pad = n => String(n).padStart(2, '0');
    return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}

/** Shift a yyyy-mm-dd string by *days*, or null if it isn't a valid date.
 *  setDate() rolls month and year boundaries over for us. */
function shiftDateStr(iso, days) {
    const d = new Date(iso + 'T00:00:00');
    if (isNaN(d)) return null;
    d.setDate(d.getDate() + days);
    return toDateStr(d);
}

function nextOptionFriday() {
    const today = new Date();
    const d = today.getDay(); // 0=Sun, 1=Mon … 5=Fri, 6=Sat
    // Default to *next* week's Friday: Mon–Thu skip an extra week so the form
    // never opens on the current week's expiry. Fri/Sat already land on next
    // Friday; Sun is 5 days out (next week).
    const days = d === 5 ? 7 : d === 6 ? 6 : d === 0 ? 5 : (5 - d) + 7;
    const result = new Date(today);
    result.setDate(today.getDate() + days);
    return toDateStr(result);
}

function openAddModal() {
    _editId = null;
    _earningsDate = null;
    document.getElementById('positionModalTitle').textContent = 'Add Position';
    document.getElementById('positionForm').reset();
    document.getElementById('fExpiration').value = nextOptionFriday();
    document.getElementById('fQty').value = '1';
    document.getElementById('fStrike2').value = '';
    document.getElementById('btnAssigned').classList.add('d-none');
    document.getElementById('btnClearCover').classList.add('d-none');
    updateFormFields();
    _posModal.show();
    setTimeout(() => document.getElementById('fSymbol').focus(), MODAL_FOCUS_DELAY_MS);
}

function updateEarningsWarning() {
    if (_editId !== null) return;   // only for Add, not Edit
    const expiration = document.getElementById('fExpiration').value;
    const titleEl = document.getElementById('positionModalTitle');
    const crossesEarnings = _earningsDate && expiration && expiration >= _earningsDate;
    titleEl.textContent = crossesEarnings
        ? 'Add Position (crosses earnings)'
        : 'Add Position';
}

async function editPosition(id) {
    const resp = await fetch(`/api/positions/${id}`);
    if (!resp.ok) return;
    const pos = await resp.json();
    _editId = id;

    document.getElementById('positionModalTitle').textContent = 'Edit Position';
    document.getElementById('fSymbol').value      = pos.symbol;
    document.getElementById('fType').value        = pos.option_type;
    document.getElementById('fExpiration').value  = pos.expiration || '';
    document.getElementById('fStrike').value      = pos.strike || '';
    document.getElementById('fQty').value         = pos.quantity || 1;
    document.getElementById('fShares').value      = pos.long_shares || '';
    document.getElementById('fCost').value        = pos.long_cost || '';
    document.getElementById('fStrike2').value  = pos.strike2 || '';

    document.getElementById('btnAssigned')
        .classList.toggle('d-none', pos.option_type !== 'PUT');
    document.getElementById('btnClearCover')
        .classList.toggle('d-none', !(pos.option_type === 'STOCK' && pos.strike));

    updateFormFields();
    _posModal.show();
}

async function savePosition(e) {
    e.preventDefault();
    const data = {
        symbol:      document.getElementById('fSymbol').value.trim().toUpperCase(),
        option_type: document.getElementById('fType').value,
        strike:      parseFloat(document.getElementById('fStrike').value) || 0,
        expiration:  document.getElementById('fExpiration').value || null,
        quantity:    parseInt(document.getElementById('fQty').value) || 1,
        long_shares: parseInt(document.getElementById('fShares').value) || null,
        long_cost:   parseFloat(document.getElementById('fCost').value) || null,
        strike2: parseFloat(document.getElementById('fStrike2').value) || null,
    };
    const url    = _editId ? `/api/positions/${_editId}` : '/api/positions';
    const method = _editId ? 'PUT' : 'POST';
    try {
        const resp = await fetch(url, {
            method,
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(data),
        });
        if (resp.ok) {
            _posModal.hide();
            loadPositions();
        } else {
            const body = await resp.json().catch(() => ({}));
            alert(`Save failed: ${body.error || `server returned ${resp.status}`}`);
        }
    } catch (err) {
        alert(`Save failed: ${err.message}`);
    }
}

async function deletePosition(id) {
    const pos = _positions.find(p => p.id === id);
    const label = pos ? pos.abbrev : 'this position';
    if (!await confirmDialog(`Delete ${label}?`)) return;
    const resp = await fetch(`/api/positions/${id}`, { method: 'DELETE' });
    if (resp.ok) loadPositions();
}

async function mergePositions(symbol, expiration, strike) {
    if (!await confirmDialog(`Merge ${symbol} STOCK positions into one?`)) return;
    const resp = await fetch('/api/positions/merge', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ symbol, expiration, strike }),
    });
    if (resp.ok) loadPositions();
}

// ---------------------------------------------------------------------------
// Form field visibility
// ---------------------------------------------------------------------------

function updateFormFields() {
    const ot = document.getElementById('fType').value;
    const isStock    = ot === 'STOCK';
    const isSpread   = ot === 'CALL_SPREAD' || ot === 'PUT_SPREAD';
    const isStraddle = ot === 'STRADDLE';
    const showStrike2 = isSpread || isStraddle;
    document.getElementById('rowShares').classList.toggle('d-none', !isStock);
    document.getElementById('rowCost').classList.toggle('d-none', !isStock);
    document.getElementById('rowStrike2').classList.toggle('d-none', !showStrike2);
    document.getElementById('strikeLabel').textContent =
        isSpread ? 'Strike (short)' : isStraddle ? 'Call Strike' : 'Strike';
    document.getElementById('strike2Label').textContent =
        isStraddle ? 'Put Strike' : 'Long Strike';
    document.getElementById('qtyLabel').textContent = isStock ? 'Quantity' : 'Contracts';
    if (!showStrike2) document.getElementById('fStrike2').value = '';
}

function applyAssigned() {
    // PUT exercised: convert to long stock at the strike price
    const strike = parseFloat(document.getElementById('fStrike').value) || 0;
    const qty    = parseInt(document.getElementById('fQty').value) || 1;
    document.getElementById('fType').value      = 'STOCK';
    document.getElementById('fShares').value    = qty * 100;
    document.getElementById('fCost').value      = strike.toFixed(2);
    document.getElementById('fStrike').value     = '';
    document.getElementById('fStrike2').value    = '';
    document.getElementById('fExpiration').value = '';
    document.getElementById('btnAssigned').classList.add('d-none');
    document.getElementById('btnClearCover').classList.add('d-none');
    updateFormFields();
}

function applyClearCover() {
    // Covered call closed/expired: keep shares, drop strike + expiration
    document.getElementById('fStrike').value     = '';
    document.getElementById('fExpiration').value = '';
    document.getElementById('btnClearCover').classList.add('d-none');
}

// ---------------------------------------------------------------------------
// Risk legend
// ---------------------------------------------------------------------------

function buildLegend() {
    const container = document.getElementById('riskLegendItems');
    if (!container) return;
    const prefix = document.createElement('span');
    prefix.className = 'mw-legend-prefix';
    prefix.textContent = 'Chance of assignment:';
    container.appendChild(prefix);
    RISK_BANDS.forEach((band, i) => {
        // Compute delta range label from adjacent thresholds
        let range;
        if (i === 0) {
            range = `≥${(band.threshold * 100).toFixed(0)}%`;
        } else if (i === RISK_BANDS.length - 1) {
            range = `<${(RISK_BANDS[i - 1].threshold * 100).toFixed(0)}%`;
        } else {
            range = `${(band.threshold * 100).toFixed(0)}–${(RISK_BANDS[i - 1].threshold * 100).toFixed(0)}%`;
        }
        const item = document.createElement('span');
        item.className = 'mw-legend-item';

        const ball = document.createElement('span');
        ball.className = 'mw-ind';
        ball.style.backgroundColor = band.color;

        const label = document.createElement('span');
        label.textContent = `${band.label} (${range})`;

        item.append(ball, label);
        container.appendChild(item);
    });
}

// ---------------------------------------------------------------------------
// Confirm dialog
// ---------------------------------------------------------------------------

let _confirmOpen = false;   // guard against re-entrant calls

function confirmDialog(msg) {
    if (_confirmOpen) return Promise.resolve(false);
    _confirmOpen = true;
    return new Promise(resolve => {
        document.getElementById('confirmMsg').textContent = msg;
        const modalEl = document.getElementById('confirmModal');
        let decided = false;

        document.getElementById('btnConfirmYes').addEventListener('click', () => {
            decided = true;
            _confirmModal.hide();
            resolve(true);
        }, { once: true });

        modalEl.addEventListener('hidden.bs.modal', () => {
            _confirmOpen = false;
            if (!decided) resolve(false);
        }, { once: true });

        _confirmModal.show();
    });
}
