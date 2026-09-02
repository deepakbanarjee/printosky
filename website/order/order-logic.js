export function countSelectedPages(included) {
  return Object.values(included).filter(Boolean).length;
}

export function computeSheets({ pages, nup, duplex, copies }) {
  const afterNup = Math.ceil(pages / nup);
  const afterDuplex = duplex ? Math.ceil(afterNup / 2) : afterNup;
  return Math.max(1, afterDuplex * copies);
}

const LAYOUT_FOR_NUP = { 1: '1-up', 2: '2-up', 4: '4-up', 6: '4-up', 9: '4-up' };

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

// Customers get Fit and Actual; Custom % is staff-only (owner, 2026-08-30) and
// the toggle is hidden outside ?staff=1, so a customer session cannot reach it.
// 'fit' is the default and is NOT sent — an absent scale block means "leave it
// to the printer", which is what every order did before scaling existed and
// what keeps those orders planning identically.
const SCALE_LABEL = { fit:'Fit to page', actual:'Actual size', custom:'Custom %' };

// Mirrors pdf_scaler.MIN_PERCENT / MAX_PERCENT. Clamped rather than rejected,
// so a typo prints something sane instead of failing the job.
const SCALE_MIN_PERCENT = 25;
const SCALE_MAX_PERCENT = 400;

export function scaleBlock(s) {
  if (s.scale === 'actual') return { scale: { mode: 'actual' } };
  if (s.scale !== 'custom') return {};                 // 'fit' sends nothing
  const n = Math.round(Number(s.scalePercent));
  if (!Number.isFinite(n)) return {};                  // never guess a percentage
  const pct = Math.max(SCALE_MIN_PERCENT, Math.min(SCALE_MAX_PERCENT, n));
  if (pct === 100) return {};                          // 100% IS actual size, unscaled
  return { scale: { mode: 'custom', percent: pct } };
}

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
    binding: s.binding, sheet_count, amount_estimated: s.amountEstimated, price_exact: s.priceExact,
    ...scaleBlock(s),
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
  if (s.scale === 'actual') parts.push('ACTUAL SIZE (not resized to fit)');
  if (s.scale === 'custom') {
    const b = scaleBlock(s);
    parts.push(b.scale ? `SCALED TO ${b.scale.percent}% of the original`
                       : 'ACTUAL SIZE (custom 100% = unscaled)');
  }
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
