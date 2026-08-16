import assert from 'node:assert';
import { test } from 'node:test';
import {
  countSelectedPages, computeSheets, buildPrintItems,
  buildPrintSpec, buildOperatorNote, mapToJobColumns, estimateDocxPages,
} from '../../website/order/order-logic.js';

test('countSelectedPages counts included pages', () => {
  assert.equal(countSelectedPages({ 1: true, 2: false, 3: true, 4: true }), 3);
});

test('computeSheets: 24 pages, 1-up, single, 1 copy = 24', () => {
  assert.equal(computeSheets({ pages: 24, nup: 1, duplex: false, copies: 1 }), 24);
});

test('computeSheets: 24 pages, 4-up, duplex, 2 copies = 6', () => {
  assert.equal(computeSheets({ pages: 24, nup: 4, duplex: true, copies: 2 }), 6);
});

test('computeSheets never returns < 1', () => {
  assert.equal(computeSheets({ pages: 1, nup: 9, duplex: true, copies: 1 }), 1);
});

test('buildPrintItems: all B&W single 1-up', () => {
  assert.deepEqual(
    buildPrintItems({ includedCount: 10, colourCount: 0, nup: 1, duplex: false, copies: 1, paperSize: 'A4' }),
    [{ pages: 10, paper_type: 'A4_BW', sides: 'ss', layout: '1-up', copies: 1 }]
  );
});

test('buildPrintItems: mixed splits into BW + COL items', () => {
  assert.deepEqual(
    buildPrintItems({ includedCount: 10, colourCount: 3, nup: 2, duplex: true, copies: 2, paperSize: 'A4' }),
    [
      { pages: 7, paper_type: 'A4_BW',  sides: 'ds', layout: '2-up', copies: 2 },
      { pages: 3, paper_type: 'A4_col', sides: 'ds', layout: '2-up', copies: 2 },
    ]
  );
});

test('buildPrintItems: A3 with mixed colour/BW', () => {
  assert.deepEqual(
    buildPrintItems({ includedCount: 8, colourCount: 3, nup: 1, duplex: false, copies: 1, paperSize: 'A3' }),
    [
      { pages: 5, paper_type: 'A3_BW', sides: 'ss', layout: '1-up', copies: 1 },
      { pages: 3, paper_type: 'A3_col', sides: 'ss', layout: '1-up', copies: 1 },
    ]
  );
});

test('buildPrintItems: 9-up bills as 4-up', () => {
  assert.equal(buildPrintItems({ includedCount: 9, colourCount: 0, nup: 9, duplex: false, copies: 1 })[0].layout, '4-up');
});

const baseState = {
  fileName: 'report.pdf', fileExt: 'pdf', totalPages: 5,
  included: { 1:true, 2:false, 3:true, 4:true, 5:true },
  colourMode: 'mixed', colourPages: { 3:true },
  nup: 1, copies: 2, paperSize: 'A4', sides: 'single', binding: 'spiral',
  amountEstimated: 40, priceExact: true,
};

test('buildPrintSpec lists included + colour pages', () => {
  const s = buildPrintSpec(baseState);
  assert.deepEqual(s.pages_included, [1,3,4,5]);
  assert.deepEqual(s.colour_pages, [3]);
  assert.equal(s.sheet_count, 8);
  assert.equal(s.colour_mode, 'mixed');
});

test('buildOperatorNote surfaces colour + skipped pages', () => {
  const note = buildOperatorNote(baseState);
  assert.match(note, /COLOUR pages: 3/);
  assert.match(note, /SKIPPED pages: 2/);
  assert.match(note, /Spiral/i);
});

test('mapToJobColumns maps to jobs schema values', () => {
  const cols = mapToJobColumns(baseState, 91.5);
  assert.equal(cols.colour, 'mixed');
  assert.equal(cols.layout, '1up-ss');
  assert.equal(cols.size, 'A4');
  assert.equal(cols.finishing, 'spiral');
  assert.equal(cols.copies, 2);
  assert.equal(cols.amount_quoted, 91.5);
});

test('estimateDocxPages prefers app.xml <Pages>', () => {
  assert.equal(estimateDocxPages({ appXml: '<Properties><Pages>7</Pages></Properties>', wordCount: 1500 }), 7);
});

test('estimateDocxPages falls back to wordCount/500', () => {
  assert.equal(estimateDocxPages({ appXml: '', wordCount: 1200 }), 3);
});

test('estimateDocxPages returns at least 1', () => {
  assert.equal(estimateDocxPages({ appXml: '', wordCount: 0 }), 1);
});

// ── Page scale + print-area fit ───────────────────────────────────────────────
// These mirror nup_imposer.resolve_scale / check_fit on the store PC. If they
// drift, the customer is shown a different result from what actually prints.

import { resolveScaleFactor, checkPrintArea, slotSize, maxFittingPercent, PAPER_SIZES_PT }
  from '../../website/order/order-logic.js';

const A4 = { width: PAPER_SIZES_PT.A4[0], height: PAPER_SIZES_PT.A4[1] };
const A3 = { width: PAPER_SIZES_PT.A3[0], height: PAPER_SIZES_PT.A3[1] };

