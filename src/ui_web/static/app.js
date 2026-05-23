'use strict';

let _positions = [];
let _colSort = null;   // { col: string, dir: 'asc'|'desc' } | null
let _editId = null;    // null = adding new, number = editing existing
let _posModal = null;
let _confirmModal = null;

// ---------------------------------------------------------------------------
// Init
// ---------------------------------------------------------------------------

document.addEventListener('DOMContentLoaded', async () => {
    _posModal     = new bootstrap.Modal(document.getElementById('positionModal'));
    _confirmModal = new bootstrap.Modal(document.getElementById('confirmModal'));

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

    document.getElementById('confirmModal').addEventListener('hide.bs.modal', () => {
        if (document.activeElement?.closest('#confirmModal')) document.activeElement.blur();
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
    try {
        const resp = await fetch(`/api/positions?sort=${sort}`);
        if (resp.status === 401) { location.href = '/login'; return; }
        const data = await resp.json();
        _positions = data.positions;
        updateSummary(data.summary);
    } catch (e) {
        _positions = [];
    }
    renderTable();
}

async function loadConfig() {
    const resp = await fetch('/api/config');
    if (!resp.ok) return;
    const cfg = await resp.json();
    document.getElementById('cfgMargin').value = cfg.MaximumMarginBasis || 250000;
    document.getElementById('cfgMultiplier').value =
        parseFloat(cfg.MarginMultiplier || 1.5).toFixed(1);
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
            else if (col === 'theta')    { va = a.theta_dollars ?? -Infinity;
                                           vb = b.theta_dollars ?? -Infinity; }
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

    for (const pos of items) {
        const tr = document.createElement('tr');
        tr.style.backgroundColor = pos.bg;
        tr.style.color = pos.fg;

        // Position cell: optional indicator swatches + name
        const posCell = document.createElement('td');
        if (pos.itm) {
            const dot = document.createElement('span');
            dot.className = 'mw-ind';
            dot.style.backgroundColor = '#8A2BE2';
            posCell.appendChild(dot);
        }
        if (pos.is_profitable) {
            const dot = document.createElement('span');
            dot.className = 'mw-ind';
            dot.style.backgroundColor = '#d6109b';
            posCell.appendChild(dot);
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
        const thetaCell  = mkTd(pos.theta_str,             'text-end');

        const actCell = document.createElement('td');
        actCell.className = 'text-center';

        const editBtn = mkRowBtn('✎', () => editPosition(pos.id));
        const delBtn  = mkRowBtn('✕', () => deletePosition(pos.id));
        actCell.append(editBtn, delBtn);

        if (pos.show_merge) {
            const [sym, exp, strike] = pos.merge_key;
            const mergeBtn = mkRowBtn('⊕', () => mergePositions(sym, exp, strike));
            actCell.appendChild(mergeBtn);
        }

        tr.append(posCell, qtyCell, marginCell, optCell, thetaCell, actCell);
        tbody.appendChild(tr);
    }
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
    btn.className = 'btn btn-sm py-0 px-1';
    btn.style.fontSize = '11px';
    btn.addEventListener('click', handler);
    return btn;
}

function updateColHeaders() {
    const labels = {
        position: 'Position', qty: '#', margin: 'Margin', opt: '$/shr', theta: 'Theta',
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
    const margin     = parseInt(document.getElementById('cfgMargin').value);
    const multiplier = parseFloat(document.getElementById('cfgMultiplier').value);
    const sort       = document.querySelector('input[name="sort"]:checked').value;
    if (isNaN(margin) || isNaN(multiplier)) { alert('Enter valid numeric values.'); return; }
    if (multiplier < 0.5 || multiplier > 4.0) { alert('Multiplier must be 0.5–4.0.'); return; }
    const resp = await fetch('/api/config', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            MaximumMarginBasis: margin,
            MarginMultiplier: multiplier,
            SortOrder: sort,
        }),
    });
    if (resp.ok) {
        loadPositions();
        const msg = document.getElementById('cfgSavedMsg');
        msg.style.display = 'inline';
        setTimeout(() => { msg.style.display = 'none'; }, 3000);
    }
}

// ---------------------------------------------------------------------------
// CRUD
// ---------------------------------------------------------------------------

function openAddModal() {
    _editId = null;
    document.getElementById('positionModalTitle').textContent = 'Add Position';
    document.getElementById('positionForm').reset();
    document.getElementById('fQty').value = '1';
    document.getElementById('fLongStrike').value = '';
    document.getElementById('btnAssigned').classList.add('d-none');
    document.getElementById('btnClearCover').classList.add('d-none');
    updateFormFields();
    _posModal.show();
    setTimeout(() => document.getElementById('fSymbol').focus(), 300);
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
    document.getElementById('fLongStrike').value  = pos.long_strike || '';

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
        long_strike: parseFloat(document.getElementById('fLongStrike').value) || null,
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
    const isStock  = ot === 'STOCK';
    const isSpread = ot === 'CALL_SPREAD' || ot === 'PUT_SPREAD';
    document.getElementById('rowShares').classList.toggle('d-none', !isStock);
    document.getElementById('rowCost').classList.toggle('d-none', !isStock);
    document.getElementById('rowLongStrike').classList.toggle('d-none', !isSpread);
    document.getElementById('strikeLabel').textContent = isSpread ? 'Strike (short)' : 'Strike';
    document.getElementById('qtyLabel').textContent = isStock ? 'Quantity' : 'Contracts';
    if (!isSpread) document.getElementById('fLongStrike').value = '';
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
