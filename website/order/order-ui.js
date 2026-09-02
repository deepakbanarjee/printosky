// order-ui.js — page glue for order-v2.html (Acrobat-style print order).
// All print math lives in order-logic.js; this module only wires the DOM,
// renders pdf.js thumbnails, debounces the live quote, and runs the submit flow.
import {
  countSelectedPages,
  computeSheets,
  buildPrintItems,
  buildPrintSpec,
  buildOperatorNote,
  estimateDocxPages,
} from './order-logic.js';
// Logged-in account detection: prefills/hides the Step 2 identity fields and
// supplies the Bearer token sent on /order/create. No-op for guests.
import { initAccount, authToken } from './order-auth.js';
// pdf.js is loaded as a UMD global (window.pdfjsLib) by a <script> tag in
// order-v2.html — pinned to 3.11.174, whose classic worker renders reliably
// cross-origin (the 4.x .mjs module-worker build is 404 on cdnjs for pdf.min.js
// and unreliable as a cross-origin module worker). `pdfjsLib` resolves to the
// global set before any file is selected.

const API = 'https://printosky.vercel.app';
const SUPABASE_PUBLIC =
  'https://mlhuwlnwwwxdnqafelko.supabase.co/storage/v1/object/public/incoming-files/';
const WA_NUMBER = '919495706405';

// ── Staff mode (launched from jobs.html with ?staff=1) ────────────────────────
const STAFF = new URLSearchParams(location.search).get('staff') === '1';

// ── Application state (shape matches order-logic.js expectations) ──────────────
const state = {
  fileName: '',
  fileExt: '',
  totalPages: 0,
  included: {},      // { pageNo: bool }
  colourPages: {},   // { pageNo: bool } — only meaningful in 'mixed'
  colourMode: 'bw',  // 'bw' | 'col' | 'mixed'
  nup: 1,
  copies: 1,
  paperSize: 'A4',
  sides: 'single',   // 'single' | 'duplex'
  orientation: 'auto', // 'auto' | 'portrait' | 'landscape'
  scale: 'fit',      // 'fit' | 'actual' | 'custom' — Custom % is staff-only (see syncStaffScale)
  scalePercent: 100, // only meaningful when scale === 'custom'
  direction: 'horizontal', // 'horizontal' | 'vertical' (N-up page fill order)
  binding: 'none',   // 'none' | 'staple' | 'spiral' | 'wiro' | 'soft' | 'perfect' | 'project' | 'record' | 'thesis'
  amountEstimated: 0,
  priceExact: true,
};

// Non-spec runtime handles (kept off `state` so buildPrintSpec stays clean).
const runtime = {
  pdf: null,            // pdf.js document (PDFs only)
  originalBytes: null,  // ArrayBuffer of the uploaded file (for pdf-lib extract / upload)
  contentType: 'application/octet-stream',
  rendered: {},         // { pageNo: true } once a thumbnail canvas is painted
  observer: null,       // IntersectionObserver for lazy thumbnails
  delivery: 0,
  pickup_store: 'thriprayar',  // which location fulfils — 'thriprayar' | 'nattika'
  lastTotal: null,      // last successful quote total (kept on network error)
  firstPageSize: null,  // { w, h } in points — drives the scale preview
};

const EAGER_THUMBS = 12;
const NUP_LAYOUT = { 1: [1, 1], 2: [1, 2], 4: [2, 2], 6: [2, 3], 9: [3, 3] };

// ── Tiny DOM helpers ──────────────────────────────────────────────────────────
const $ = (id) => document.getElementById(id);
const show = (el) => el && el.classList.remove('ov2-hidden');
const hide = (el) => el && el.classList.add('ov2-hidden');

function thumbEl(pg) {
  return document.querySelector('.ov2-thumb[data-pg="' + pg + '"]');
}

// ── File upload + page-count detection ────────────────────────────────────────
function resetForNewFile() {
  runtime.pdf = null;
  runtime.originalBytes = null;
  runtime.rendered = {};
  if (runtime.observer) { runtime.observer.disconnect(); runtime.observer = null; }
  state.included = {};
  state.colourPages = {};
  state.totalPages = 0;
  state.colourMode = 'bw';
  runtime.lastTotal = null;
}

function initPages(n) {
  state.totalPages = n;
  state.included = {};
  state.colourPages = {};
  for (let i = 1; i <= n; i++) {
    state.included[i] = true;
    state.colourPages[i] = false;
  }
}

async function handleFile(file) {
  if (!file) return;
  resetForNewFile();
  const name = file.name || 'upload';
  const ext = (name.split('.').pop() || '').toLowerCase();
  state.fileName = name;
  state.fileExt = ext;
  runtime.contentType = file.type || guessContentType(ext);

  $('ov2-filename').textContent = name;
  $('ov2-filechip').classList.add('show');
  $('ov2-amber').classList.remove('show');
  $('ov2-manual').classList.remove('show');

  const bytes = await file.arrayBuffer();
  runtime.originalBytes = bytes;

  try {
    if (ext === 'pdf') {
      await loadPdf(bytes);
    } else if (ext === 'jpg' || ext === 'jpeg' || ext === 'png') {
      loadImage();
    } else if (ext === 'docx') {
      await loadDocx(bytes);
    } else {
      loadGeneric();
    }
  } catch (err) {
    // Fall back to manual page entry if a parser blows up.
    loadGeneric();
  }

  $('ov2-totalCount').textContent = state.totalPages;
  buildPreview();
  renderThumbs();
  show($('ov2-previewBlock'));
  show($('ov2-controlsBlock'));
  if (STAFF) show($('ov2-batchbar'));   // multi-file batch controls
  if (carryOpts) applyCarry();   // 2nd+ file in a staff batch keeps prior options
  else setNup(1);
  updateSummary();
  requestQuote();
  updateSubmitLabel();
}

function guessContentType(ext) {
  if (ext === 'pdf') return 'application/pdf';
  if (ext === 'jpg' || ext === 'jpeg') return 'image/jpeg';
  if (ext === 'png') return 'image/png';
  if (ext === 'docx') return 'application/vnd.openxmlformats-officedocument.wordprocessingml.document';
  return 'application/octet-stream';
}

async function loadPdf(bytes) {
  // pdf.js consumes the buffer, so hand it a copy and keep originalBytes intact.
  const pdf = await pdfjsLib.getDocument({ data: bytes.slice(0) }).promise;
  runtime.pdf = pdf;
  state.priceExact = true;
  // The scale preview needs the page's true size in points. Rotation-aware:
  // scale 1 gives the size the page presents, which is the size that has to
  // fit on the sheet.
  try {
    const vp = (await pdf.getPage(1)).getViewport({ scale: 1 });
    runtime.firstPageSize = { w: vp.width, h: vp.height };
  } catch { runtime.firstPageSize = null; }
  initPages(pdf.numPages);
  $('ov2-pagecount').textContent = pdf.numPages + (pdf.numPages === 1 ? ' page' : ' pages');
  syncScaleCardVisibility();
  renderScalePreview();
}

function loadImage() {
  state.priceExact = true;
  runtime.firstPageSize = null;   // no page box to preview — caption only
  initPages(1);
  $('ov2-pagecount').textContent = '1 page';
}

async function loadDocx(bytes) {
  state.priceExact = false;
  let appXml = '';
  let wordCount = 0;
  try {
    const zip = await JSZip.loadAsync(bytes);
    const appFile = zip.file('docProps/app.xml');
    if (appFile) appXml = await appFile.async('string');
    const docFile = zip.file('word/document.xml');
    if (docFile) {
      const docXml = await docFile.async('string');
      const text = docXml.replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' ').trim();
      wordCount = text ? text.split(' ').length : 0;
    }
  } catch (err) {
    // Leave appXml/wordCount empty — estimateDocxPages still returns >= 1.
  }
  const pages = estimateDocxPages({ appXml, wordCount });
  initPages(pages);
  $('ov2-pagecount').textContent = '~' + pages + (pages === 1 ? ' page' : ' pages');
  $('ov2-amber').classList.add('show');
}

function loadGeneric() {
  state.priceExact = false;
  const manual = $('ov2-manual');
  manual.classList.add('show');
  const input = $('ov2-manual-pages');
  const n = Math.max(1, parseInt(input.value, 10) || 1);
  initPages(n);
  $('ov2-pagecount').textContent = '~' + n + (n === 1 ? ' page' : ' pages');
  $('ov2-amber').classList.add('show');
  input.oninput = () => {
    const v = Math.max(1, Math.min(500, parseInt(input.value, 10) || 1));
    initPages(v);
    $('ov2-totalCount').textContent = v;
    buildPreview();
    renderThumbs();
    updateSummary();
    requestQuote();
  };
}

