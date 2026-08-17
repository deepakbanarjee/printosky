export function countSelectedPages(included) {
  return Object.values(included).filter(Boolean).length;
}

export function computeSheets({ pages, nup, duplex, copies }) {
  const afterNup = Math.ceil(pages / nup);
  const afterDuplex = duplex ? Math.ceil(afterNup / 2) : afterNup;
  return Math.max(1, afterDuplex * copies);
}

const LAYOUT_FOR_NUP = { 1: '1-up', 2: '2-up', 4: '4-up', 6: '4-up', 9: '4-up' };

// ── Print-area fit ────────────────────────────────────────────────────────────
// Mirrors nup_imposer.resolve_scale / check_fit on the store PC. Keep the two
// in step: this is what the customer is shown, that is what actually prints.

export const PAPER_SIZES_PT = {
  A4: [595.28, 841.89], A3: [841.89, 1190.55], A5: [419.53, 595.28],
  Letter: [612.0, 792.0], Legal: [612.0, 1008.0],
};

// Same grid table as print_planner.plan_print_job — every imposed sheet is
// portrait (the "portrait canvas rule"; see the long note in print_planner).
// Keep these two in step or the quoted sheet count and the print-area warning
// stop matching what actually prints.
const NUP_GRID = {
  1: [1, 1, 'portrait'], 2: [1, 2, 'portrait'], 4: [2, 2, 'portrait'],
  6: [2, 3, 'portrait'], 9: [3, 3, 'portrait'],
};
const MARGIN_PT = 20, GUTTER_PT = 10, FIT_TOLERANCE_PT = 1;

/**
 * Should a source page be turned a quarter turn to fill its slot?
 * Mirrors nup_imposer.should_rotate_into_slot — a PORTRAIT page is never
 * rotated, so a portrait document reads without turning the sheet.
 */
export function shouldRotateIntoSlot(pageW, pageH, slotW, slotH) {
  if (pageH > pageW) return false;      // portrait source — never rotate
  return slotH > slotW;                 // landscape source into a portrait slot
}

export function resolveScaleFactor({ mode, effW, effH, slotW, slotH, percent = 100 }) {
  if (effW <= 0 || effH <= 0) return 1;
  const fit = Math.min(slotW / effW, slotH / effH);
  switch ((mode || 'fit').toLowerCase()) {
    case 'actual': return 1;
    case 'shrink': return Math.min(1, fit);
    case 'custom': return Math.max(0.01, (Number(percent) || 100) / 100);
    default: return fit;
  }
}

/** Slot geometry for a given paper size / N-up / direction, in points. */
export function slotSize({ paperSize = 'A4', nup = 1, direction = 'horizontal' }) {
  const [cols, rows, orient] = NUP_GRID[nup] || NUP_GRID[4];

  const [pw, ph] = PAPER_SIZES_PT[paperSize] || PAPER_SIZES_PT.A4;
  const outW = orient === 'landscape' ? Math.max(pw, ph) : Math.min(pw, ph);
  const outH = orient === 'landscape' ? Math.min(pw, ph) : Math.max(pw, ph);

  let slotW = (outW - 2 * MARGIN_PT - GUTTER_PT * (cols - 1)) / cols;
  let slotH = (outH - 2 * MARGIN_PT - GUTTER_PT * (rows - 1)) / rows;
  if (slotW <= 0 || slotH <= 0) { slotW = outW / cols; slotH = outH / rows; }
  return { slotW, slotH, cols, rows, orient };
}

/**
 * Will `pages` fit the printable area under these settings?
 * `pages` is [{ width, height }] in PDF points.
 * Returns { fits, overflowPct, worstPage, factor, slotW, slotH }.
 */
export function checkPrintArea({ pages, paperSize = 'A4', nup = 1,
                                 direction = 'horizontal', scaleMode = 'fit',
                                 scalePercent = 100 }) {
  const { slotW, slotH } = slotSize({ paperSize, nup, direction });
  let worst = { fits: true, overflowPct: 0, worstPage: null, factor: 1, slotW, slotH };

  (pages || []).forEach((pg, i) => {
    if (!pg || !pg.width || !pg.height) return;
    const rotate = shouldRotateIntoSlot(pg.width, pg.height, slotW, slotH);
    const effW = rotate ? pg.height : pg.width;
    const effH = rotate ? pg.width : pg.height;

    const factor = resolveScaleFactor({ mode: scaleMode, effW, effH, slotW, slotH, percent: scalePercent });
    const overW = Math.max(0, effW * factor - slotW);
    const overH = Math.max(0, effH * factor - slotH);
    const pct = Math.max(overW / slotW, overH / slotH) * 100;

    if (pct > worst.overflowPct) {
      worst = {
        fits: overW <= FIT_TOLERANCE_PT && overH <= FIT_TOLERANCE_PT,
        overflowPct: pct, worstPage: i + 1, factor, slotW, slotH,
      };
    }
  });
  return worst;
}