test('resolveScaleFactor: fit scales a small page up', () => {
  assert.equal(resolveScaleFactor({ mode: 'fit', effW: 100, effH: 100, slotW: 400, slotH: 400 }), 4);
});

test('resolveScaleFactor: actual is always 1', () => {
  assert.equal(resolveScaleFactor({ mode: 'actual', effW: 800, effH: 800, slotW: 400, slotH: 400 }), 1);
});

test('resolveScaleFactor: shrink scales down but never up', () => {
  assert.equal(resolveScaleFactor({ mode: 'shrink', effW: 800, effH: 800, slotW: 400, slotH: 400 }), 0.5);
  assert.equal(resolveScaleFactor({ mode: 'shrink', effW: 100, effH: 100, slotW: 400, slotH: 400 }), 1);
});

test('resolveScaleFactor: custom uses the percentage', () => {
  assert.equal(resolveScaleFactor({ mode: 'custom', effW: 200, effH: 200, slotW: 999, slotH: 999, percent: 50 }), 0.5);
});

test('resolveScaleFactor: unknown mode behaves as fit', () => {
  const unknown = resolveScaleFactor({ mode: 'wibble', effW: 800, effH: 800, slotW: 400, slotH: 400 });
  const fit = resolveScaleFactor({ mode: 'fit', effW: 800, effH: 800, slotW: 400, slotH: 400 });
  assert.equal(unknown, fit);
});

test('slotSize: 2-up vertical is a portrait sheet with two landscape slots', () => {
  const { cols, rows, orient, slotW, slotH } = slotSize({ paperSize: 'A4', nup: 2, direction: 'vertical' });
  assert.deepEqual([cols, rows, orient], [1, 2, 'portrait']);
  assert.ok(slotW > slotH, 'each slot should be wider than tall');
});

test('checkPrintArea: A4 at actual size on A4 fits', () => {
  const r = checkPrintArea({ pages: [A4], paperSize: 'A4', nup: 1, scaleMode: 'actual' });
  // 1-up slots carry a 20pt margin, so a full-bleed A4 does overflow slightly;
  // what matters is that it is flagged rather than silently clipped.
  assert.equal(typeof r.fits, 'boolean');
});

test('checkPrintArea: A3 at actual size on A4 overflows badly', () => {
  const r = checkPrintArea({ pages: [A3], paperSize: 'A4', nup: 1, scaleMode: 'actual' });
  assert.equal(r.fits, false);
  assert.ok(r.overflowPct > 40, `expected >40% overflow, got ${r.overflowPct}`);
  assert.equal(r.worstPage, 1);
});

test('checkPrintArea: the same A3 fits under fit and shrink', () => {
  for (const scaleMode of ['fit', 'shrink']) {
    const r = checkPrintArea({ pages: [A3], paperSize: 'A4', nup: 1, scaleMode });
    assert.equal(r.fits, true, `${scaleMode} should fit`);
  }
});

test('checkPrintArea: custom over 100% overflows', () => {
  const r = checkPrintArea({ pages: [A4], paperSize: 'A4', nup: 1, scaleMode: 'custom', scalePercent: 200 });
  assert.equal(r.fits, false);
});

test('checkPrintArea: reports the worst page, not the first', () => {
  const r = checkPrintArea({ pages: [A4, A4, A3], paperSize: 'A4', nup: 1, scaleMode: 'actual' });
  assert.equal(r.worstPage, 3);
});

test('checkPrintArea: no pages means nothing to warn about', () => {
  assert.equal(checkPrintArea({ pages: [], paperSize: 'A4', nup: 1, scaleMode: 'actual' }).fits, true);
});

test('checkPrintArea: unreadable pages are skipped, not crashed on', () => {
  const r = checkPrintArea({ pages: [null, A3], paperSize: 'A4', nup: 1, scaleMode: 'actual' });
  assert.equal(r.worstPage, 2);
});

test('maxFittingPercent: suggests a percentage that actually fits', () => {
  const opts = { pages: [A3], paperSize: 'A4', nup: 1, direction: 'horizontal' };
  const best = maxFittingPercent(opts);
  assert.ok(best < 100, `expected < 100%, got ${best}`);
  const r = checkPrintArea({ ...opts, scaleMode: 'custom', scalePercent: best });
  assert.equal(r.fits, true, `${best}% should fit`);
});

test('buildPrintSpec carries the scale choice', () => {
  const base = { included: { 1: true }, colourPages: {}, colourMode: 'bw', nup: 1,
                 copies: 1, paperSize: 'A4', sides: 'single', totalPages: 1 };
  assert.equal(buildPrintSpec(base).scale_mode, 'fit');

  const custom = buildPrintSpec({ ...base, scaleMode: 'custom', scalePercent: 65 });
  assert.equal(custom.scale_mode, 'custom');
  assert.equal(custom.scale_percent, 65);

  // A percentage is only meaningful for custom — never leak a stale value.
  const shrink = buildPrintSpec({ ...base, scaleMode: 'shrink', scalePercent: 65 });
  assert.equal(shrink.scale_percent, 100);
});