// ── Thumbnail grid (lazy render for PDFs) ─────────────────────────────────────
function buildPreview() {
  const strip = $('ov2-strip');
  strip.innerHTML = '';
  runtime.rendered = {};
  if (runtime.observer) { runtime.observer.disconnect(); runtime.observer = null; }

  for (let i = 1; i <= state.totalPages; i++) {
    const t = document.createElement('div');
    t.className = 'ov2-thumb included';
    t.dataset.pg = i;
    t.innerHTML =
      '<div class="ov2-colour-dot"></div><div class="ov2-bw-dot"></div>' +
      (runtime.pdf ? '<div class="ov2-skeleton"></div>' : '') +
      '<div class="ov2-pg-num">' + i + '</div>';
    if (!runtime.pdf) t.classList.add('placeholder');
    t.addEventListener('click', () => thumbClick(i));
    strip.appendChild(t);
  }

  if (runtime.pdf) {
    // Eager-render the first batch, lazy-render the rest on scroll.
    for (let i = 1; i <= Math.min(EAGER_THUMBS, state.totalPages); i++) {
      renderPdfThumb(i);
    }
    if (state.totalPages > EAGER_THUMBS) {
      runtime.observer = new IntersectionObserver((entries) => {
        for (const entry of entries) {
          if (entry.isIntersecting) {
            const pg = parseInt(entry.target.dataset.pg, 10);
            renderPdfThumb(pg);
            runtime.observer.unobserve(entry.target);
          }
        }
      }, { root: $('ov2-strip'), rootMargin: '200px' });
      for (let i = EAGER_THUMBS + 1; i <= state.totalPages; i++) {
        runtime.observer.observe(thumbEl(i));
      }
    }
  } else {
    // Non-PDF: numbered placeholder showing the page number large.
    for (let i = 1; i <= state.totalPages; i++) {
      const t = thumbEl(i);
      const big = document.createElement('div');
      big.textContent = i;
      big.style.fontSize = '22px';
      big.style.fontWeight = '700';
      big.style.color = '#9ca3af';
      t.insertBefore(big, t.querySelector('.ov2-pg-num'));
    }
  }
}

async function renderPdfThumb(pg) {
  if (runtime.rendered[pg] || !runtime.pdf) return;
  runtime.rendered[pg] = true;
  try {
    const page = await runtime.pdf.getPage(pg);
    const baseViewport = page.getViewport({ scale: 1 });
    const targetW = 150; // device px; crisp on the ~110px tile
    const scale = targetW / baseViewport.width;
    const viewport = page.getViewport({ scale });
    const canvas = document.createElement('canvas');
    canvas.width = Math.ceil(viewport.width);
    canvas.height = Math.ceil(viewport.height);
    const ctx = canvas.getContext('2d');
    await page.render({ canvasContext: ctx, viewport }).promise;
    const el = thumbEl(pg);
    if (!el) return;
    const sk = el.querySelector('.ov2-skeleton');
    if (sk) sk.remove();
    el.insertBefore(canvas, el.querySelector('.ov2-pg-num'));
    applyThumbColourClass(pg);
  } catch (err) {
    const el = thumbEl(pg);
    const sk = el && el.querySelector('.ov2-skeleton');
    if (sk) sk.remove();
  }
}

function thumbClick(pg) {
  if (state.colourMode === 'mixed') {
    if (!state.included[pg]) state.included[pg] = true; // re-include on colour tap
    state.colourPages[pg] = !state.colourPages[pg];
  } else {
    state.included[pg] = !state.included[pg];
  }
  renderThumbs();
  updateSummary();
  requestQuote();
}

function applyThumbColourClass(pg) {
  const el = thumbEl(pg);
  if (!el) return;
  const isColour =
    state.colourMode === 'col' ||
    (state.colourMode === 'mixed' && state.colourPages[pg]);
  el.classList.toggle('colour-on', isColour);
  // Any page that will print B&W shows a monochrome preview — all pages in
  // 'bw' mode, and the non-colour pages in 'mixed'. ('col' mode → never.)
  el.classList.toggle('bw-mono', !isColour);
  el.classList.toggle('dot-colour', isColour && state.included[pg]);
  el.classList.toggle('dot-bw', state.colourMode === 'mixed' && state.included[pg] && !state.colourPages[pg]);
}

function renderThumbs() {
  for (let i = 1; i <= state.totalPages; i++) {
    const el = thumbEl(i);
    if (!el) continue;
    el.classList.toggle('excluded', !state.included[i]);
    el.classList.toggle('included', !!state.included[i]);
    applyThumbColourClass(i);
  }
}

function selectAll(on) {
  for (let i = 1; i <= state.totalPages; i++) state.included[i] = on;
  renderThumbs();
  updateSummary();
  requestQuote();
}

// ── Controls ──────────────────────────────────────────────────────────────────
function setColourMode(mode) {
  state.colourMode = mode;
  $('ov2-col-bw').classList.toggle('active', mode === 'bw');
  $('ov2-col-col').classList.toggle('active', mode === 'col');
  $('ov2-col-mixed').classList.toggle('active', mode === 'mixed');
  const hint = $('ov2-hint');
  if (mode === 'mixed') {
    hint.classList.add('mixed-on');
    hint.innerHTML = '🖍️ <b>Mixed mode:</b> tap pages you want in <b>colour</b> (a colour dot appears). Untapped pages stay B&amp;W. Tap again to undo.';
  } else {
    hint.classList.remove('mixed-on');
    hint.innerHTML = "Tap any page to skip it. The page won't be printed.";
  }
  renderThumbs();
  updateSummary();
  requestQuote();
}

function setNup(n) {
  state.nup = n;
  document.querySelectorAll('[data-nup]').forEach((el) => {
    el.classList.toggle('active', parseInt(el.dataset.nup, 10) === n);
  });
  renderNupSheet();
  syncScaleCardVisibility();
  renderScalePreview();
  updateSummary();
  requestQuote();
}

// Redraws the little N-up preview so it symbolically reflects the CURRENT
// n / direction / orientation: the sheet takes a portrait or landscape shape,
// and each slot is numbered (and pops in) in the page fill order — left-to-right
// per row for "horizontal", top-to-bottom per column for "vertical".
function renderNupSheet() {
  const n = state.nup;
  const sheet = $('ov2-nupSheet');
  if (!sheet) return;
  let [rows, cols] = NUP_LAYOUT[n] || [1, 1];
  // 2-up: horizontal = two side-by-side; vertical = two stacked (1 on top, 2 below).
  if (n === 2 && state.direction === 'vertical') { rows = 2; cols = 1; }
  const landscape = state.orientation === 'landscape';
  sheet.style.width  = landscape ? '74px' : '56px';
  sheet.style.height = landscape ? '56px' : '74px';
  sheet.style.gridTemplateColumns = 'repeat(' + cols + ',1fr)';
  sheet.style.gridTemplateRows = 'repeat(' + rows + ',1fr)';
  sheet.innerHTML = '';
  for (let r = 0; r < rows; r++) {
    for (let c = 0; c < cols; c++) {
      const idx = state.direction === 'vertical'
        ? (c * rows + r + 1)   // column-major fill
        : (r * cols + c + 1);  // row-major fill
      const cell = document.createElement('div');
      cell.className = 'ov2-nup-cell';
      cell.textContent = n <= 6 ? idx : '';
      cell.style.animationDelay = (idx * 0.03) + 's';
      sheet.appendChild(cell);
    }
  }
  const dirSym = state.direction === 'vertical' ? ' · ↓' : ' · →';
  $('ov2-nupLabel').textContent = (n === 1 ? '1 / sheet' : n + ' / sheet') + (n > 1 ? dirSym : '');
  const dirRow = $('ov2-dir-row');
  if (dirRow) dirRow.style.display = n > 1 ? 'flex' : 'none';
}

// ── Page scaling (Fit / Actual size) ─────────────────────────────────────────
// The customer sees where their page will land on the sheet. The geometry is
// NOT worked out here: it comes from GET /order/scale-rect, which calls the
// same pdf_scaler.scale_rect() the printer bakes with. A preview drawn by
// different code than the printer gets is a preview that can lie, so there is
// deliberately no JavaScript copy of it.
let scaleRectSeq = 0;

function setScale(mode) {
  state.scale = mode;
  document.querySelectorAll('[data-scale]').forEach((el) => {
    el.classList.toggle('active', el.dataset.scale === mode);
  });
  const row = $('ov2-scale-custom-row');
  if (row) row.style.display = (mode === 'custom' && STAFF) ? 'flex' : 'none';
  renderScalePreview();
  updateSummary();
}

// Custom % bounds mirror pdf_scaler.MIN_PERCENT / MAX_PERCENT. Clamped, never
// rejected — a typo should print something sane, not fail the job — and the
// clamp is announced so nobody wonders why 900% came out as 400%.
const SCALE_MIN_PERCENT = 25;
const SCALE_MAX_PERCENT = 400;

function setScalePercent(raw) {
  const note = $('ov2-scale-pct-note');
  const n = Number(raw);
  if (!Number.isFinite(n)) {
    // Keep the last good percentage rather than silently printing at 100%.
    if (note) { note.className = 'ov2-scale-pct-note warn'; note.textContent = 'Enter a number between 25 and 400.'; }
    return;
  }
  const clamped = Math.max(SCALE_MIN_PERCENT, Math.min(SCALE_MAX_PERCENT, Math.round(n)));
  state.scalePercent = clamped;
  if (note) {
    if (clamped !== Math.round(n)) {
      note.className = 'ov2-scale-pct-note warn';
      note.textContent = `Scaling is limited to ${SCALE_MIN_PERCENT}–${SCALE_MAX_PERCENT}% — using ${clamped}%.`;
    } else {
      note.className = 'ov2-scale-pct-note';
      note.textContent = clamped === 100 ? 'Same as the original size.'
        : clamped < 100 ? `Shrunk to ${clamped}% of the original.`
                        : `Enlarged to ${clamped}% of the original.`;
    }
  }
  renderScalePreview();
  updateSummary();
}

// Custom % is staff-only: a customer picking a percentage is a customer
// guessing, and the shop takes the complaint. Staff mode reveals it.
function syncStaffScale() {
  if (!STAFF) return;
  const tog = $('ov2-sc-custom');
  if (tog) tog.style.display = '';
}

// Scaling is 1-up only: N-up already IS a fit, so the card hides rather than
// offering a choice that would be ignored (print_planner drops it and alerts).
function syncScaleCardVisibility() {
  const card = $('ov2-scale-card');
  if (card) card.style.display = state.nup === 1 ? '' : 'none';
}

