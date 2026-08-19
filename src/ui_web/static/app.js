'use strict';

let _positions = [];
// Multi-column sort: ordered chain of { col, dir } — index 0 is the primary
// key, each later entry breaks ties in the one before it.  Empty means "the
// server's order" (the A-Z / Exp / Type radios).  See onSortColumn().
let _sortKeys = [];
const SORT_KEYS_STORAGE = 'mw.sortKeys';   // localStorage key (per device)
const SORT_HOLD_MS = 450;                   // touch: press-and-hold to stack a sort key
let _editId = null;    // null = adding new, number = editing existing
let _posModal = null;
let _confirmModal = null;
let _progressPoller = null;    // interval handle for fetch-progress polling
let _loadCtrl = null;          // AbortController for the in-flight loadPositions() (both phases)
let _loadSeq  = 0;             // generation counter — only the newest load may touch the table
let _sortQueued = false;       // sort changed mid-load: re-sort once the pull finishes
let _lastFetchSymbol = '';     // symbol the progress poller last reported
let _bars = {};                // symbol → [[epoch_ms, o, h, l, c], ...] oldest first (phase 3)
let _barsMeta = { days: 7, interval: '1h' };
let _portfolios = [];          // [{id, name, abbrev, max_margin, multiplier, is_default}]
let _maxPortfolios = 10;
let _pfModal = null;
let _pfEditId = null;          // null = adding a portfolio, number = editing

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
    _pfModal      = new bootstrap.Modal(document.getElementById('portfolioModal'));

    buildLegend();
    await loadConfig();
    await loadPortfolios();
    loadPositions();

    _loadSortKeys();
    updateColHeaders();

    document.querySelectorAll('input[name="sort"]').forEach(r =>
        r.addEventListener('change', () => {
            // A radio is the "back to plain order" control, on touch the only
            // one: it drops the whole column-sort chain.
            _setSortKeys([]);
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

    document.querySelectorAll('#positionsTable thead th.sortable').forEach(th => {
        // Appended, not assigned: Pf and 7d carry their own hints in the
        // template ("Portfolio", "hover / tap for detail") that must survive.
        const sortHint = _isTouch
            ? 'Tap to sort · press and hold to add a secondary sort'
            : 'Click to sort · Shift+click to add a secondary sort';
        th.title = th.title ? `${th.title} · ${sortHint}` : sortHint;
        // Desktop: Shift/Ctrl-click stacks.  Touch has no modifier keys, so a
        // press-and-hold stacks instead; the click that follows the release
        // is swallowed so the column is not immediately re-cycled.
        let holdTimer = null, held = false;
        const cancelHold = () => { if (holdTimer) { clearTimeout(holdTimer); holdTimer = null; } };
        if (_isTouch) {
            th.addEventListener('pointerdown', () => {
                held = false;
                cancelHold();
                holdTimer = setTimeout(() => {
                    holdTimer = null;
                    held = true;
                    onSortColumn(th.dataset.col, true);
                }, SORT_HOLD_MS);
            });
            ['pointerup', 'pointercancel', 'pointerleave'].forEach(ev =>
                th.addEventListener(ev, cancelHold));
            th.addEventListener('contextmenu', e => e.preventDefault());
        }
        th.addEventListener('click', e => {
            if (held) { held = false; return; }
            onSortColumn(th.dataset.col, e.shiftKey || e.ctrlKey || e.metaKey);
        });
    });

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
    document.getElementById('btnAddPortfolio').addEventListener('click', openAddPortfolio);

    // Widening past the breakpoint after loading narrow: the column appears
    // with empty cells, so run a load to fill them (server-cached, so cheap).
    _narrowMq.addEventListener('change', e => {
        if (!e.matches && !Object.keys(_bars).length && !_loadCtrl) loadPositions();
    });
    document.getElementById('portfolioForm').addEventListener('submit', savePortfolio);

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

    // ── Phase 3: 7-day price bars for the sparklines ─────────────────────────
    // Cosmetic, so it runs last and never holds up the table; the cells are
    // fixed-size placeholders until the bars land and are then filled in place
    // rather than by a third full re-render (which would yank a hovered row).
    if (_sparkHidden()) { drain(); return; }   // column not shown — skip the fetch
    _startProgressPolling();
    try {
        const resp = await fetch('/api/bars', { signal: ctrl.signal });
        const data = resp.ok ? await resp.json() : null;
        // Checked before anything is touched — on every path, not just the
        // happy one: a newer load owns the table and the progress poller now,
        // and the tail below must not stop *its* poller.
        if (!isCurrent()) return;
        if (data) {
            _bars = data.bars || {};
            if (data.days)     _barsMeta.days     = data.days;
            if (data.interval) _barsMeta.interval = data.interval;
            _updateSparkCells();
            _refreshTooltipText();   // an open chart popup picks up the new bars
        }
    } catch (e) {
        if (e.name === 'AbortError' || !isCurrent()) return;
        console.error('[MarginWatch] bars fetch failed:', e);
    }
    _stopProgressPolling();
    drain();         // everything is in — now apply any sort that arrived meanwhile
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

/** Row 1 is "All" — every position in every portfolio against the summed
 *  capacity.  One row per portfolio follows, keyed by its three-letter tag,
 *  then theta underneath the lot. */
function updateSummary(s) {
    document.getElementById('totalMargin').textContent = `$${s.total_margin.toFixed(1)}k`;
    document.getElementById('totalTheta').textContent =
        `$${s.total_theta.toLocaleString()}/d`;
    const el = document.getElementById('availMargin');
    el.textContent = `$${s.avail_margin.toFixed(1)}k`;
    el.className = 'mw-val' + (s.avail_margin < 0 ? ' mw-danger' : '');

    const grid = document.getElementById('summaryGrid');
    grid.querySelectorAll('.mw-sum-pf-row').forEach(n => n.remove());
    for (const pf of s.portfolios || []) {
        const label = document.createElement('span');
        label.className = 'mw-sum-label mw-sum-pf mw-sum-pf-row';
        label.textContent = pf.abbrev;
        label.title = pf.name;
        const gap1 = document.createElement('span');
        gap1.className = 'mw-sum-pf-row';
        const total = document.createElement('strong');
        total.className = 'mw-val mw-sum-pf-row';
        total.textContent = `$${pf.total_margin.toFixed(1)}k`;
        const gap2 = document.createElement('span');
        gap2.className = 'mw-sum-pf-row';
        const avail = document.createElement('strong');
        avail.className = 'mw-val mw-sum-pf-row' + (pf.avail_margin < 0 ? ' mw-danger' : '');
        avail.textContent = `$${pf.avail_margin.toFixed(1)}k`;
        grid.append(label, gap1, total, gap2, avail);
    }
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

// ---------------------------------------------------------------------------
// Column sort (multi-key)
// ---------------------------------------------------------------------------

/** The value a row sorts on for *col*.  Unknown / missing values sort to the
 *  bottom in ascending order (-Infinity for numbers, '￿' for text). */
function _sortValue(pos, col) {
    switch (col) {
        case 'position':   return pos.abbrev;
        case 'portfolio':  return (pos.portfolio || '').toLowerCase();
        case 'sector':     return pos.sector || '￿';
        case 'qty':        return pos.qty;
        case 'margin':     return pos.margin;
        case 'opt':        return _optSortValue(pos.opt_str);
        case 'theta':      return pos.theta_dollars ?? -Infinity;
        case 'theta_norm': return pos.theta_norm ?? -Infinity;
        default:           return null;   // unknown column — no effect
    }
}

/** The chain as actually applied.  Sorting by portfolio implies sorting by
 *  position name within each portfolio, so an implicit "Position ▲" is
 *  appended whenever Pf is in the chain and Position is not — at the end, so
 *  any explicit secondary keys (Pf, then Margin) still take precedence. */
function _effectiveSortKeys() {
    const hasPf   = _sortKeys.some(k => k.col === 'portfolio');
    const hasName = _sortKeys.some(k => k.col === 'position');
    return hasPf && !hasName ? [..._sortKeys, { col: 'position', dir: 'asc' }] : _sortKeys;
}

/** Sort by the chain: the first key that differs decides; equal rows keep
 *  the order they arrived in (Array.sort is stable), i.e. the server's. */
function _sortRows(rows) {
    const keys = _effectiveSortKeys();
    return [...rows].sort((a, b) => {
        for (const { col, dir } of keys) {
            const va = _sortValue(a, col), vb = _sortValue(b, col);
            if (va == null || vb == null) continue;
            let cmp = 0;
            if (va < vb) cmp = -1;
            else if (va > vb) cmp = 1;
            if (cmp !== 0) return dir === 'asc' ? cmp : -cmp;
        }
        return 0;
    });
}

/** Header click.  A column cycles ▲ → ▼ → off.  Plain click: that column is
 *  the whole sort.  *stack* (Shift/Ctrl-click, or any tap on touch): the
 *  column is added to the end of the chain, or cycled in place if present. */
function onSortColumn(col, stack) {
    const idx = _sortKeys.findIndex(k => k.col === col);
    const cur = idx === -1 ? null : _sortKeys[idx].dir;
    const next = cur === null ? 'asc' : cur === 'asc' ? 'desc' : null;
    let keys;
    if (stack) {
        keys = _sortKeys.filter(k => k.col !== col);
        if (next) {
            if (idx === -1) keys.push({ col, dir: next });
            else            keys.splice(idx, 0, { col, dir: next });
        }
    } else {
        // Single sort: the column's own cycle continues only if it was the
        // sole key; otherwise it starts fresh as the one ascending key.
        const sole = _sortKeys.length === 1 && idx === 0;
        keys = sole ? (next ? [{ col, dir: next }] : []) : [{ col, dir: 'asc' }];
    }
    _setSortKeys(keys);
    renderTable();
}

function _setSortKeys(keys) {
    _sortKeys = keys;
    updateColHeaders();
    try {
        if (keys.length) localStorage.setItem(SORT_KEYS_STORAGE, JSON.stringify(keys));
        else             localStorage.removeItem(SORT_KEYS_STORAGE);
    } catch (_) { /* private mode / storage disabled — sort still works this visit */ }
}

function _loadSortKeys() {
    try {
        const raw = localStorage.getItem(SORT_KEYS_STORAGE);
        const keys = raw ? JSON.parse(raw) : [];
        // Only columns that are sortable today survive: a key saved by an
        // older build for a column that no longer sorts is dropped.
        const sortable = new Set([...document.querySelectorAll('#positionsTable thead th.sortable')]
                                 .map(th => th.dataset.col));
        _sortKeys = Array.isArray(keys)
            ? keys.filter(k => k && sortable.has(k.col) && (k.dir === 'asc' || k.dir === 'desc'))
            : [];
    } catch (_) { _sortKeys = []; }
}

function renderTable() {
    let items = [..._positions];

    if (_sortKeys.length) items = _sortRows(items);

    const tbody = document.getElementById('positionsBody');
    tbody.innerHTML = '';

    if (items.length === 0) {
        tbody.innerHTML =
            '<tr><td colspan="10" class="text-center text-muted py-3">No open positions.</td></tr>';
        return;
    }

    for (const [i, pos] of items.entries()) {
        const tr = document.createElement('tr');
        if ((i + 1) % ROW_RULE_INTERVAL === 0) tr.classList.add('mw-row-rule');
        tr.style.backgroundColor = pos.bg;
        tr.style.color = pos.fg;

        // Portfolio tag — first three letters of the portfolio's name.
        const pfCell = mkTd(pos.portfolio_abbrev || '', 'mw-pf-col');

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
        nameSpan.className = 'mw-pos-label' + (pos.is_stock_row ? ' mw-stock-pos' : '');
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

        // 7-day sparkline.  Empty placeholder until phase 3 fills it in.
        const sparkCell = document.createElement('td');
        sparkCell.className = 'mw-spark-col mw-spark-cell';
        sparkCell.dataset.symbol = pos.symbol;
        sparkCell.innerHTML = sparkSvg(_bars[pos.symbol]);

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
            const [pfId, sym, exp, strike] = pos.merge_key;
            const mergeBtn = mkRowBtn(ICON_MERGE, () => mergePositions(pfId, sym, exp, strike));
            actCell.appendChild(mergeBtn);
        }

        tr.append(pfCell, posCell, sectorCell, sparkCell, qtyCell, marginCell, optCell, thetaCell, thetaNormCell, actCell);
        _addRowInteractions(tr, pos);
        tbody.appendChild(tr);
    }
}

// ---------------------------------------------------------------------------
// 7-day sparkline + expanded OHLC chart
// ---------------------------------------------------------------------------
// Bars arrive in phase 3 as [epoch_ms, o, h, l, c] arrays, oldest first.  The
// table cell shows a close-price line (too narrow for candles to read); the
// hover / tap popup shows the same week as candlesticks with the position's
// strike(s) drawn across it.

const SPARK_W = 64, SPARK_H = 18;
const CHART_W = 280, CHART_H = 180;     // ≈ 12 lines of text tall
const COLOR_UP_ROW = '#15803d', COLOR_DN_ROW = '#b91c1c';   // on light row backgrounds
const COLOR_UP_POP = '#4ade80', COLOR_DN_POP = '#f87171';   // on the dark popup

/** Narrow window: the 7d column is hidden by CSS, so the bars are not worth
 *  fetching.  Matches the breakpoint in style.css. */
const _narrowMq = window.matchMedia('(max-width: 599px)');
function _sparkHidden() { return _narrowMq.matches; }

/** Refill every sparkline cell in place — no row re-render, so a hovered row
 *  stays put and the open tooltip (if any) is untouched. */
function _updateSparkCells() {
    document.querySelectorAll('#positionsBody td.mw-spark-cell').forEach(td => {
        td.innerHTML = sparkSvg(_bars[td.dataset.symbol]);
    });
}

/** Small close-price line for the table cell; '' when there are no bars. */
function sparkSvg(bars) {
    if (!bars || bars.length < 2) return '';
    const closes = bars.map(b => b[4]);
    let lo = Math.min(...closes), hi = Math.max(...closes);
    if (hi === lo) { hi += 0.5; lo -= 0.5; }
    const w = SPARK_W, h = SPARK_H, pad = 1.5;
    const x = i => (i / (closes.length - 1)) * w;
    const y = v => pad + (hi - v) / (hi - lo) * (h - 2 * pad);
    const pts = closes.map((c, i) => `${x(i).toFixed(1)},${y(c).toFixed(1)}`).join(' ');
    const up = closes[closes.length - 1] >= bars[0][1];
    const color = up ? COLOR_UP_ROW : COLOR_DN_ROW;
    const lastX = x(closes.length - 1).toFixed(1), lastY = y(closes[closes.length - 1]).toFixed(1);
    return `<svg viewBox="0 0 ${w} ${h}" preserveAspectRatio="none" aria-hidden="true">` +
           `<polygon points="0,${h} ${pts} ${w},${h}" fill="${color}" fill-opacity="0.12"/>` +
           `<polyline points="${pts}" fill="none" stroke="${color}" stroke-width="1.3" ` +
           `vector-effect="non-scaling-stroke" stroke-linejoin="round"/>` +
           `<circle cx="${lastX}" cy="${lastY}" r="1.6" fill="${color}"/>` +
           `</svg>`;
}

function _fmtPrice(v) {
    if (v == null || !isFinite(v)) return '—';
    return v >= 1000 ? v.toFixed(0) : v >= 100 ? v.toFixed(1) : v.toFixed(2);
}

function _esc(s) {
    return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/"/g, '&quot;');
}

/** Expanded candlestick chart for the popup; '' when the bars are not in yet. */
function chartSvg(pos) {
    const bars = _bars[pos.symbol];
    if (!bars || bars.length < 2) return '';

    const W = CHART_W, H = CHART_H;
    const mL = 6, mR = 46, mT = 20, mB = 16;
    const pw = W - mL - mR, ph = H - mT - mB;
    const n = bars.length;

    // Y domain: the bars, padded, stretched to take in a strike that is near
    // enough to be worth seeing (a strike half a chart away would squash the
    // candles into a line, so it is left off and only named in the title).
    let lo = Math.min(...bars.map(b => b[3])), hi = Math.max(...bars.map(b => b[2]));
    if (hi === lo) { hi += 0.5; lo -= 0.5; }
    let range = hi - lo;
    lo -= range * 0.04; hi += range * 0.04;
    const strikes = [];
    if (pos.strike)  strikes.push(pos.strike);
    if (pos.strike2) strikes.push(pos.strike2);
    const shown = [];
    for (const k of strikes) {
        if (k > lo - range * 0.5 && k < hi + range * 0.5) {
            lo = Math.min(lo, k - range * 0.03); hi = Math.max(hi, k + range * 0.03);
            shown.push(k);
        }
    }
    const x = i => mL + (i + 0.5) * (pw / n);
    const y = v => mT + (hi - v) / (hi - lo) * ph;
    const slot = pw / n, bodyW = Math.max(1.2, Math.min(7, slot * 0.62));

    const out = [];
    // Day boundaries — faint rule where the date rolls over, weekday label
    // centred under each day's bars (ET, the exchange's clock).
    const dayOf = t => new Intl.DateTimeFormat('en-US', { timeZone: 'America/New_York',
                                                          weekday: 'short', day: 'numeric' })
                           .formatToParts(new Date(t));
    let dayStart = 0, prevKey = null;
    const days = [];
    bars.forEach((b, i) => {
        const parts = dayOf(b[0]);
        const key = parts.map(p => p.value).join('');
        if (prevKey !== null && key !== prevKey) { days.push([dayStart, i - 1, prevKey]); dayStart = i; }
        prevKey = key;
    });
    days.push([dayStart, n - 1, prevKey]);
    days.forEach(([a, z, key], j) => {
        if (j > 0) {
            const xb = mL + a * slot;
            out.push(`<line x1="${xb.toFixed(1)}" y1="${mT}" x2="${xb.toFixed(1)}" y2="${mT + ph}" ` +
                     `stroke="rgba(255,255,255,0.12)"/>`);
        }
        const label = key.replace(/[0-9]/g, '').trim();
        const cx = (x(a) + x(z)) / 2;
        out.push(`<text x="${cx.toFixed(1)}" y="${H - 4}" font-size="9" fill="#aaa" ` +
                 `text-anchor="middle">${label}</text>`);
    });

    // Strike lines
    for (const k of shown) {
        const yk = y(k).toFixed(1);
        out.push(`<line x1="${mL}" y1="${yk}" x2="${mL + pw}" y2="${yk}" stroke="#facc15" ` +
                 `stroke-width="1" stroke-dasharray="4 3"/>`);
        out.push(`<text x="${mL + pw + 3}" y="${(+yk + 3).toFixed(1)}" font-size="9" fill="#facc15">` +
                 `K ${_fmtPrice(k)}</text>`);
    }

    // Candles
    for (let i = 0; i < n; i++) {
        const [, o, h, l, c] = bars[i];
        const up = c >= o;
        const color = up ? COLOR_UP_POP : COLOR_DN_POP;
        const cx = x(i).toFixed(1);
        out.push(`<line x1="${cx}" y1="${y(h).toFixed(1)}" x2="${cx}" y2="${y(l).toFixed(1)}" ` +
                 `stroke="${color}" stroke-width="1"/>`);
        const top = y(Math.max(o, c)), bot = y(Math.min(o, c));
        const bh = Math.max(1, bot - top);
        out.push(`<rect x="${(x(i) - bodyW / 2).toFixed(1)}" y="${top.toFixed(1)}" ` +
                 `width="${bodyW.toFixed(1)}" height="${bh.toFixed(1)}" fill="${color}"/>`);
    }

    // Axis labels: window high / low on the right edge — skipped when a strike
    // label already sits there, so the two never print on top of each other.
    const barHi = Math.max(...bars.map(b => b[2])), barLo = Math.min(...bars.map(b => b[3]));
    for (const v of [barHi, barLo]) {
        if (shown.some(k => Math.abs(y(k) - y(v)) < 10)) continue;
        out.push(`<text x="${mL + pw + 3}" y="${(y(v) + 3).toFixed(1)}" font-size="9" fill="#ddd">${_fmtPrice(v)}</text>`);
    }

    // Title: symbol, window, last close and change
    const last = bars[n - 1][4], first = bars[0][1];
    const chg = first ? (last - first) / first * 100 : null;
    const chgColor = chg == null ? '#ddd' : chg >= 0 ? COLOR_UP_POP : COLOR_DN_POP;
    const offStrikes = strikes.filter(k => !shown.includes(k)).map(k => `K ${_fmtPrice(k)} off-chart`);
    const title = `${_esc(pos.symbol)} · ${_barsMeta.days}d ${_esc(_barsMeta.interval)}` +
                  (offStrikes.length ? ` · ${_esc(offStrikes.join(', '))}` : '');
    out.push(`<text x="${mL}" y="13" font-size="11" font-weight="600" fill="#fff">${title}</text>`);
    out.push(`<text x="${W - 4}" y="13" font-size="11" text-anchor="end" fill="${chgColor}">` +
             `${_fmtPrice(last)}${chg == null ? '' : ` (${chg >= 0 ? '+' : ''}${chg.toFixed(1)}%)`}</text>`);

    return `<svg class="mw-chart" width="${W}" height="${H}" viewBox="0 0 ${W} ${H}" ` +
           `role="img" aria-label="${_esc(pos.symbol)} 7-day price chart">${out.join('')}</svg>`;
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
const TIP_PF    = 'pf';      // full portfolio name — the Pf column
const TIP_CHART = 'chart';   // expanded 7-day OHLC chart — the sparkline column

let _hoverTimer = null;   // hover delay timer handle
let _hideTimer  = null;   // auto-dismiss timer handle (touch)
let _tipPosId   = null;   // id of the position the tooltip is currently showing
let _tipKind    = null;   // TIP_NAME / TIP_PRICE — which datum, so a refresh
                          // that lands while it is open rewrites the right one

/** Which tooltip a cell owns, or null when it should stay quiet. */
function _cellTipKind(td) {
    if (!td)                                       return null;
    if (td.classList.contains('mw-actions-cell'))  return null;
    if (td.classList.contains('mw-pf-col'))        return TIP_PF;
    if (td.classList.contains('mw-spark-cell'))    return TIP_CHART;
    if (td.classList.contains('mw-position-cell')) return TIP_NAME;
    return TIP_PRICE;
}

/** Text for one kind of row tooltip, or null when there is nothing to say.
 *  The company name arrives with the price pass, so it is absent on a cold
 *  paint — and some symbols have no name at all. */
function _tipTextFor(pos, kind) {
    if (kind === TIP_NAME)  return pos.company_name || null;
    if (kind === TIP_PRICE) return _priceTipText(pos);
    if (kind === TIP_PF)    return pos.portfolio || null;
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
    if (_tipKind === TIP_CHART) {
        const html = chartSvg(pos);
        if (html) tip.innerHTML = html;
        return;
    }
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
    if (kind === TIP_CHART) {
        const html = chartSvg(pos);
        if (!html) return false;          // bars not in yet — stay quiet
        showTipHtml(html, clientX, clientY);
    } else {
        const text = _tipTextFor(pos, kind);
        if (text == null) return false;
        showTipText(text, clientX, clientY);
    }
    _tipPosId = pos.id;
    _tipKind  = kind;
    return true;
}

/** Show arbitrary text in the tooltip, anchored near (clientX, clientY). */
function showTipText(text, clientX, clientY) {
    const tip = document.getElementById('posTooltip');
    tip.textContent = text;
    _placeTip(tip, clientX, clientY);
}

/** Show markup (the expanded chart) in the tooltip, anchored like showTipText. */
function showTipHtml(html, clientX, clientY) {
    const tip = document.getElementById('posTooltip');
    tip.innerHTML = html;
    _placeTip(tip, clientX, clientY);
}

function _placeTip(tip, clientX, clientY) {
    _tipPosId = null;
    _tipKind  = null;
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
        portfolio: 'Pf', position: 'Position', sector: 'Sector', qty: '#', margin: 'Margin',
        opt: '$/shr', theta: 'Theta', theta_norm: 'θ/10k',
    };
    const multi = _sortKeys.length > 1;
    document.querySelectorAll('#positionsTable thead th.sortable').forEach(th => {
        const col = th.dataset.col;
        const idx = _sortKeys.findIndex(k => k.col === col);
        let text = labels[col];
        if (idx !== -1) {
            // Rank number only when there is more than one key — "Margin 2▼"
            // says it breaks ties in key 1; a lone "Margin ▼" needs no number.
            text += (multi ? ` ${idx + 1}` : '') + (_sortKeys[idx].dir === 'asc' ? '▲' : '▼');
        }
        th.textContent = text;
        th.classList.toggle('mw-sorted', idx !== -1);
    });
}

// ---------------------------------------------------------------------------
// Config
// ---------------------------------------------------------------------------

async function saveConfig() {
    const riskFree    = parseFloat(document.getElementById('cfgRiskFree').value);
    const sort        = document.querySelector('input[name="sort"]:checked').value;
    if (isNaN(riskFree)) { alert('Enter a valid numeric rate.'); return; }
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
    // Pre-select the default portfolio.  Changing it here only affects this
    // position; the default itself is set in the Configuration card.
    fillPortfolioSelect(_defaultPortfolioId());
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
    fillPortfolioSelect(pos.portfolio_id ?? _defaultPortfolioId());
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
        portfolio_id: parseInt(document.getElementById('fPortfolio').value) || null,
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

async function mergePositions(portfolioId, symbol, expiration, strike) {
    if (!await confirmDialog(`Merge ${symbol} STOCK positions into one?`)) return;
    const resp = await fetch('/api/positions/merge', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ portfolio_id: portfolioId, symbol, expiration, strike }),
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
// Portfolios
// ---------------------------------------------------------------------------

function _defaultPortfolioId() {
    return (_portfolios.find(p => p.is_default) || _portfolios[0] || {}).id ?? null;
}

async function loadPortfolios() {
    try {
        const resp = await fetch('/api/portfolios');
        if (!resp.ok) return;
        const data = await resp.json();
        _portfolios = data.portfolios || [];
        _maxPortfolios = data.max_portfolios || _maxPortfolios;
    } catch (err) {
        console.error('[MarginWatch] loadPortfolios failed:', err);
        return;
    }
    renderPortfolios();
}

/** Populate the Add/Edit form's portfolio dropdown and select *selectedId*. */
function fillPortfolioSelect(selectedId) {
    const sel = document.getElementById('fPortfolio');
    sel.innerHTML = '';
    for (const pf of _portfolios) {
        const opt = document.createElement('option');
        opt.value = pf.id;
        opt.textContent = pf.name + (pf.is_default ? ' (default)' : '');
        sel.appendChild(opt);
    }
    if (selectedId != null) sel.value = String(selectedId);
}

/** One row per portfolio in the Configuration card, each with edit / delete. */
function renderPortfolios() {
    const tbody = document.getElementById('portfoliosBody');
    if (!tbody) return;
    tbody.innerHTML = '';
    for (const pf of _portfolios) {
        const tr = document.createElement('tr');
        tr.append(
            mkTd(pf.name),
            mkTd(`$${pf.max_margin.toLocaleString()}`, 'text-end'),
            mkTd(pf.multiplier.toFixed(1), 'text-end'),
        );
        const defCell = document.createElement('td');
        defCell.className = 'text-center';
        if (pf.is_default) {
            defCell.textContent = '✓';
            defCell.className += ' mw-pf-default';
            defCell.title = 'Default portfolio';
        } else {
            const btn = mkRowBtn('☆', () => setDefaultPortfolio(pf.id));
            btn.title = 'Make default';
            defCell.appendChild(btn);
        }
        const actCell = document.createElement('td');
        actCell.className = 'text-end mw-actions-cell';
        actCell.appendChild(mkRowBtn(ICON_EDIT, () => openEditPortfolio(pf.id)));
        const del = mkRowBtn(ICON_DELETE, () => deletePortfolio(pf.id));
        if (pf.is_default) {
            // The default portfolio cannot be deleted: everything else's
            // positions fall back to it.
            del.disabled = true;
            del.title = 'The default portfolio cannot be deleted';
        }
        actCell.appendChild(del);
        tr.append(defCell, actCell);
        tbody.appendChild(tr);
    }
    const addBtn = document.getElementById('btnAddPortfolio');
    if (addBtn) {
        const full = _portfolios.length >= _maxPortfolios;
        addBtn.disabled = full;
        addBtn.title = full ? `At most ${_maxPortfolios} portfolios` : 'Add portfolio';
    }
}

function _showPfMsg(text, cssClass) {
    const msg = document.getElementById('pfStatusMsg');
    if (!msg) return;
    msg.textContent = text;
    msg.className = `small ${cssClass}`;
    msg.style.display = 'inline';
    setTimeout(() => { msg.style.display = 'none'; }, SAVED_MSG_DISMISS_MS * 2);
}

function openAddPortfolio() {
    _pfEditId = null;
    document.getElementById('portfolioModalTitle').textContent = 'Add Portfolio';
    document.getElementById('portfolioForm').reset();
    document.getElementById('pfMaxMargin').value = 250000;
    document.getElementById('pfMultiplier').value = '1.5';
    document.getElementById('pfDefault').checked = false;
    document.getElementById('pfDefault').disabled = false;
    _pfModal.show();
    setTimeout(() => document.getElementById('pfName').focus(), MODAL_FOCUS_DELAY_MS);
}

function openEditPortfolio(id) {
    const pf = _portfolios.find(p => p.id === id);
    if (!pf) return;
    _pfEditId = id;
    document.getElementById('portfolioModalTitle').textContent = 'Edit Portfolio';
    document.getElementById('pfName').value = pf.name;
    document.getElementById('pfMaxMargin').value = pf.max_margin;
    document.getElementById('pfMultiplier').value = pf.multiplier.toFixed(1);
    const def = document.getElementById('pfDefault');
    def.checked = pf.is_default;
    // Un-ticking the only default would leave no default; pick another
    // portfolio's box instead.
    def.disabled = pf.is_default;
    _pfModal.show();
}

async function savePortfolio(e) {
    e.preventDefault();
    const name = document.getElementById('pfName').value.trim();
    const maxMargin = parseInt(document.getElementById('pfMaxMargin').value);
    const multiplier = parseFloat(document.getElementById('pfMultiplier').value);
    if (!name) { alert('Enter a portfolio name.'); return; }
    if (isNaN(maxMargin) || isNaN(multiplier)) { alert('Enter valid numeric values.'); return; }
    if (multiplier < 0.5 || multiplier > 4.0) { alert('Multiplier must be 0.5–4.0.'); return; }
    // Names are unique ignoring case; catch the obvious clash before the round trip.
    const clash = _portfolios.find(p => p.id !== _pfEditId &&
                                        p.name.toLowerCase() === name.toLowerCase());
    if (clash) { alert(`A portfolio named "${clash.name}" already exists.`); return; }

    const body = {
        name, max_margin: maxMargin, multiplier,
        is_default: document.getElementById('pfDefault').checked,
    };
    const url    = _pfEditId ? `/api/portfolios/${_pfEditId}` : '/api/portfolios';
    const method = _pfEditId ? 'PUT' : 'POST';
    try {
        const resp = await fetch(url, {
            method,
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body),
        });
        if (resp.ok) {
            _pfModal.hide();
            await loadPortfolios();
            _showPfMsg('Saved', 'text-success');
            loadPositions();   // capacities changed → summary changes
        } else {
            const err = await resp.json().catch(() => ({}));
            alert(`Save failed: ${err.error || `server returned ${resp.status}`}`);
        }
    } catch (err) {
        alert(`Save failed: ${err.message}`);
    }
}

async function setDefaultPortfolio(id) {
    const resp = await fetch(`/api/portfolios/${id}/default`, { method: 'POST' });
    if (resp.ok) {
        await loadPortfolios();
    } else {
        const err = await resp.json().catch(() => ({}));
        alert(`Failed: ${err.error || `server returned ${resp.status}`}`);
    }
}

async function deletePortfolio(id) {
    const pf = _portfolios.find(p => p.id === id);
    if (!pf) return;
    const count = _positions.filter(p => p.portfolio_id === id).length;
    const def = _portfolios.find(p => p.is_default);
    const note = count
        ? ` Its ${count} position${count === 1 ? '' : 's'} will move to ${def ? def.name : 'the default portfolio'}.`
        : '';
    if (!await confirmDialog(`Delete portfolio "${pf.name}"?${note}`)) return;
    const resp = await fetch(`/api/portfolios/${id}`, { method: 'DELETE' });
    if (resp.ok) {
        await loadPortfolios();
        loadPositions();
    } else {
        const err = await resp.json().catch(() => ({}));
        alert(`Delete failed: ${err.error || `server returned ${resp.status}`}`);
    }
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
