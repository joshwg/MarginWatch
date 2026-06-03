'use strict';

// ---------------------------------------------------------------------------
// MarginWatch — shared UI constants
// ---------------------------------------------------------------------------

// ── Colours ──────────────────────────────────────────────────────────────────

/** ITM indicator: covered-call ITM is good (profitable assignment likely) */
const COLOR_ITM_GOOD = '#16a34a';

/** ITM indicator: put/call/spread ITM is bad (short option losing) */
const COLOR_ITM_BAD  = '#dc2626';

// ── Timing (ms) ──────────────────────────────────────────────────────────────

/** Hover dwell time before the price tooltip appears */
const HOVER_DELAY_MS       = 500;

/** Touch hold time before the price tooltip appears */
const LONGPRESS_DELAY_MS   = 500;

/** How long the price tooltip stays visible after a touch */
const TOOLTIP_DISMISS_MS   = 3000;

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

const ICON_EDIT   = '✎';   // row edit button
const ICON_DELETE = '✕';   // row delete button
const ICON_MERGE  = '⊕';   // row merge button
const ICON_PROFIT = '⬆';   // profitable-position indicator