function firstPageSize() {
  const page = runtime.firstPageSize;
  return page && page.w > 0 && page.h > 0 ? page : null;
}

async function renderScalePreview() {
  const sheet = $('ov2-scaleSheet');
  const page = $('ov2-scalePage');
  const cap = $('ov2-scaleCaption');
  if (!sheet || !page || !cap) return;

  const src = firstPageSize();
  const seq = ++scaleRectSeq;

  // Fit is the default and never crops; describe it without a round trip.
  // Custom always asks, because only the printer's own scale_rect() knows where
  // an arbitrary percentage lands — a preview drawn by different code can lie.
  if (state.scale === 'fit' || !src) {
    page.className = 'ov2-scale-page';
    Object.assign(page.style, { left: '6%', top: '6%', width: '88%', height: '88%' });
    cap.innerHTML = '<b>Fit to page:</b> your page is resized to fill the sheet.';
    return;
  }

  try {
    const p = new URLSearchParams({
      page_w: src.w.toFixed(2), page_h: src.h.toFixed(2),
      sheet: state.paperSize, mode: state.scale,
    });
    if (state.scale === 'custom') p.set('percent', String(state.scalePercent));
    const r = await fetch(`${API}/order/scale-rect?${p}`);
    if (!r.ok) throw new Error('scale-rect ' + r.status);
    const { scale } = await r.json();
    if (seq !== scaleRectSeq) return;            // a newer request won

    if (!scale) {
      // Already exactly the sheet size — nothing to show but the full page.
      page.className = 'ov2-scale-page';
      Object.assign(page.style, { left: '0%', top: '0%', width: '100%', height: '100%' });
      cap.innerHTML = '<b>Actual size:</b> your page is already this size — nothing changes.';
      return;
    }
    const pct = (v, total) => (v / total * 100).toFixed(2) + '%';
    page.className = 'ov2-scale-page' + (scale.crops ? ' crops' : '');
    Object.assign(page.style, {
      left: pct(scale.x0, scale.sheet_w), top: pct(scale.y0, scale.sheet_h),
      width: pct(scale.width, scale.sheet_w), height: pct(scale.height, scale.sheet_h),
    });
    cap.innerHTML = scale.crops
      ? '<b>Actual size:</b> your page is bigger than the paper — '
        + '<span class="ov2-scale-warn">the edges will be cut off.</span>'
      : '<b>Actual size:</b> printed at its true size, centred on the sheet.';
  } catch (e) {
    if (seq !== scaleRectSeq) return;
    // Never show an invented placement. Say the preview is unavailable and let
    // the words carry the meaning instead.
    page.className = 'ov2-scale-page';
    Object.assign(page.style, { left: '6%', top: '6%', width: '88%', height: '88%' });
    cap.innerHTML = '<b>Actual size:</b> printed at its true size, not resized. '
                  + '<span style="color:#9aa1ab">(preview unavailable)</span>';
  }
}

function setDirection(mode) {
  state.direction = mode;
  document.querySelectorAll('[data-direction]').forEach((el) => {
    el.classList.toggle('active', el.dataset.direction === mode);
  });
  renderNupSheet();
  updateSummary();
}

function changeCopies(d) {
  state.copies = Math.max(1, Math.min(99, state.copies + d));
  const el = $('ov2-copiesVal');
  el.textContent = state.copies;
  el.classList.remove('bump');
  void el.offsetWidth;
  el.classList.add('bump');
  updateSummary();
  requestQuote();
}

function setSides(mode) {
  state.sides = mode;
  $('ov2-sd-single').classList.toggle('active', mode === 'single');
  $('ov2-sd-duplex').classList.toggle('active', mode === 'duplex');
  const card = $('ov2-flipCard');
  const cap = $('ov2-duplexCaption');
  if (mode === 'duplex') {
    cap.innerHTML = '<b style="color:#e8500a">Duplex:</b> page 1 on the <b>front</b>, page 2 on the <b style="color:#e8500a">back</b> of the <b>same sheet</b> → half the paper.';
    card.classList.add('flipped');
    setTimeout(() => card.classList.remove('flipped'), 1400);
    setTimeout(() => card.classList.add('flipped'), 2600);
  } else {
    cap.innerHTML = '<b>Single-sided:</b> each page on its own sheet. Page 1 here, page 2 on the next sheet.';
    card.classList.remove('flipped');
  }
  updateSummary();
  requestQuote();
}

function setOrientation(mode) {
  state.orientation = mode;
  document.querySelectorAll('[data-orientation]').forEach((el) => {
    el.classList.toggle('active', el.dataset.orientation === mode);
  });
  renderNupSheet();   // reshape the preview to portrait/landscape
  updateSummary();
}

function setBinding(mode) {
  state.binding = mode;
  document.querySelectorAll('[data-binding]').forEach((el) => {
    el.classList.toggle('active', el.dataset.binding === mode);
  });
  updateSummary();
  requestQuote();
}

// ── Live summary ──────────────────────────────────────────────────────────────
function colourCountWithinIncluded() {
  if (state.colourMode === 'col') return countSelectedPages(state.included);
  if (state.colourMode !== 'mixed') return 0;
  let c = 0;
  for (let i = 1; i <= state.totalPages; i++) {
    if (state.included[i] && state.colourPages[i]) c++;
  }
  return c;
}

function flashTag(el) {
  el.classList.remove('flash');
  void el.offsetWidth;
  el.classList.add('flash');
}

function updateSummary() {
  const inc = countSelectedPages(state.included);
  $('ov2-incCount').textContent = inc;

  const pTag = $('ov2-s-pages');
  pTag.textContent = inc === state.totalPages ? state.totalPages + ' pages' : inc + ' of ' + state.totalPages + ' pages';
  flashTag(pTag);

  const colTag = $('ov2-s-colour');
  if (state.colourMode === 'bw') colTag.textContent = 'All B&W';
  else if (state.colourMode === 'col') colTag.textContent = 'All Colour';
  else {
    const cc = colourCountWithinIncluded();
    colTag.textContent = cc + ' colour · ' + (inc - cc) + ' B&W';
  }
  flashTag(colTag);

  const nTag = $('ov2-s-nup');
  if (state.nup === 1) nTag.style.display = 'none';
  else { nTag.style.display = ''; nTag.textContent = state.nup + '-up'; flashTag(nTag); }

  const cTag = $('ov2-s-copies');
  cTag.textContent = state.copies === 1 ? '1 copy' : state.copies + ' copies';
  flashTag(cTag);

  $('ov2-s-paper').textContent = state.paperSize;
  $('ov2-s-sides').textContent = state.sides === 'single' ? 'Single-sided' : 'Duplex';

  const scaleTag = $('ov2-s-scale');
  if (scaleTag) {
    if (state.scale === 'actual' && state.nup === 1) {
      scaleTag.style.display = ''; scaleTag.textContent = 'Actual size'; flashTag(scaleTag);
    } else { scaleTag.style.display = 'none'; }
  }

  const orientTag = $('ov2-s-orient');
  if (orientTag) {
    if (state.orientation === 'auto') { orientTag.style.display = 'none'; }
    else { orientTag.style.display = ''; orientTag.textContent = state.orientation === 'landscape' ? 'Landscape' : 'Portrait'; }
  }

  const bMap = { none: 'No binding', staple: 'Stapled', spiral: 'Spiral bound', wiro: 'Wiro bound', soft: 'Soft bound', perfect: 'Perfect bound', project: 'Project bound', record: 'Record bound', thesis: 'Thesis bound' };
  $('ov2-s-bind').textContent = bMap[state.binding] || state.binding;

  const sheets = computeSheets({ pages: inc, nup: state.nup, duplex: state.sides === 'duplex', copies: state.copies });
  const shTag = $('ov2-s-sheets');
  shTag.textContent = sheets + (sheets === 1 ? ' sheet' : ' sheets');
  flashTag(shTag);
}

// ── Live price (debounced /order/quote) ───────────────────────────────────────
let quoteTimer = null;
let quoteSeq = 0;

function requestQuote() {
  if (quoteTimer) clearTimeout(quoteTimer);
  quoteTimer = setTimeout(fetchQuote, 400);
}

async function fetchQuote() {
  const includedCount = countSelectedPages(state.included);
  if (includedCount === 0) {
    setPriceText(null);
    return;
  }
  const colourCount = colourCountWithinIncluded();
  const print_items = buildPrintItems({
    includedCount,
    colourCount,
    nup: state.nup,
    duplex: state.sides === 'duplex',
    copies: state.copies,
    paperSize: state.paperSize,
  });
  const seq = ++quoteSeq;
  try {
    const res = await fetch(API + '/order/quote', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ print_items, finishing: state.binding, paper_size: state.paperSize }),
    });
    if (!res.ok) throw new Error('quote http ' + res.status);
    const data = await res.json();
    if (seq !== quoteSeq) return; // a newer request superseded this one
    runtime.lastTotal = data.total;
    state.amountEstimated = data.total;
    setPriceText(data.total);
  } catch (err) {
    // Keep last known price; never block the flow on a network error.
    if (runtime.lastTotal != null) setPriceText(runtime.lastTotal);
  }
}

function priceLabel(total) {
  const prefix = state.priceExact ? '₹' : '≈₹';
  return prefix + Math.round(total);
}

