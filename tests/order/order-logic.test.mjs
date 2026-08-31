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

// ── Page scaling (A-6) ───────────────────────────────────────────────────────
// Fit is the default and must NOT be sent: an absent scale block means "leave
// it to the printer", which is what every order did before scaling existed and
// what keeps those orders planning identically. Custom % is staff-only, so this
// UI can never emit it whatever it is handed.

const scaleBase = {
  fileName: 'a.pdf', fileExt: 'pdf', totalPages: 2,
  included: { 1: true, 2: true }, colourPages: {}, colourMode: 'bw',
  nup: 1, copies: 1, paperSize: 'A4', sides: 'single',
  orientation: 'auto', direction: 'horizontal', binding: 'none',
  amountEstimated: 6, priceExact: true,
};

test('buildPrintSpec omits scale entirely at the Fit default', () => {
  const spec = buildPrintSpec({ ...scaleBase, scale: 'fit' });
  assert.equal('scale' in spec, false);
});

test('buildPrintSpec omits scale when the field is absent', () => {
  const spec = buildPrintSpec({ ...scaleBase });
  assert.equal('scale' in spec, false);
});

test('buildPrintSpec sends Actual size', () => {
  const spec = buildPrintSpec({ ...scaleBase, scale: 'actual' });
  assert.deepEqual(spec.scale, { mode: 'actual' });
});

test('the customer UI can never emit a custom scale', () => {
  // Custom % is staff-only (owner, 2026-08-30). Even handed one, this builder
  // must not pass it on.
  for (const bogus of ['custom', 'CUSTOM', '150', 150, null, undefined, {}]) {
    const spec = buildPrintSpec({ ...scaleBase, scale: bogus });
    assert.equal('scale' in spec, false, `emitted a scale for ${JSON.stringify(bogus)}`);
  }
});

test('the operator note flags Actual size so the counter sees it', () => {
  const note = buildOperatorNote({ ...scaleBase, scale: 'actual' });
  assert.match(note, /ACTUAL SIZE/);
});

test('the operator note says nothing at the Fit default', () => {
  const note = buildOperatorNote({ ...scaleBase, scale: 'fit' });
  assert.equal(/ACTUAL SIZE/.test(note), false);
});
