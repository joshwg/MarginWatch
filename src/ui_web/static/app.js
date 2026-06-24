'use strict';

let _positions = [];
let _colSort = null;   // { col: string, dir: 'asc'|'desc' } | null
let _editId = null;    // null = adding new, number = editing existing
let _posModal = null;
let _confirmModal = null;
let _progressPoller = null;  // interval handle for fetch-progress polling

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

    document.getElementById('fExpiration').addEventListener('keydown', function (e) {
        if (e.key !== '-' && e.key !== '=') return;
        e.preventDefault();
        const d = new Date(this.value + 'T00:00:00');
        if (isNaN(d)) return;
        d.setDate(d.getDate() + (e.key === '=' ? 1 : -1));
        this.value = d.toISOString().slice(0, 10);
    });

    document.getElementById('confirmModal').addEventListener('hide.bs.modal', () => {
        if (document.activeElement?.closest('#confirmModal')) document.activeElement.blur();
    });

    document.getElementById('cfgExtHours').addEventListener('change', async function () {
        await fetch('/api/extended-hours', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ enabled: this.checked }),
        });
        loadPositions();
    });

    document.getElementById('btnAdd').addEventListener('click', openAddModal);
    document.getElementById('btnRefresh').addEventListener('click', refreshPrices);
    document.getElementById('btnSaveConfig').addEventListener('click', saveConfig);
    document.getElementById('positionForm').addEventListener('submit', savePosition);
    document.getElementById('fType').addEventListener('change', updateFormFields);
    document.getElementById('btnAssigned').addEventListener('click', applyAssigned);
    document.getElementById('btnClearCover').addEventListener('click', applyClearCover);
});

// ---------------------------------------------------------------------------
// Data loading
// ---------------------------------------------------------------------------

async function refreshPrices() {
    const btn = document.getElementById('btnRefresh');
    btn.disabled = true;
    await fetch('/api/refresh', { method: 'POST' });
    await loadPositions();
    btn.disabled = false;
}

async function loadPositions() {
    const sort = document.querySelector('input[name="sort"]:checked').value;

    // ── Phase 1: positions from the database (fast) ──────────────────────────
    try {
        const resp = await fetch(`/api/positions?sort=${sort}`);
        if (resp.status === 401) { location.href = '/login'; return; }
        const data = await resp.json();
        _positions = data.positions;
        updateSummary(data.summary);
        showFetchErrors(data.fetch_errors || []);
    } catch (e) {
        console.error('[MarginWatch] loadPositions failed:', e);
        _setFetchStatus(`⚠ Load failed: ${e.message || e}`, true);
        return;
    }
    renderTable();   // show the table immediately with whatever is cached

    // ── Phase 2: live market prices (slow, with progress bar) ────────────────
    _startProgressPolling();
    try {
        const resp = await fetch('/api/prices');
        if (resp.status === 401) { _stopProgressPolling(); location.href = '/login'; return; }
        const data = await resp.json();
        // Merge price-dependent fields into the existing position objects.
        const upd = data.updates || {};
        for (const pos of _positions) {
            if (upd[pos.id]) Object.assign(pos, upd[pos.id]);
        }
        // Update theta in the summary (the only summary field that needs prices).
        const sumEl = document.getElementById('totalTheta');
        if (sumEl && data.total_theta != null)
            sumEl.textContent = `$${data.total_theta.toLocaleString()}/d`;
        showFetchErrors(data.fetch_errors || []);
    } catch (e) {
        console.error('[MarginWatch] price fetch failed:', e);
        _stopProgressPolling();
        _setFetchStatus(`⚠ Price fetch failed: ${e.message || e}`, true);
        return;
    }
    _stopProgressPolling();
    renderTable();   // re-render with live prices filled in
}

function _startProgressPolling() {
    _setFetchStatus('Loading…');
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
            _setFetchStatus(symbol ? `Loading ${symbol}…` : 'Loading…');
        } catch { /* ignore poll errors */ }
    }, 300);
}

function _stopProgressPolling() {
    if (_progressPoller) { clearInterval(_progressPoller); _progressPoller = null; }
    _setFetchStatus('');
}

function _setFetchStatus(msg, isError = false) {
    const el = document.getElementById('fetchStatus');
    if (!el) return;
    el.textContent = msg;
    el.classList.toggle('d-none', !msg);
    el.classList.toggle('text-danger', isError);
    el.classList.toggle('text-muted', !isError);
}