function setPriceText(total) {
  const sPrice = $('ov2-s-price');
  const nextPrice = $('ov2-nextPrice');
  const submitPrice = $('ov2-submitPrice');
  if (total == null) {
    sPrice.style.display = 'none';
    nextPrice.textContent = '→';
    submitPrice.textContent = '→';
    return;
  }
  const label = priceLabel(total);
  sPrice.style.display = '';
  sPrice.textContent = label;
  flashTag(sPrice);
  nextPrice.textContent = label + ' ·';
  submitPrice.textContent = label + ' ·';
}

// ── Step navigation ───────────────────────────────────────────────────────────
function goStep2() {
  buildRecap();
  hide($('ov2-step1'));
  show($('ov2-step2'));
  $('ov2-step1pill').classList.remove('active');
  $('ov2-step2pill').classList.add('active');
  window.scrollTo({ top: 0, behavior: 'smooth' });
}

function goStep1() {
  show($('ov2-step1'));
  hide($('ov2-step2'));
  $('ov2-step2pill').classList.remove('active');
  $('ov2-step1pill').classList.add('active');
  window.scrollTo({ top: 0, behavior: 'smooth' });
}

function buildRecap() {
  const spec = buildPrintSpec(state);
  const pills = [];
  pills.push(spec.pages_included.length + ' of ' + spec.total_pages + ' pages');
  if (spec.colour_mode === 'bw') pills.push('All B&W');
  else if (spec.colour_mode === 'col') pills.push('All Colour');
  else pills.push(spec.colour_pages.length + ' colour pages · rest B&W');
  if (spec.nup !== 1) pills.push(spec.nup + '-up');
  pills.push(spec.copies === 1 ? '1 copy' : spec.copies + ' copies');
  pills.push(spec.paper_size);
  pills.push(spec.sides === 'duplex' ? 'Duplex' : 'Single-sided');
  if (spec.orientation && spec.orientation !== 'auto') pills.push(spec.orientation === 'landscape' ? 'Landscape' : 'Portrait');
  const bMap = { none: 'No binding', staple: 'Staple', spiral: 'Spiral', wiro: 'Wiro', soft: 'Soft', perfect: 'Perfect', project: 'Project', record: 'Record', thesis: 'Thesis' };
  pills.push(bMap[spec.binding] || spec.binding);
  pills.push(spec.sheet_count + (spec.sheet_count === 1 ? ' sheet' : ' sheets'));
  if (state.amountEstimated) pills.push(priceLabel(state.amountEstimated));

  const wrap = $('ov2-recapPills');
  wrap.innerHTML = '';
  for (const p of pills) {
    const el = document.createElement('span');
    el.className = 'ov2-recap-pill';
    el.textContent = p;
    wrap.appendChild(el);
  }
}

// ── Step 2 validation ─────────────────────────────────────────────────────────
function plausiblePhone(raw) {
  const digits = (raw || '').replace(/\D/g, '');
  return digits.length >= 10 && digits.length <= 13;
}

function isHidden(id) {
  const el = $(id);
  return !!el && el.classList.contains('ov2-hidden');
}

function validateStep2() {
  // A logged-in account hides the name and/or WhatsApp field (the backend
  // supplies that identity from the Bearer token) — a hidden field is satisfied.
  // In staff/walk-in mode the customer name is genuinely optional (the field is
  // shown but only relabelled, not hidden), so don't let an empty name block submit.
  const nameOk = STAFF || isHidden('ov2-field-name') || $('ov2-name').value.trim().length > 0;
  const phoneOk = isHidden('ov2-field-wa') || plausiblePhone($('ov2-whatsapp').value);
  const addrOk = runtime.delivery === 0 || $('ov2-address').value.trim().length > 0;
  const ok = nameOk && phoneOk && addrOk;
  $('ov2-submit').disabled = !ok;
  return ok;
}

function setDelivery(d) {
  runtime.delivery = d;
  $('ov2-dl-pickup').classList.toggle('active', d === 0);
  $('ov2-dl-deliver').classList.toggle('active', d === 1);
  $('ov2-addrField').classList.toggle('show', d === 1);
  validateStep2();
}

function setStore(s) {
  runtime.pickup_store = s;
  $('ov2-st-thriprayar').classList.toggle('active', s === 'thriprayar');
  $('ov2-st-nattika').classList.toggle('active', s === 'nattika');
}

// Staff mode: the fulfilling store is whatever the operator picked in the
// (visible) location picker, mapped to a real store id — never the box's own
// machine id. A roaming box can serve either store, so the store must be an
// explicit choice, not derived from the machine.
const PICKUP_TO_STORE = { thriprayar: 'OSP', nattika: 'PRINTK' };
function staffStoreId() { return PICKUP_TO_STORE[runtime.pickup_store] || 'OSP'; }

// Staff payment choice at creation: 'cash' | 'upi' | 'hold'. cash/upi mark the
// job Paid so it prints immediately; hold leaves it Pending for the console.
function staffPaymentMode() {
  const el = $('ov2-payment');
  const v = el ? (el.value || '').toLowerCase() : 'hold';
  return (v === 'cash' || v === 'upi') ? v : 'hold';
}

// ── Submit flow ───────────────────────────────────────────────────────────────
function showError(html) {
  const box = $('ov2-error');
  if (STAFF) {
    box.innerHTML = html;
  } else {
    const wa = 'https://wa.me/' + WA_NUMBER;
    box.innerHTML = html + ' <br>Please <a href="' + wa + '" target="_blank" rel="noopener">message us on WhatsApp</a> and we\'ll sort it out.';
  }
  box.classList.add('show');
}

function clearError() {
  $('ov2-error').classList.remove('show');
}

function setBusy(on, text) {
  const b = $('ov2-busy');
  if (on) {
    $('ov2-busyText').textContent = text || 'Working…';
    b.classList.add('show');
    $('ov2-submit').disabled = true;
    $('ov2-back').disabled = true;
  } else {
    b.classList.remove('show');
    $('ov2-back').disabled = false;
    validateStep2();
  }
}

function zeroBasedIncludedIndices() {
  const idx = [];
  for (let i = 1; i <= state.totalPages; i++) {
    if (state.included[i]) idx.push(i - 1);
  }
  return idx;
}

async function buildUploadBytes() {
  const indices = zeroBasedIncludedIndices();
  const allIncluded = indices.length === state.totalPages;
  // Only PDFs can be page-sliced; everything else uploads as-is.
  if (state.fileExt !== 'pdf' || allIncluded) {
    return new Uint8Array(runtime.originalBytes);
  }
  const srcDoc = await PDFLib.PDFDocument.load(runtime.originalBytes);
  const newDoc = await PDFLib.PDFDocument.create();
  const copied = await newDoc.copyPages(srcDoc, indices);
  copied.forEach((p) => newDoc.addPage(p));
  return await newDoc.save();
}

// ── Multi-file batch (staff mode) ─────────────────────────────────────────────
// Staff can queue several files, each with its OWN print options, and create one
// job per file in a single submit. The existing single-file editor edits the
// "current" file; addAnotherFile() snapshots it into `batch` (carrying the print
// options forward as defaults) and loads the next file. Submit loops batch + the
// current file, uploading + creating each independently.
let batch = [];          // [{ bytes, contentType, spec }]
let pendingFiles = [];   // Files still to load from a multi-select
let carryOpts = null;    // print options carried to the next file

function snapshotSpec() {
  return {
    fileName: state.fileName, fileExt: state.fileExt, totalPages: state.totalPages,
    included: Object.assign({}, state.included), colourPages: Object.assign({}, state.colourPages),
    colourMode: state.colourMode, nup: state.nup, copies: state.copies, paperSize: state.paperSize,
    sides: state.sides, orientation: state.orientation, direction: state.direction,
    binding: state.binding, amountEstimated: state.amountEstimated, priceExact: state.priceExact,
    // Carried like every other print option: buildPrintSpec() reads a snapshot
    // for batched files, so leaving this out would drop the customer's Actual
    // size choice on every file but the last one — silently, since the summary
    // tag reads live state.
    scale: state.scale,
  };
}

function currentRecord() {
  return { bytes: runtime.originalBytes, contentType: runtime.contentType, spec: snapshotSpec() };
}

async function buildUploadBytesFrom(spec, bytes) {
  const indices = [];
  for (let i = 1; i <= spec.totalPages; i++) if (spec.included[i]) indices.push(i - 1);
  const allIncluded = indices.length === spec.totalPages;
  if (spec.fileExt !== 'pdf' || allIncluded) return new Uint8Array(bytes);
  const srcDoc = await PDFLib.PDFDocument.load(bytes);
  const newDoc = await PDFLib.PDFDocument.create();
  const copied = await newDoc.copyPages(srcDoc, indices);
  copied.forEach((p) => newDoc.addPage(p));
  return await newDoc.save();
}