/** Largest whole percent that still fits every page — for the "fix it" action. */
export function maxFittingPercent(opts) {
  const { slotW, slotH } = slotSize(opts);
  let smallest = Infinity;
  (opts.pages || []).forEach((pg) => {
    if (!pg || !pg.width || !pg.height) return;
    const rotate = shouldRotateIntoSlot(pg.width, pg.height, slotW, slotH);
    const effW = rotate ? pg.height : pg.width;
    const effH = rotate ? pg.width : pg.height;
    smallest = Math.min(smallest, Math.min(slotW / effW, slotH / effH));
  });
  if (!isFinite(smallest)) return 100;
  return Math.max(10, Math.floor(smallest * 100));
}

export function buildPrintItems({ includedCount, colourCount, nup, duplex, copies, paperSize = 'A4' }) {
  const sides = duplex ? 'ds' : 'ss';
  const layout = LAYOUT_FOR_NUP[nup] || '1-up';
  const bw = includedCount - colourCount;
  const items = [];
  if (bw > 0 || colourCount === 0) {
    items.push({ pages: bw, paper_type: `${paperSize}_BW`, sides, layout, copies });
  }
  if (colourCount > 0) {
    items.push({ pages: colourCount, paper_type: `${paperSize}_col`, sides, layout, copies });
  }
  return items;
}

function includedList(included) {
  return Object.keys(included).filter(p => included[p]).map(Number).sort((a,b)=>a-b);
}
function colourListFn(colourPages) {
  return Object.keys(colourPages).filter(p => colourPages[p]).map(Number).sort((a,b)=>a-b);
}
const FINISHING_LABEL = { none:'No binding', staple:'Staple', spiral:'Spiral', wiro:'Wiro', soft:'Soft', perfect:'Perfect', project:'Project', record:'Record', thesis:'Thesis' };

const ORIENTATION_LABEL = { auto:'Auto orientation', portrait:'Portrait', landscape:'Landscape' };

export function buildPrintSpec(s) {
  const inc = includedList(s.included);
  const col = s.colourMode === 'mixed' ? colourListFn(s.colourPages) : [];
  const sheet_count = computeSheets({ pages: inc.length, nup: s.nup, duplex: s.sides === 'duplex', copies: s.copies });
  return {
    file_name: s.fileName, file_ext: s.fileExt, total_pages: s.totalPages,
    pages_included: inc, colour_mode: s.colourMode, colour_pages: col,
    nup: s.nup, copies: s.copies, paper_size: s.paperSize, sides: s.sides,
    orientation: s.orientation || 'auto',
    nup_direction: s.direction || 'horizontal',   // 'horizontal' | 'vertical' (N-up fill order)
    scale_mode: s.scaleMode || 'fit',             // 'fit' | 'actual' | 'shrink' | 'custom'
    scale_percent: s.scaleMode === 'custom' ? (s.scalePercent || 100) : 100,
    binding: s.binding, sheet_count, amount_estimated: s.amountEstimated, price_exact: s.priceExact,
  };
}

export function buildOperatorNote(s) {
  const inc = includedList(s.included);
  const skipped = [];
  for (let p = 1; p <= s.totalPages; p++) if (!s.included[p]) skipped.push(p);
  const parts = [];
  parts.push(`${inc.length} of ${s.totalPages} pages`);
  if (skipped.length) parts.push(`SKIPPED pages: ${skipped.join(', ')}`);
  if (s.colourMode === 'mixed') {
    const col = colourListFn(s.colourPages);
    parts.push(`COLOUR pages: ${col.join(', ') || 'none'} · rest B&W`);
  } else {
    parts.push(s.colourMode === 'col' ? 'All Colour' : 'All B&W');
  }
  if (s.nup !== 1) parts.push(`${s.nup}-up`);
  parts.push(s.sides === 'duplex' ? 'Duplex' : 'Single-sided');
  if (s.orientation && s.orientation !== 'auto') parts.push(ORIENTATION_LABEL[s.orientation] || s.orientation);
  parts.push(FINISHING_LABEL[s.binding] || s.binding);
  if (!s.priceExact) parts.push('(page count ESTIMATED — non-PDF; confirm before print)');
  return parts.join(' · ');
}

export function mapToJobColumns(s, amountQuoted) {
  const sidesCode = s.sides === 'duplex' ? 'ds' : 'ss';
  return {
    copies: s.copies,
    finishing: s.binding,
    size: s.paperSize,
    colour: s.colourMode,
    orientation: s.orientation || 'auto',
    layout: `${s.nup}up-${sidesCode}`,
    amount_quoted: amountQuoted,
  };
}

export function estimateDocxPages({ appXml, wordCount }) {
  const m = (appXml || '').match(/<Pages>(\d+)<\/Pages>/);
  if (m) return Math.max(1, parseInt(m[1], 10));
  return Math.max(1, Math.ceil((wordCount || 0) / 500));
}