function showFetchErrors(errors) {
    const banner = document.getElementById('fetchErrorBanner');
    const list   = document.getElementById('fetchErrorList');
    if (!errors.length) {
        banner.classList.add('d-none');
        return;
    }
    list.innerHTML = errors.map(e => `<li>${e}</li>`).join('');
    // Re-show the banner even if the user previously dismissed it, since
    // the errors may have changed after a refresh.
    banner.classList.remove('d-none');
    // Bootstrap may have removed 'show' on dismiss — restore it so the
    // alert is visible without needing a fade-in trigger.
    if (!banner.classList.contains('show')) banner.classList.add('show');
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
    // cfgExtHours intentionally not loaded from config — defaults to unchecked each session
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

function renderTable() {
    let items = [..._positions];

    if (_colSort) {
        const { col, dir } = _colSort;
        items.sort((a, b) => {
            let va, vb;
            if      (col === 'position') { va = a.abbrev;       vb = b.abbrev; }
            else if (col === 'qty')      { va = a.qty;          vb = b.qty; }
            else if (col === 'margin')   { va = a.margin;       vb = b.margin; }
            else if (col === 'opt')      { va = parseFloat(a.opt_str) || -Infinity;
                                           vb = parseFloat(b.opt_str) || -Infinity; }
            else if (col === 'theta')      { va = a.theta_dollars ?? -Infinity;
                                             vb = b.theta_dollars ?? -Infinity; }
            else if (col === 'theta_norm') { va = a.theta_norm ?? -Infinity;
                                             vb = b.theta_norm ?? -Infinity; }
            if (va < vb) return dir === 'asc' ? -1 : 1;
            if (va > vb) return dir === 'asc' ?  1 : -1;
            return 0;
        });
    }

    const tbody = document.getElementById('positionsBody');
    tbody.innerHTML = '';

    if (items.length === 0) {
        tbody.innerHTML =
            '<tr><td colspan="6" class="text-center text-muted py-3">No open positions.</td></tr>';
        return;
    }

    for (const [i, pos] of items.entries()) {
        const tr = document.createElement('tr');
        if ((i + 1) % ROW_RULE_INTERVAL === 0) tr.classList.add('mw-row-rule');
        tr.style.backgroundColor = pos.bg;
        tr.style.color = pos.fg;

        // Position cell: optional indicator swatches + name
        const posCell = document.createElement('td');

        // Risk indicator: coloured ball showing probability of assignment
        const rc = riskColor(pos.delta);
        if (rc !== null) {
            const risk = document.createElement('span');
            risk.className = 'mw-ind';
            risk.style.backgroundColor = rc;
            risk.title = `δ ${(pos.delta * 100).toFixed(0)}%`;
            posCell.appendChild(risk);
        }

        if (pos.itm) {
            const dot = document.createElement('span');
            dot.className = 'mw-ind mw-ind-itm';
            // Yellow when barely ITM (< $1 or < 3% of strike) — close to the edge.
            // Green for covered calls clearly ITM (profitable assignment likely).
            // Red for short options clearly ITM (losing position).
            const barelyItm = pos.itm_amount != null && pos.strike != null &&
                              (pos.itm_amount < 1.0 || pos.itm_amount < 0.03 * pos.strike);
            dot.style.backgroundColor = barelyItm
                ? '#ca8a04'
                : pos.is_stock_row ? COLOR_ITM_GOOD : COLOR_ITM_BAD;
            dot.textContent = 'i';
            dot.title = pos.itm_amount != null
                ? `ITM $${pos.itm_amount.toFixed(2)}`
                : 'In the money';
            // When the cursor enters the ITM ball, cancel any pending row hover
            // timer and hide any already-visible price tooltip so only the
            // native title tooltip (ITM amount) appears.
            dot.addEventListener('mouseenter', e => {
                e.stopPropagation();
                if (_hoverTimer) { clearTimeout(_hoverTimer); _hoverTimer = null; }
                hideTooltip();
            });
            dot.addEventListener('mouseleave', e => e.stopPropagation());
            posCell.appendChild(dot);
            if (pos.itm_amount != null) {
                const lbl = document.createElement('span');
                lbl.className = 'mw-itm-inline';
                lbl.textContent = `[${pos.itm_amount.toFixed(2)}]`;
                posCell.appendChild(lbl);
            }
        }
        if (pos.is_profitable) {
            const arrow = document.createElement('span');
            arrow.className = 'mw-profit-arrow';
            arrow.textContent = ICON_PROFIT;
            posCell.appendChild(arrow);
        }
        const nameSpan = document.createElement('span');
        nameSpan.textContent = pos.abbrev;
        if (pos.is_stock_row) nameSpan.className = 'mw-stock-pos';
        posCell.appendChild(nameSpan);
        if (pos.abbrev2) {
            const line2 = document.createElement('div');
            line2.textContent = pos.abbrev2;
            line2.style.cssText = 'font-size:0.78em;opacity:0.75';
            posCell.appendChild(line2);
        }

        const qtyCell    = mkTd(pos.qty,                   'text-center');
        const marginCell = mkTd(pos.margin.toFixed(1),     'text-end');
        const optCell    = mkTd(pos.opt_str,               'text-end');
        const thetaCell     = mkTd(pos.theta_str,                                            'text-end');
        const thetaNormCell = mkTd(pos.theta_norm != null ? pos.theta_norm.toFixed(1) : '—', 'text-end');

        const actCell = document.createElement('td');
        actCell.className = 'text-center';

        const editBtn = mkRowBtn(ICON_EDIT,   () => editPosition(pos.id));
        const delBtn  = mkRowBtn(ICON_DELETE, () => deletePosition(pos.id));
        actCell.append(editBtn, delBtn);

        if (pos.show_merge) {
            const [sym, exp, strike] = pos.merge_key;
            const mergeBtn = mkRowBtn(ICON_MERGE, () => mergePositions(sym, exp, strike));
            actCell.appendChild(mergeBtn);
        }

        tr.append(posCell, qtyCell, marginCell, optCell, thetaCell, thetaNormCell, actCell);
        _addRowInteractions(tr, pos);
        tbody.appendChild(tr);
    }
}

// ---------------------------------------------------------------------------
// Position tooltip (hover on desktop, long-press on mobile)
// ---------------------------------------------------------------------------

let _lpTimer    = null;   // long-press timer handle
let _hoverTimer = null;   // hover delay timer handle

function showTooltip(pos, clientX, clientY) {
    const tip = document.getElementById('posTooltip');
    tip.textContent = pos.price != null ? `${pos.symbol} $${pos.price.toFixed(2)}` : `${pos.symbol} —`;
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
}

function hideTooltip() {
    document.getElementById('posTooltip').style.display = 'none';
}

function _addRowInteractions(tr, pos) {
    // Desktop: hover with delay
    tr.addEventListener('mouseenter', e => {
        const x = e.clientX, y = e.clientY;
        _hoverTimer = setTimeout(() => showTooltip(pos, x, y), HOVER_DELAY_MS);
    });
    tr.addEventListener('mouseleave', () => {
        if (_hoverTimer) { clearTimeout(_hoverTimer); _hoverTimer = null; }
        hideTooltip();
    });

    // Mobile: long-press (~500 ms)
    tr.addEventListener('touchstart', e => {
        const t = e.touches[0];
        _lpTimer = setTimeout(() => { _lpTimer = null; showTooltip(pos, t.clientX, t.clientY); }, LONGPRESS_DELAY_MS);
    }, { passive: true });
    tr.addEventListener('touchmove', () => {
        if (_lpTimer) { clearTimeout(_lpTimer); _lpTimer = null; }
    }, { passive: true });
    tr.addEventListener('touchend', () => {
        if (_lpTimer) { clearTimeout(_lpTimer); _lpTimer = null; }
        // Short delay so a tap-to-dismiss works cleanly
        setTimeout(hideTooltip, TOOLTIP_DISMISS_MS);
    });
}

function mkTd(text, cls) {
    const td = document.createElement('td');
    if (cls) td.className = cls;
    td.textContent = text;
    return td;
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
        position: 'Position', qty: '#', margin: 'Margin', opt: '$/shr', theta: 'Theta', theta_norm: 'θ/10k',
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

function nextOptionFriday() {
    const today = new Date();
    const d = today.getDay(); // 0=Sun … 5=Fri, 6=Sat
    const days = d === 5 ? 7 : d === 6 ? 6 : 5 - d;
    const result = new Date(today);
    result.setDate(today.getDate() + days);
    return result.toISOString().slice(0, 10);
}

function openAddModal() {
    _editId = null;
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
    const resp = await fetch(url, {
        method,
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(data),
    });
    if (resp.ok) {
        _posModal.hide();
        loadPositions();
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
    document.getElementById('fStrike').value    = '';
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

function confirmDialog(msg) {
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
            if (!decided) resolve(false);
        }, { once: true });

        _confirmModal.show();
    });
}