// ── Local-first printing ──────────────────────────────────────────────────────
// A walk-in printed at the counter used to travel: browser -> Supabase Storage
// -> a jobs row with a file_url -> the store PC's puller downloads it back ->
// prints. A round trip through the internet for a file that never leaves the
// room, and counter printing that broke whenever the line did.
//
// When this browser IS the fulfilling store's PC (⚙ PC Setup has been done and
// its store matches the job's), hand the bytes straight to the local print
// server. The job record still syncs to the cloud, so the console sees it; no
// file_url is ever set, so the puller cannot print it a second time.
//
// Returns a created-job object, or null when local printing is not available —
// in which case the caller falls back to the cloud path unchanged.
async function tryLocalPrint(rec, cust, bytes) {
  const pcUrl = (localStorage.getItem('storePcUrl') || '').trim();
  const token = localStorage.getItem('storeToken') || '';
  const machineStore = (localStorage.getItem('storeId') || '').toUpperCase();
  if (!pcUrl || !token) return null;                      // this box is not a store PC
  if (!machineStore || machineStore !== staffStoreId()) return null;  // fulfilling elsewhere

  const spec = buildPrintSpec(rec.spec);
  const mode = staffPaymentMode();
  try {
    const res = await fetch(pcUrl + '/local-print', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-Store-Token': token },
      body: JSON.stringify({
        filename: rec.spec.fileName,
        file_data: bytesToBase64(bytes),
        print_spec: spec,
        // The record gets bw/col; the actual print is driven by print_spec, which
        // carries the per-page colour list for a mixed job.
        colour: spec.colour_mode === 'bw' ? 'bw' : 'col',
        copies: spec.copies || 1,
        paper_size: spec.paper_size || 'A4',
        orientation: spec.orientation || null,
        sides: spec.sides === 'duplex' ? 'ds' : 'ss',
        pages: (spec.pages_included && spec.pages_included.length) || spec.total_pages || 1,
        // Bill what the customer was quoted on screen. Without this the store PC
        // would re-quote from colour alone and overcharge a mixed job.
        amount_quoted: spec.amount_estimated || 0,
        customer_name: cust.name,
        phone: cust.phone,
        source: 'Walk-in',
        payment_mode: mode === 'upi' ? 'UPI' : 'Cash',
        amount_collected: 0,
        override_reason: mode === 'hold' ? 'Counter job — payment on collection' : '',
        staff_id: sessionStorage.getItem('staff_id') || 'counter',
        operator_note: buildOperatorNote(rec.spec),
      }),
    });
    if (!res.ok) throw new Error('local-print http ' + res.status);
    const data = await res.json();
    if (!data.job_id) throw new Error('no job id');
    return { job_id: data.job_id, total: data.amount_quoted || 0,
             paid: mode !== 'hold', mode: mode, local: true, printed: !!data.printed };
  } catch (e) {
    // Never block a counter job on the local path — fall back to the cloud.
    console.warn('local print unavailable, falling back to upload:', e);
    return null;
  }
}

function bytesToBase64(bytes) {
  const arr = bytes instanceof Uint8Array ? bytes : new Uint8Array(bytes);
  let binary = '';
  const chunk = 0x8000;                     // chunked: apply() blows the stack on big files
  for (let i = 0; i < arr.length; i += chunk) {
    binary += String.fromCharCode.apply(null, arr.subarray(i, i + chunk));
  }
  return btoa(binary);
}

// Upload one file to storage and create one staff job. Throws on failure
// (message '403' for an auth failure so the caller can show the login hint).
async function uploadAndCreateStaff(rec, cust) {
  const bytes = await buildUploadBytesFrom(rec.spec, rec.bytes);

  // Printing here? Then keep the file here.
  const local = await tryLocalPrint(rec, cust, bytes);
  if (local) return local;

  const signRes = await fetch(API + '/order/upload-sign', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ filename: rec.spec.fileName }),
  });
  if (!signRes.ok) throw new Error('sign http ' + signRes.status);
  const signed = await signRes.json();
  if (!signed.signed_url || !signed.storage_path) throw new Error('missing signed url');
  const put = await fetch(signed.signed_url, {
    method: 'PUT', headers: { 'Content-Type': rec.contentType }, body: bytes,
  });
  if (!put.ok) throw new Error('put http ' + put.status);
  const res = await fetch(API + '/order/staff-create', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-Staff-Pin': sessionStorage.getItem('staff_pin') || '' },
    body: JSON.stringify({
      file_url: SUPABASE_PUBLIC + signed.storage_path,
      file_name: rec.spec.fileName,
      print_spec: buildPrintSpec(rec.spec),
      store_id: staffStoreId(),
      payment_mode: staffPaymentMode(),
      customer_name: cust.name,
      phone: cust.phone,
      operator_note: buildOperatorNote(rec.spec),
    }),
  });
  if (res.status === 403) throw new Error('403');
  if (!res.ok) throw new Error('staff-create http ' + res.status);
  const data = await res.json();
  if (!data.job_id) throw new Error('no job id');
  return { job_id: data.job_id, total: data.total || 0, paid: !!data.paid, mode: data.payment_mode || 'hold' };
}

// Apply carried-forward print options to the freshly-loaded file.
function applyCarry() {
  if (!carryOpts) return;
  state.copies = carryOpts.copies;
  $('ov2-copiesVal').textContent = state.copies;
  state.paperSize = carryOpts.paperSize;
  if ($('ov2-paper')) $('ov2-paper').value = state.paperSize;
  setColourMode(carryOpts.colourMode);
  setSides(carryOpts.sides);
  setOrientation(carryOpts.orientation);
  setDirection(carryOpts.direction);
  setBinding(carryOpts.binding);
  setNup(carryOpts.nup);   // also re-renders the sheet + quote
}

// Clear the editor so the "current" slot is empty until the next file loads —
// prevents the just-added file also counting as the current one at submit.
function clearEditor() {
  resetForNewFile();
  runtime.originalBytes = null;
  hide($('ov2-previewBlock'));
  hide($('ov2-controlsBlock'));
  $('ov2-filechip').classList.remove('show');
  updateSubmitLabel();
}

function addAnotherFile() {
  if (!runtime.originalBytes) return;
  batch.push(currentRecord());
  carryOpts = {
    colourMode: state.colourMode, nup: state.nup, copies: state.copies, paperSize: state.paperSize,
    sides: state.sides, orientation: state.orientation, direction: state.direction, binding: state.binding,
  };
  renderBatchStrip();
  clearEditor();
  if (pendingFiles.length) handleFile(pendingFiles.shift());
  else $('ov2-file').click();   // let the operator pick the next file
}

function removeBatchFile(idx) {
  batch.splice(idx, 1);
  renderBatchStrip();
}

function renderBatchStrip() {
  const strip = $('ov2-batch');
  if (!strip) return;
  strip.innerHTML = '';
  if (!batch.length) { strip.classList.remove('show'); updateSubmitLabel(); return; }
  strip.classList.add('show');
  const title = document.createElement('div');
  title.className = 'ov2-batch-title';
  title.textContent = 'In this batch (' + batch.length + ')';
  strip.appendChild(title);
  batch.forEach((r, i) => {
    const chip = document.createElement('div');
    chip.className = 'ov2-batch-chip';
    const nm = document.createElement('span');
    nm.textContent = r.spec.fileName;
    chip.appendChild(nm);
    const x = document.createElement('button');
    x.className = 'ov2-batch-x'; x.textContent = '✕'; x.title = 'Remove';
    x.addEventListener('click', () => removeBatchFile(i));
    chip.appendChild(x);
    strip.appendChild(chip);
  });
  updateSubmitLabel();
}

function updateSubmitLabel() {
  if (!STAFF) return;
  const span = $('ov2-submit') && $('ov2-submit').querySelector('span');
  if (!span) return;
  const n = batch.length + (runtime.originalBytes ? 1 : 0);
  span.textContent = n > 1 ? ('Create ' + n + ' jobs') : 'Add to queue';
}

async function submitStaffBatch() {
  const cust = {
    name: $('ov2-name').value.trim(),
    phone: ($('ov2-whatsapp') && !$('ov2-field-wa').classList.contains('ov2-hidden'))
      ? $('ov2-whatsapp').value.trim() : '',
  };
  const files = batch.concat(runtime.originalBytes ? [currentRecord()] : []);
  if (!files.length) return;
  const created = [];
  try {
    for (let i = 0; i < files.length; i++) {
      setBusy(true, 'Adding job ' + (i + 1) + ' of ' + files.length + '…');
      created.push(await uploadAndCreateStaff(files[i], cust));
    }
  } catch (err) {
    setBusy(false);
    if (String(err.message) === '403') {
      showError('Open this from the jobs page (log in with your PIN first).');
      return;
    }
    showError('Added ' + created.length + ' of ' + files.length
      + ' jobs — the rest failed. The created ones are already in the queue; reload to retry the remaining.');
    return;
  }
  onStaffBatchSuccess(created);
}

function onStaffBatchSuccess(created) {
  setBusy(false);
  hide($('ov2-step1'));
  hide($('ov2-step2'));
  const total = created.reduce((s, r) => s + (r.total || 0), 0);
  const successDiv = $('ov2-success');
  successDiv.innerHTML = '';
  const check = document.createElement('div'); check.className = 'ov2-check'; check.textContent = '✅';
  const h = document.createElement('h2');
  h.textContent = created.length + (created.length > 1 ? ' jobs added' : ' job added');
  successDiv.appendChild(check); successDiv.appendChild(h);
  created.forEach((r) => {
    const d = document.createElement('div'); d.className = 'ov2-job'; d.style.fontSize = '13px';
    d.textContent = 'Job ' + r.job_id;
    successDiv.appendChild(d);
  });
  const sub = document.createElement('div'); sub.className = 'ov2-job';
  sub.style.color = '#888'; sub.style.fontSize = '13px';
  const anyPaid = created.some((r) => r.paid);
  const mode = (created.find((r) => r.paid) || {}).mode || '';
  sub.textContent = anyPaid
    ? '₹' + Math.round(total) + ' total · Paid (' + String(mode).toUpperCase() + ') · printing now'
    : '₹' + Math.round(total) + ' total · mark paid & print from the jobs page';
  successDiv.appendChild(sub);
  const actions = document.createElement('div');
  actions.style.cssText = 'display:flex;gap:10px;justify-content:center;margin-top:18px';
  actions.innerHTML = '<button class="ov2-btn ov2-btn-primary" onclick="location.reload()">+ New batch</button>'
    + '<button class="ov2-btn ov2-btn-ghost" onclick="window.close()">Close</button>';
  successDiv.appendChild(actions);
  successDiv.classList.add('show');
  window.scrollTo({ top: 0, behavior: 'smooth' });
}

