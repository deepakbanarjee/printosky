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

const API = 'https://printosky.vercel.app';
const SUPABASE_PUBLIC =
  'https://mlhuwlnwwwxdnqafelko.supabase.co/storage/v1/object/public/incoming-files/';
const WA_NUMBER = '919495706405';

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
  binding: 'none',   // 'none' | 'staple' | 'spiral' | 'wiro'
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
  lastTotal: null,      // last successful quote total (kept on network error)
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
  setNup(1);
  updateSummary();
  requestQuote();
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
  initPages(pdf.numPages);
  $('ov2-pagecount').textContent = pdf.numPages + (pdf.numPages === 1 ? ' page' : ' pages');
}

function loadImage() {
  state.priceExact = true;
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
  el.classList.toggle('mixed-bw', state.colourMode === 'mixed' && !state.colourPages[pg]);
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
  const sheet = $('ov2-nupSheet');
  const [rows, cols] = NUP_LAYOUT[n] || [1, 1];
  sheet.style.gridTemplateColumns = 'repeat(' + cols + ',1fr)';
  sheet.style.gridTemplateRows = 'repeat(' + rows + ',1fr)';
  sheet.innerHTML = '';
  for (let i = 1; i <= n; i++) {
    const c = document.createElement('div');
    c.className = 'ov2-nup-cell';
    c.textContent = n <= 6 ? i : '';
    c.style.animationDelay = (i * 0.03) + 's';
    sheet.appendChild(c);
  }
  $('ov2-nupLabel').textContent = n === 1 ? '1 / sheet' : n + ' / sheet';
  updateSummary();
  requestQuote();
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

  const bMap = { none: 'No binding', staple: 'Stapled', spiral: 'Spiral bound', wiro: 'Wiro bound' };
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
  const bMap = { none: 'No binding', staple: 'Staple', spiral: 'Spiral', wiro: 'Wiro' };
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

function validateStep2() {
  const name = $('ov2-name').value.trim();
  const phoneOk = plausiblePhone($('ov2-whatsapp').value);
  const addrOk = runtime.delivery === 0 || $('ov2-address').value.trim().length > 0;
  const ok = name.length > 0 && phoneOk && addrOk;
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

// ── Submit flow ───────────────────────────────────────────────────────────────
function showError(html) {
  const box = $('ov2-error');
  const wa = 'https://wa.me/' + WA_NUMBER;
  box.innerHTML = html + ' <br>Please <a href="' + wa + '" target="_blank" rel="noopener">message us on WhatsApp</a> and we\'ll sort it out.';
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

async function submitOrder() {
  if (!validateStep2()) return;
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
  try {
    setBusy(true, 'Placing your order…');
    const fileUrl = SUPABASE_PUBLIC + signed.storage_path;
    const customer = {
      name: $('ov2-name').value.trim(),
      whatsapp: $('ov2-whatsapp').value.trim(),
      delivery: runtime.delivery,
    };
    if (runtime.delivery === 1) customer.address = $('ov2-address').value.trim();

    const note = withExtraInstructions(buildOperatorNote(state));
    const res = await fetch(API + '/order/create', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
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

// ── Wiring ────────────────────────────────────────────────────────────────────
function wire() {
  const fileInput = $('ov2-file');
  fileInput.addEventListener('change', (e) => {
    const f = e.target.files && e.target.files[0];
    if (f) handleFile(f);
  });
  $('ov2-change').addEventListener('click', (e) => { e.preventDefault(); fileInput.click(); });

  // Drag & drop onto the label
  const drop = $('ov2-drop');
  ['dragenter', 'dragover'].forEach((evt) =>
    drop.addEventListener(evt, (e) => { e.preventDefault(); drop.classList.add('dragover'); }));
  ['dragleave', 'drop'].forEach((evt) =>
    drop.addEventListener(evt, (e) => { e.preventDefault(); drop.classList.remove('dragover'); }));
  drop.addEventListener('drop', (e) => {
    const f = e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files[0];
    if (f) { fileInput.files = e.dataTransfer.files; handleFile(f); }
  });

  $('ov2-selAll').addEventListener('click', () => selectAll(true));
  $('ov2-skipAll').addEventListener('click', () => selectAll(false));

  document.querySelectorAll('[data-colour]').forEach((el) =>
    el.addEventListener('click', () => setColourMode(el.dataset.colour)));
  document.querySelectorAll('[data-nup]').forEach((el) =>
    el.addEventListener('click', () => setNup(parseInt(el.dataset.nup, 10))));
  $('ov2-copiesMinus').addEventListener('click', () => changeCopies(-1));
  $('ov2-copiesPlus').addEventListener('click', () => changeCopies(1));
  $('ov2-paper').addEventListener('change', (e) => { state.paperSize = e.target.value; updateSummary(); requestQuote(); });
  document.querySelectorAll('[data-sides]').forEach((el) =>
    el.addEventListener('click', () => setSides(el.dataset.sides)));
  document.querySelectorAll('[data-binding]').forEach((el) =>
    el.addEventListener('click', () => setBinding(el.dataset.binding)));

  $('ov2-toStep2').addEventListener('click', goStep2);
  $('ov2-back').addEventListener('click', goStep1);

  document.querySelectorAll('[data-delivery]').forEach((el) =>
    el.addEventListener('click', () => setDelivery(parseInt(el.dataset.delivery, 10))));
  $('ov2-name').addEventListener('input', validateStep2);
  $('ov2-whatsapp').addEventListener('input', validateStep2);
  $('ov2-address').addEventListener('input', validateStep2);
  $('ov2-submit').addEventListener('click', submitOrder);
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', wire);
} else {
  wire();
}
