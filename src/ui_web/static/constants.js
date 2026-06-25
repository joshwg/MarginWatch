'use strict';

// ---------------------------------------------------------------------------
// MarginWatch — shared UI constants
// ---------------------------------------------------------------------------

// ── Colours ──────────────────────────────────────────────────────────────────

/** ITM indicator: covered-call ITM is good (profitable assignment likely) */
const COLOR_ITM_GOOD = '#16a34a';

/** ITM indicator: put/call/spread ITM is bad (short option losing) */
const COLOR_ITM_BAD = '#dc2626';

// ── Timing (ms) ──────────────────────────────────────────────────────────────

/** Hover dwell time before the price tooltip appears */
const HOVER_DELAY_MS = 500;

/** Touch hold time before the price tooltip appears */
const LONGPRESS_DELAY_MS = 500;

/** How long the price tooltip stays visible after a touch */
const TOOLTIP_DISMISS_MS = 3000;

/** How long the "Saved" confirmation message stays visible */
const SAVED_MSG_DISMISS_MS = 3000;

/** Delay before auto-focusing the first field after a modal opens */
const MODAL_FOCUS_DELAY_MS = 300;

// ── Table layout ─────────────────────────────────────────────────────────────

/** Draw a thick rule beneath every Nth row to aid counting */
const ROW_RULE_INTERVAL = 5;

// ── Tooltip positioning (px) ─────────────────────────────────────────────────

/** Horizontal gap between the cursor/touch point and the tooltip edge */
const TOOLTIP_OFFSET_X = 12;

/** Minimum gap between the tooltip and the viewport edge */
const TOOLTIP_EDGE_GAP = 8;

// ── Icons / glyphs ───────────────────────────────────────────────────────────

const ICON_EDIT = '✎';   // row edit button
const ICON_DELETE = '✕';   // row delete button
const ICON_MERGE = '⊕';   // row merge button
const ICON_PROFIT = '⬆';   // profitable-position indicator

// ── Risk / probability-of-assignment spectrum ─────────────────────────────────
//
// Each band covers [threshold, previous_threshold).  The array is ordered from
// highest risk to lowest so the first matching entry wins.
//
//  Color    Delta range   Market state
//  Red      85–100 %      Deep ITM   — assignment almost certain
//  Orange   65– 84 %      Mod ITM    — high risk, active defence needed
//  Yellow   45– 64 %      ATM        — coin-flip danger zone
//  Lime     25– 44 %      Slight OTM — getting uncomfortably close
//  Green    10– 24 %      OTM        — comfortable theta-burn zone
//  Blue      0–  9 %      Deep OTM   — practically zero assignment risk

const RISK_BANDS = [
    { threshold: 0.85, color: '#FF4D4D', label: 'Deep ITM'   },  // 🔴 Red
    { threshold: 0.65, color: '#FF944D', label: 'Mod ITM'    },  // 🟠 Orange
    { threshold: 0.45, color: '#FFD633', label: 'ATM'        },  // 🟡 Yellow
    { threshold: 0.25, color: '#DEFF6E', label: 'Slight OTM' },  // 🟡 Lime
    { threshold: 0.10, color: '#1AAB5D', label: 'OTM'        },  // 🟢 Green
    { threshold: 0.00, color: '#3498DB', label: 'Deep OTM'   },  // 🔵 Blue
];

/**
 * Return the risk band colour for a given delta probability (0–1),
 * or null when no delta is available (STOCK without a covered call, etc.).
 */
function riskColor(delta) {
    if (delta == null) return null;
    for (const band of RISK_BANDS) {
        if (delta >= band.threshold) return band.color;
    }
}