async function submitOrder() {
  if (!validateStep2()) return;
  if (STAFF) { submitStaffBatch(); return; }
  clearError();
  setBusy(true, 'Preparing your file…');

  let bytes;
  try {
    bytes = await buildUploadBytes();
  } catch (err) {
    setBusy(false);
    showError('We couldn\'t prepare your PDF.');
    return;
  }

  // 1. signed upload URL
  let signed;
  try {
    setBusy(true, 'Uploading your file…');
    const res = await fetch(API + '/order/upload-sign', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ filename: state.fileName }),
    });
    if (!res.ok) throw new Error('sign http ' + res.status);
    signed = await res.json();
    if (!signed.signed_url || !signed.storage_path) throw new Error('missing signed url');
  } catch (err) {
    setBusy(false);
    showError('We couldn\'t start the upload.');
    return;
  }

  // 2. PUT bytes to the signed URL
  try {
    const put = await fetch(signed.signed_url, {
      method: 'PUT',
      headers: { 'Content-Type': runtime.contentType },
      body: bytes,
    });
    if (!put.ok) throw new Error('put http ' + put.status);
  } catch (err) {
    setBusy(false);
    showError('Your file didn\'t finish uploading.');
    return;
  }

  // 3. create the order
  const fileUrl = SUPABASE_PUBLIC + signed.storage_path;

  if (STAFF) {
    // ── Staff mode: POST to /order/staff-create ──
    try {
      setBusy(true, 'Adding to queue…');
      const res = await fetch(API + '/order/staff-create', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'X-Staff-Pin': sessionStorage.getItem('staff_pin') || '',
        },
        body: JSON.stringify({
          file_url: fileUrl,
          file_name: state.fileName,
          print_spec: buildPrintSpec(state),
          store_id: staffStoreId(),
          payment_mode: staffPaymentMode(),
          customer_name: $('ov2-name').value.trim(),
          phone: $('ov2-whatsapp') && !$('ov2-field-wa').classList.contains('ov2-hidden')
            ? $('ov2-whatsapp').value.trim() : '',
          // Page-inclusion + colour detail (N of M pages, SKIPPED, COLOUR pages).
          // Pure buildOperatorNote — NOT withExtraInstructions, which would inject
          // the customer 'Payment: cash' line from the hidden payment select.
          operator_note: buildOperatorNote(state),
        }),
      });
      if (res.status === 403) {
        setBusy(false);
        showError('Open this from the jobs page (log in with your PIN first).');
        return;
      }
      if (!res.ok) throw new Error('staff-create http ' + res.status);
      const data = await res.json();
      if (!data.job_id) throw new Error('no job id');
      onStaffSuccess(data.job_id, data.total, !!data.paid, data.payment_mode || 'hold');
    } catch (err) {
      setBusy(false);
      showError('Couldn\'t add the job to the queue.');
    }
  } else {
    // ── Customer mode: POST to /order/create ──
    try {
      setBusy(true, 'Placing your order…');
      const customer = {
        name: $('ov2-name').value.trim(),
        whatsapp: $('ov2-whatsapp').value.trim(),
        delivery: runtime.delivery,
        pickup_store: runtime.pickup_store,
      };
      if (runtime.delivery === 1) customer.address = $('ov2-address').value.trim();

      const note = withExtraInstructions(buildOperatorNote(state));
      const createHeaders = { 'Content-Type': 'application/json' };
      const tok = authToken();
      if (tok) createHeaders['Authorization'] = 'Bearer ' + tok;
      const res = await fetch(API + '/order/create', {
        method: 'POST',
        headers: createHeaders,
        body: JSON.stringify({
          customer,
          file_url: fileUrl,
          file_name: state.fileName,
          print_spec: buildPrintSpec(state),
          operator_note: note,
        }),
      });
      if (!res.ok) throw new Error('create http ' + res.status);
      const data = await res.json();
      if (!data.job_id) throw new Error('no job id');
      onSuccess(data.job_id);
    } catch (err) {
      setBusy(false);
      showError('We couldn\'t place your order.');
    }
  }
}

function withExtraInstructions(note) {
  const payment = $('ov2-payment').value;
  const extra = $('ov2-instructions').value.trim();
  const parts = [note, 'Payment: ' + payment];
  if (extra) parts.push('Note: ' + extra);
  if (runtime.delivery === 1) {
    const addr = $('ov2-address').value.trim();
    if (addr) parts.push('DELIVERY to: ' + addr);
  }
  return parts.join(' · ');
}

function onSuccess(jobId) {
  setBusy(false);
  hide($('ov2-step1'));
  hide($('ov2-step2'));
  $('ov2-successJob').textContent = jobId;
  const msg = 'Hi! I just placed order ' + jobId + ' on printosky.com.';
  $('ov2-waLink').href = 'https://wa.me/' + WA_NUMBER + '?text=' + encodeURIComponent(msg);
  $('ov2-success').classList.add('show');
  window.scrollTo({ top: 0, behavior: 'smooth' });
}

function onStaffSuccess(jobId, total, paid, mode) {
  setBusy(false);
  hide($('ov2-step1'));
  hide($('ov2-step2'));
  const successDiv = $('ov2-success');
  const priceStr = total ? '₹' + Math.round(total) : '₹—';
  const statusStr = paid
    ? 'Paid (' + String(mode || '').toUpperCase() + ') · printing now'
    : 'On hold — mark paid & print from the jobs page';
  successDiv.innerHTML =
    '<div class="ov2-check">✅</div>' +
    '<h2>' + (paid ? 'Sent to print' : 'Added to queue') + '</h2>' +
    '<div class="ov2-job">Job <b>' + jobId + '</b> · ' + priceStr + '</div>' +
    '<div class="ov2-job" style="color:#888;font-size:13px">' + statusStr + '</div>' +
    '<div style="display:flex;gap:10px;justify-content:center;margin-top:18px">' +
      '<button class="ov2-btn ov2-btn-primary" onclick="location.reload()">+ New job</button>' +
      '<button class="ov2-btn ov2-btn-ghost" onclick="window.close()">Close</button>' +
    '</div>';
  successDiv.classList.add('show');
  window.scrollTo({ top: 0, behavior: 'smooth' });
}

// ── Wiring ────────────────────────────────────────────────────────────────────
// Staff sign-in gate. order-v2 staff mode carries no login of its own — it reads
// the PIN from sessionStorage. When launched standalone (or on a machine off the
// store LAN, e.g. the office box) that PIN is missing, so we verify it against
// the cloud (/staff/login → Supabase active staff) with no store-PC dependency.
// Resolves immediately when a PIN is already present (e.g. opened from jobs.html).
// ── Staff services — work that never touches a printer ───────────────────────
//
// Lamination, binding, scanning, photocopying, foiling, cutting, DTP. Booked
// through the **Vercel API** (owner, 2026-09-02) rather than the store PC's
// print_server, so staff can work off the shop LAN.
//
// The price is not computed here. /order/service-quote calls the same
// rate_card.calculate_service_quote() the counter and the digest use — a second
// implementation in JavaScript is how a shop starts quoting two different
// prices for the same lamination.

// Mirrors rate_card.SERVICE_KINDS. `manual` marks a kind the card cannot price
// on its own, so the UI asks for an amount instead of pretending to know one.
const SERVICE_KINDS = [
  { id: 'copy',     label: '📄 Photocopy'   },
  { id: 'scan',     label: '🖨️ Scanning'    },
  { id: 'laminate', label: '✨ Lamination'  },
  { id: 'bind',     label: '📚 Binding'     },
  { id: 'foil',     label: '🥇 Foiling'     },
  { id: 'cut',      label: '✂️ Cutting'     },
  { id: 'punch',    label: '🕳️ Punching'    },
  { id: 'photo',    label: '🖼️ Photo print' },
  { id: 'dtp',      label: '⌨️ DTP / typing' },
  { id: 'other',    label: '➕ Other', manual: true },
];

// Which extra fields each kind actually uses. A field that means nothing for
// the chosen service is hidden, not disabled — an input nobody should fill is
// one more thing to get wrong at a busy counter.
const SERVICE_FIELDS = {
  copy:     ['copies', 'colour', 'sides', 'student'],
  scan:     [],
  laminate: ['lam'],
  bind:     ['bind'],
  foil:     [],
  cut:      [],
  punch:    [],
  photo:    [],
  dtp:      [],
  other:    [],
};

// What the quantity box is counting, per kind. "Sheets" is wrong for foiling.
const SERVICE_QTY_LABEL = {
  copy: 'How many sheets', scan: 'How many sheets', laminate: 'How many pieces',
  bind: 'How many sheets', foil: 'How many pieces', cut: 'How many sheets',
  punch: 'How many sheets', photo: 'How many prints', dtp: 'How many pages',
  other: 'How many',
};

const svc = {
  kind: null,
  quote: null,        // last successful quote, or null
  manual: false,      // the rate card could not price it
  busy: false,
  timer: null,
};

function renderServiceKinds() {
  const host = $('ov2-svc-kinds');
  if (!host) return;
  host.innerHTML = SERVICE_KINDS.map((k) =>
    `<button type="button" class="ov2-svc-kind" data-kind="${k.id}">${k.label}</button>`).join('');
  host.querySelectorAll('[data-kind]').forEach((el) =>
    el.addEventListener('click', () => setServiceKind(el.dataset.kind)));
}

function setServiceKind(kind) {
  svc.kind = kind;
  document.querySelectorAll('#ov2-svc-kinds [data-kind]').forEach((el) =>
    el.classList.toggle('active', el.dataset.kind === kind));

  const fields = SERVICE_FIELDS[kind] || [];
  const show1 = (id, on) => { const el = $(id); if (el) el.style.display = on ? '' : 'none'; };
  show1('ov2-svc-copies-card', fields.includes('copies'));
  show1('ov2-svc-lam-card',    fields.includes('lam'));
  show1('ov2-svc-bind-card',   fields.includes('bind'));
  show1('ov2-svc-colour-card', fields.includes('colour'));
  show1('ov2-svc-sides-card',  fields.includes('sides'));

  const label = $('ov2-svc-qty-label');
  if (label) label.textContent = SERVICE_QTY_LABEL[kind] || 'How many';
  quoteService();
}

function serviceMeta() {
  const num = (id, fallback) => {
    const el = $(id);
    const n = el ? Math.floor(Number(el.value)) : NaN;
    return Number.isFinite(n) && n > 0 ? n : fallback;
  };
  const fields = SERVICE_FIELDS[svc.kind] || [];
  const meta = {
    sheets: num('ov2-svc-sheets', 1),
    paper_size: ($('ov2-svc-size') || {}).value || 'A4',
  };
  if (fields.includes('copies')) meta.copies = num('ov2-svc-copies', 1);
  if (fields.includes('lam'))    meta.lam_type = ($('ov2-svc-lam') || {}).value || 'pouch';
  if (fields.includes('bind'))   meta.binding = ($('ov2-svc-bind') || {}).value || 'spiral';
  if (fields.includes('colour')) meta.colour = ($('ov2-svc-colour') || {}).value || 'bw';
  if (fields.includes('sides'))  meta.sides = ($('ov2-svc-sides') || {}).value || 'ss';
  if (fields.includes('student') && ($('ov2-svc-student') || {}).checked) meta.is_student = true;
  if (($('ov2-svc-urgent') || {}).checked) meta.urgent = true;
  return meta;
}

// Debounced, because it runs while somebody is still typing a sheet count.
function quoteServiceSoon() {
  clearTimeout(svc.timer);
  svc.timer = setTimeout(quoteService, 250);
}

async function quoteService() {
  const total = $('ov2-svc-total');
  const lines = $('ov2-svc-lines');
  const box = $('ov2-svc-quote');
  if (!total || !lines || !box) return;

  if (!svc.kind) {
    box.className = 'ov2-svc-quote';
    total.textContent = '—';
    lines.textContent = 'Choose a service to see the price.';
    svc.quote = null; svc.manual = false;
    syncServiceOverride();
    return;
  }

  const meta = serviceMeta();
  const p = new URLSearchParams({ kind: svc.kind });
  Object.entries(meta).forEach(([k, v]) => p.set(k, String(v)));

  try {
    const r = await fetch(`${API}/order/service-quote?${p}`);
    const d = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(d.error || ('service-quote ' + r.status));

    svc.quote = d;
    svc.manual = !!d.needs_manual_price;
    box.className = 'ov2-svc-quote' + (svc.manual ? ' manual' : '');
    total.textContent = svc.manual ? 'Enter the price' : `₹${Math.round(d.total)}`;
    const parts = (d.breakdown || []).map((b) => `<div>${escapeHtml(b)}</div>`).join('');
    const dep = d.deposit_due > 0
      ? `<div><b>₹${Math.round(d.deposit_due)} deposit</b> before the work starts.</div>` : '';
    lines.innerHTML = (parts || '<div>No breakdown.</div>') + dep;
  } catch (e) {
    // Never show an invented price. Say the quote is unavailable and let staff
    // type one — the same rule the store-PC modal follows.
    svc.quote = null; svc.manual = true;
    box.className = 'ov2-svc-quote manual';
    total.textContent = 'Enter the price';
    lines.innerHTML = '<div>Could not reach the rate card — type the amount taken.</div>';
  }
  syncServiceOverride();
}

// The waiver box appears only when there is a deposit to waive.
function syncServiceOverride() {
  const card = $('ov2-svc-override-card');
  if (!card) return;
  const due = (svc.quote && svc.quote.deposit_due) || 0;
  const paid = Number(($('ov2-svc-paid') || {}).value || 0);
  card.style.display = (due > 0 && paid < due) ? '' : 'none';
}

function escapeHtml(s) {
  return String(s == null ? '' : s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

async function submitService() {
  const err = $('ov2-svc-err');
  const done = $('ov2-svc-done');
  const btn = $('ov2-svc-submit');
  if (!err || !btn) return;
  err.textContent = '';

  if (!svc.kind) { err.textContent = 'Pick a service first.'; return; }

  const paid = Number(($('ov2-svc-paid') || {}).value || 0);
  const typed = Number.isFinite(paid) && paid > 0 ? paid : null;
  if (svc.manual && !typed) {
    err.textContent = 'This one has no rate — enter the amount taken.';
    return;
  }

  if (svc.busy) return;
  svc.busy = true; btn.disabled = true; btn.textContent = 'Booking…';

  const storeMap = { thriprayar: 'OSP', nattika: 'PRINTK' };
  const body = {
    kind: svc.kind,
    meta: serviceMeta(),
    store_id: storeMap[runtime.pickup_store] || 'OSP',
    customer_name: ($('ov2-svc-name') || {}).value || '',
    phone: ($('ov2-svc-phone') || {}).value || '',
    notes: ($('ov2-svc-notes') || {}).value || '',
    staff_id: sessionStorage.getItem('staff_id') || '',
    payment_mode: ($('ov2-svc-mode') || {}).value || 'Cash',
    amount_collected: typed || 0,
    override_reason: ($('ov2-svc-override') || {}).value || '',
  };
  // A manually priced service is quoted at what was actually taken; there is no
  // other number, and a Rs.0 quote would read as a free job rather than an
  // unpriced one.
  if (svc.manual && typed) body.amount_quoted = typed;

  // A photocopy is work the Konica actually did, so it is filed as a completed
  // photocopy rather than a service job — that is what keeps it inside the
  // printer counts the copy/scan reconciliation compares against.
  const isCopy = svc.kind === 'copy';
  const url = isCopy ? '/order/staff-photocopy' : '/order/staff-service';
  if (isCopy) {
    const meta = serviceMeta();
    Object.assign(body, {
      pages: meta.sheets, copies: meta.copies || 1,
      colour: meta.colour || 'bw', sides: meta.sides || 'ss',
      paper_size: meta.paper_size, is_student: !!meta.is_student,
    });
  }

  try {
    const r = await fetch(API + url, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-Staff-Pin': sessionStorage.getItem('staff_pin') || '',
      },
      body: JSON.stringify(body),
    });
    const d = await r.json().catch(() => ({}));
    if (r.status === 403) throw new Error('Staff PIN not accepted — sign in again.');
    if (!r.ok) throw new Error(d.error || ('Could not book this (' + r.status + ')'));
    if (d.ok === false) throw new Error(d.error || 'Could not book this.');

    if (done) {
      const amount = d.amount != null ? d.amount : d.amount_quoted;
      done.innerHTML =
        `✅ <b>${escapeHtml(d.job_id)}</b> booked — ₹${Math.round(amount || 0)}` +
        (d.status ? ` · ${escapeHtml(d.status)}` : '') +
        '<br><span style="font-weight:400;color:#6b7280">It is in the console queue now.</span>';
    }
    ['ov2-svc-name', 'ov2-svc-phone', 'ov2-svc-notes', 'ov2-svc-paid',
     'ov2-svc-override'].forEach((id) => { const el = $(id); if (el) el.value = ''; });
  } catch (e) {
    err.textContent = e.message || 'Could not book this.';
  } finally {
    svc.busy = false; btn.disabled = false; btn.textContent = 'Book this service';
  }
}

function setServiceMode(on) {
  const panel = $('ov2-svc-panel');
  const print = $('ov2-print-panel');
  if (panel) panel.style.display = on ? '' : 'none';
  if (print) print.style.display = on ? 'none' : '';
  const t1 = $('ov2-svc-tab-print');
  const t2 = $('ov2-svc-tab-service');
  if (t1) t1.classList.toggle('active', !on);
  if (t2) t2.classList.toggle('active', on);
}

// Customers order prints; a lamination is booked at the counter. Staff mode is
// what reveals any of this.
function syncStaffServices() {
  if (!STAFF) return;
  const sw = $('ov2-svc-switch');
  if (sw) sw.style.display = 'flex';
  renderServiceKinds();

  const t1 = $('ov2-svc-tab-print');
  const t2 = $('ov2-svc-tab-service');
  if (t1) t1.addEventListener('click', () => setServiceMode(false));
  if (t2) t2.addEventListener('click', () => setServiceMode(true));

  ['ov2-svc-sheets', 'ov2-svc-copies'].forEach((id) => {
    const el = $(id); if (el) el.addEventListener('input', quoteServiceSoon);
  });
  ['ov2-svc-size', 'ov2-svc-lam', 'ov2-svc-bind', 'ov2-svc-colour',
   'ov2-svc-sides', 'ov2-svc-student', 'ov2-svc-urgent'].forEach((id) => {
    const el = $(id); if (el) el.addEventListener('change', quoteService);
  });
  const paid = $('ov2-svc-paid');
  if (paid) paid.addEventListener('input', syncServiceOverride);
  const submit = $('ov2-svc-submit');
  if (submit) submit.addEventListener('click', submitService);
}


function ensureStaffAuth() {
  if (sessionStorage.getItem('staff_pin')) return Promise.resolve();
  return new Promise((resolve) => {
    const ov = document.createElement('div');
    ov.setAttribute('role', 'dialog');
    ov.style.cssText =
      'position:fixed;inset:0;z-index:9999;display:flex;align-items:center;' +
      'justify-content:center;background:rgba(17,24,39,.72);backdrop-filter:blur(2px)';
    ov.innerHTML =
      '<div style="background:#fff;border-radius:16px;padding:28px 26px;width:min(360px,92vw);' +
      'box-shadow:0 20px 60px rgba(0,0,0,.35);font-family:inherit;text-align:center">' +
      '<div style="font-size:18px;font-weight:700;color:#111827">Staff sign-in</div>' +
      '<div style="font-size:13px;color:#6b7280;margin:6px 0 16px">Enter your staff PIN to create jobs.</div>' +
      '<input id="ov2-staffpin" type="password" inputmode="numeric" autocomplete="off" maxlength="8" ' +
      'placeholder="PIN" style="width:100%;box-sizing:border-box;padding:12px 14px;font-size:20px;' +
      'letter-spacing:.3em;text-align:center;border:1.5px solid #d1d5db;border-radius:10px;outline:none" />' +
      '<div id="ov2-staffpin-err" style="min-height:18px;color:#dc2626;font-size:12.5px;margin:8px 0"></div>' +
      '<button id="ov2-staffpin-btn" type="button" style="width:100%;padding:12px;font-size:15px;' +
      'font-weight:600;color:#fff;background:#2563eb;border:none;border-radius:10px;cursor:pointer">' +
      'Sign in</button></div>';
    document.body.appendChild(ov);
    const input = ov.querySelector('#ov2-staffpin');
    const btn = ov.querySelector('#ov2-staffpin-btn');
    const err = ov.querySelector('#ov2-staffpin-err');
    input.focus();
    async function submit() {
      const pin = input.value.trim();
      if (!/^\d{4,8}$/.test(pin)) { err.textContent = 'Enter your 4–8 digit PIN'; return; }
      btn.disabled = true; err.textContent = '';
      try {
        const r = await fetch(API + '/staff/login', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ pin }),
        });
        const d = await r.json().catch(() => ({}));
        if (r.ok && d.ok) {
          sessionStorage.setItem('staff_pin', pin);
          sessionStorage.setItem('staff_id', d.staff_id || '');
          sessionStorage.setItem('staff_name', d.name || '');
          ov.remove();
          resolve();
        } else {
          err.textContent = d.error || 'Incorrect PIN';
          btn.disabled = false; input.select();
        }
      } catch (e) {
        err.textContent = 'Network error — try again';
        btn.disabled = false;
      }
    }
    btn.addEventListener('click', submit);
    input.addEventListener('keydown', (e) => { if (e.key === 'Enter') submit(); });
  });
}

function wire() {
  const fileInput = $('ov2-file');
  fileInput.addEventListener('change', (e) => {
    const fl = e.target.files;
    if (!fl || !fl.length) return;
    if (STAFF && fl.length > 1) pendingFiles = Array.from(fl).slice(1);  // batch the rest
    handleFile(fl[0]);
  });
  $('ov2-change').addEventListener('click', (e) => { e.preventDefault(); fileInput.click(); });
  const addBtn = $('ov2-addfile');
  if (addBtn) addBtn.addEventListener('click', (e) => { e.preventDefault(); addAnotherFile(); });

  // Drag & drop onto the label
  const drop = $('ov2-drop');
  ['dragenter', 'dragover'].forEach((evt) =>
    drop.addEventListener(evt, (e) => { e.preventDefault(); drop.classList.add('dragover'); }));
  ['dragleave', 'drop'].forEach((evt) =>
    drop.addEventListener(evt, (e) => { e.preventDefault(); drop.classList.remove('dragover'); }));
  drop.addEventListener('drop', (e) => {
    const fl = e.dataTransfer && e.dataTransfer.files;
    if (!fl || !fl.length) return;
    if (STAFF && fl.length > 1) pendingFiles = Array.from(fl).slice(1);
    handleFile(fl[0]);
  });

  $('ov2-selAll').addEventListener('click', () => selectAll(true));
  $('ov2-skipAll').addEventListener('click', () => selectAll(false));

  document.querySelectorAll('[data-colour]').forEach((el) =>
    el.addEventListener('click', () => setColourMode(el.dataset.colour)));
  document.querySelectorAll('[data-nup]').forEach((el) =>
    el.addEventListener('click', () => setNup(parseInt(el.dataset.nup, 10))));
  document.querySelectorAll('[data-direction]').forEach((el) =>
    el.addEventListener('click', () => setDirection(el.dataset.direction)));
  document.querySelectorAll('[data-scale]').forEach((el) =>
    el.addEventListener('click', () => setScale(el.dataset.scale)));
  // order-ui.js is an ES module, so its functions are NOT global — an inline
  // oninput="" in the HTML would silently never fire. Everything here binds.
  const pctInput = $('ov2-scale-percent');
  if (pctInput) pctInput.addEventListener('input', () => setScalePercent(pctInput.value));
  $('ov2-copiesMinus').addEventListener('click', () => changeCopies(-1));
  $('ov2-copiesPlus').addEventListener('click', () => changeCopies(1));
  $('ov2-paper').addEventListener('change', (e) => {
    state.paperSize = e.target.value; renderScalePreview(); updateSummary(); requestQuote();
  });
  document.querySelectorAll('[data-sides]').forEach((el) =>
    el.addEventListener('click', () => setSides(el.dataset.sides)));
  document.querySelectorAll('[data-orientation]').forEach((el) =>
    el.addEventListener('click', () => setOrientation(el.dataset.orientation)));
  document.querySelectorAll('[data-binding]').forEach((el) =>
    el.addEventListener('click', () => setBinding(el.dataset.binding)));

  $('ov2-toStep2').addEventListener('click', goStep2);
  $('ov2-back').addEventListener('click', goStep1);

  document.querySelectorAll('[data-delivery]').forEach((el) =>
    el.addEventListener('click', () => setDelivery(parseInt(el.dataset.delivery, 10))));
  document.querySelectorAll('[data-store]').forEach((el) =>
    el.addEventListener('click', () => setStore(el.dataset.store)));
  $('ov2-name').addEventListener('input', validateStep2);
  $('ov2-whatsapp').addEventListener('input', validateStep2);
  $('ov2-address').addEventListener('input', validateStep2);
  $('ov2-submit').addEventListener('click', submitOrder);

  // ── Staff mode: hide customer fields, lock store, relabel UI ──
  if (STAFF) {
    // Gate the page behind a staff PIN when none is inherited from a prior login.
    // Verified in the cloud, so it works off the store LAN (e.g. the office box).
    ensureStaffAuth();
    // Custom % — the one scaling mode customers never see.
    syncStaffScale();
    // Lamination, binding, scanning, photocopy — work with no file at all.
    syncStaffServices();
    // Fulfilling store: staff choose it explicitly via the location picker,
    // which stays VISIBLE in staff mode (a roaming box can serve either store
    // and must never tag jobs with its own machine id). Default the picker to
    // this box's store when it IS a real store PC (OSP/PRINTK); otherwise leave
    // Thriprayar as the default and let the operator switch per job.
    const sid = (localStorage.getItem('storeId') || '').toUpperCase();
    const storeMap = { OSP: 'thriprayar', PRINTK: 'nattika' };
    setStore(storeMap[sid] || 'thriprayar');
    const storeField = $('ov2-field-store');
    if (storeField) {
      const lbl = storeField.querySelector('label');
      if (lbl) lbl.textContent = 'Fulfilling store';
    }

    // Hide customer-only fields
    hide($('ov2-field-wa'));       // WhatsApp number
    hide($('ov2-identity'));       // Logged-in account banner
    hide($('ov2-addrField'));      // Delivery address
    // Hide delivery/pickup toggles and payment select — but KEEP the store
    // picker visible so staff pick which store fulfils the job.
    document.querySelectorAll('[data-delivery]').forEach(function(el) {
      if (el.parentElement) hide(el.parentElement.parentElement);  // .ov2-field
    });
    // Payment: staff choose at creation. Cash/UPI record the payment and print
    // now; Hold (default) leaves the job Pending to pay + print later from the
    // console. Repurpose the payment select (kept visible) with these options.
    var payField = $('ov2-payment');
    if (payField) {
      payField.innerHTML =
        '<option value="hold">Hold — take payment later</option>' +
        '<option value="cash">Cash — paid, print now</option>' +
        '<option value="upi">UPI — paid, print now</option>';
      payField.value = 'hold';
      var payLabel = payField.parentElement
        ? payField.parentElement.querySelector('label') : null;
      if (payLabel) payLabel.textContent = 'Payment';
    }

    // Make name optional (remove required asterisk)
    var nameLabel = $('ov2-field-name');
    if (nameLabel) {
      var req = nameLabel.querySelector('.ov2-req');
      if (req) req.remove();
    }
    $('ov2-name').placeholder = 'Customer name (optional)';

    // Relabel UI for staff context
    var topSub = document.querySelector('.ov2-topbar-sub');
    if (topSub) topSub.textContent = 'Staff · New Job';
    $('ov2-step2pill').textContent = '2 · Review & Add';
    $('ov2-submit').querySelector('span').textContent = 'Add to queue';

    // Multi-file batch: allow selecting several files, one job per file.
    $('ov2-file').multiple = true;
    updateSubmitLabel();

    // Skip customer account detection — staff don't use Supabase auth
    validateStep2();
  } else {
    // Detect a logged-in account (async, non-blocking); re-validate Step 2 once
    // the identity fields have been prefilled/hidden.
    initAccount().then(() => validateStep2()).catch(() => {});
  }
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', wire);
} else {
  wire();
}
