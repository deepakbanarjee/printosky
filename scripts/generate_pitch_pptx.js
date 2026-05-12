/**
 * Printosky Marketplace Pitch — .pptx Generator
 *
 * Generates website/pitch.pptx using pptxgenjs.
 *
 * SETUP (run once from repo root):
 *   cd scripts && npm install
 *
 * GENERATE:
 *   node scripts/generate_pitch_pptx.js
 *
 * OUTPUT:
 *   website/pitch.pptx
 */

'use strict';

const path = require('path');

let PptxGenJS;
try {
  PptxGenJS = require('pptxgenjs');
} catch (e) {
  console.error('\n❌  pptxgenjs not found.');
  console.error('   Run: cd scripts && npm install\n');
  process.exit(1);
}

const OUT = path.join(__dirname, '..', 'website', 'pitch.pptx');

// ── Brand tokens ────────────────────────────────────────────────
const C = {
  dark:    '0D1117',
  navy:    '1B3F8B',
  light:   'F5F1EB',
  white:   'FFFFFF',
  accent:  'E8500A',
  mid:     '6B7280',
  green:   '25D366',
  dimWhite:'AAAAAA',
  panel:   '1A2333',
  panelBdr:'2A3A4A',
};

const SYNE   = 'Trebuchet MS';
const DMSANS = 'Calibri';

// ── Helper: small label chip ─────────────────────────────────────
function addChip(slide, text, x, y) {
  slide.addText(text.toUpperCase(), {
    x, y, w: 2.8, h: 0.22,
    fontSize: 7, bold: true, charSpacing: 2,
    color: C.accent, fontFace: DMSANS,
  });
}

// ── Helper: large heading ────────────────────────────────────────
function addHeading(slide, text, opts = {}) {
  slide.addText(text, {
    x: 0.5, y: 0.9, w: 9, h: 1.2,
    fontSize: 30, bold: true, fontFace: SYNE,
    color: C.white, charSpacing: -0.5,
    valign: 'top', lineSpacingMultiple: 1.15,
    ...opts,
  });
}

// ── Helper: body paragraph ───────────────────────────────────────
function addBody(slide, text, opts = {}) {
  slide.addText(text, {
    x: 0.5, y: 2.0, w: 5.5, h: 1.5,
    fontSize: 11, fontFace: DMSANS,
    color: C.dimWhite, lineSpacingMultiple: 1.5,
    valign: 'top',
    ...opts,
  });
}

// ── Helper: big stat (number + label below) ──────────────────────
function addStat(slide, num, label, x, y, light = false) {
  slide.addText(num, {
    x, y, w: 3.0, h: 0.9,
    fontSize: 38, bold: true, fontFace: SYNE,
    color: light ? C.dark : C.white, charSpacing: -1,
  });
  slide.addText(label, {
    x, y: y + 0.88, w: 3.0, h: 0.5,
    fontSize: 8.5, fontFace: DMSANS,
    color: light ? C.mid : C.dimWhite,
    lineSpacingMultiple: 1.3,
  });
}

// ── Helper: feature card box ─────────────────────────────────────
function addCard(slide, icon, title, desc, x, y, w = 2.9, dark = false) {
  const bg     = dark ? C.panel : C.white;
  const bdC    = dark ? C.panelBdr : 'E5E7EB';
  const titleC = dark ? C.white : C.dark;
  const descC  = dark ? C.dimWhite : C.mid;

  slide.addShape('rect', {
    x, y, w, h: 1.45,
    fill: { color: bg },
    line: { color: bdC, width: dark ? 0 : 0.4 },
    rectRadius: 0.1,
  });
  if (icon) {
    slide.addText(icon, {
      x: x + 0.15, y: y + 0.13, w: 0.35, h: 0.32, fontSize: 12,
    });
  }
  const titleX = icon ? x + 0.52 : x + 0.18;
  const titleW = icon ? w - 0.65  : w - 0.36;
  slide.addText(title, {
    x: titleX, y: y + 0.16, w: titleW, h: 0.28,
    fontSize: 9, bold: true, fontFace: SYNE, color: titleC,
  });
  slide.addText(desc, {
    x: x + 0.18, y: y + 0.52, w: w - 0.36, h: 0.82,
    fontSize: 8, fontFace: DMSANS, color: descC,
    lineSpacingMultiple: 1.45,
  });
}

// ── Helper: numbered step row ────────────────────────────────────
function addStep(slide, num, title, desc, x, y, light = false) {
  slide.addShape('ellipse', {
    x, y: y - 0.02, w: 0.28, h: 0.28,
    fill: { color: C.accent },
    line: { color: C.accent, width: 0 },
  });
  slide.addText(String(num), {
    x, y: y - 0.02, w: 0.28, h: 0.28,
    fontSize: 7, bold: true, fontFace: SYNE,
    color: C.white, align: 'center', valign: 'middle',
  });
  slide.addText(title, {
    x: x + 0.38, y, w: 4.2, h: 0.25,
    fontSize: 10, bold: true, fontFace: DMSANS,
    color: light ? C.dark : C.white,
  });
  slide.addText(desc, {
    x: x + 0.38, y: y + 0.26, w: 4.2, h: 0.38,
    fontSize: 8, fontFace: DMSANS,
    color: light ? C.mid : C.dimWhite,
    lineSpacingMultiple: 1.4,
  });
}

// ── Helper: pain-point row ───────────────────────────────────────
function addPain(slide, icon, text, x, y) {
  slide.addShape('rect', {
    x, y, w: 5.6, h: 0.7,
    fill: { color: 'EAE6E0' },
    line: { color: 'DDD8D0', width: 0.3 },
    rectRadius: 0.07,
  });
  slide.addText(icon, { x: x + 0.14, y: y + 0.17, w: 0.3, h: 0.35, fontSize: 12 });
  slide.addText(text, {
    x: x + 0.54, y: y + 0.12, w: 4.9, h: 0.5,
    fontSize: 8.5, fontFace: DMSANS, color: C.dark,
    lineSpacingMultiple: 1.4,
  });
}

// ═══════════════════════════════════════════════════════════════
// BUILD PRESENTATION
// ═══════════════════════════════════════════════════════════════

const prs = new PptxGenJS();
prs.layout  = 'LAYOUT_WIDE';   // 13.33" × 7.5"
prs.author  = 'Printosky';
prs.company = 'Oxygen Students Paradise';
prs.subject = 'Printosky Marketplace — Partner Pitch 2026';
prs.title   = 'Printosky Marketplace';

// ── 1 · COVER ───────────────────────────────────────────────────
{
  const s = prs.addSlide();
  s.background = { color: C.dark };

  addChip(s, 'Partner Pitch · 2026', 0.5, 0.42);

  s.addText([
    { text: 'Print', options: { color: C.white } },
    { text: 'osky',  options: { color: C.accent } },
  ], {
    x: 0.5, y: 0.78, w: 7.5, h: 1.5,
    fontSize: 64, bold: true, fontFace: SYNE, charSpacing: -1,
  });

  s.addText('The Print Marketplace\nfor Thrissur.', {
    x: 0.5, y: 2.45, w: 7.5, h: 1.35,
    fontSize: 24, bold: true, fontFace: SYNE,
    color: 'CCCCCC', charSpacing: -0.3, lineSpacingMultiple: 1.3,
  });

  s.addText('WhatsApp → Quote → Pay → Print → Pickup\nOne brand. Multiple stores. Zero friction.', {
    x: 0.5, y: 4.0, w: 7, h: 0.9,
    fontSize: 11, fontFace: DMSANS, color: C.dimWhite,
    lineSpacingMultiple: 1.65,
  });

  s.addShape('ellipse', {
    x: 10.6, y: 4.6, w: 3.8, h: 3.8,
    fill: { color: C.accent, transparency: 94 },
    line: { color: C.accent, width: 0.5, transparency: 88 },
  });
}

// ── 2 · PROBLEM ─────────────────────────────────────────────────
{
  const s = prs.addSlide();
  s.background = { color: C.light };

  addChip(s, 'The Problem', 0.5, 0.3);
  addHeading(s, "Printing shouldn't require a treasure hunt.", {
    color: C.dark, y: 0.6, w: 8.5, fontSize: 26,
  });

  addPain(s, '🗺️', 'Students waste 30 min hunting for a free shop — walk in, find a queue, walk out. Repeat.',           0.45, 1.52);
  addPain(s, '📵', 'No way to know if a shop is free before making the trip. Every visit is a gamble.',                  0.45, 2.34);
  addPain(s, '💸', 'Stores lose jobs to each other based on proximity alone — not quality, speed, or availability.',     0.45, 3.16);
  addPain(s, '🧾', 'No digital record of jobs, payments, or customers. Zero structured repeat-customer pipeline.',       0.45, 3.98);

  // blue stat panel right
  s.addShape('rect', {
    x: 6.35, y: 1.42, w: 3.2, h: 3.7,
    fill: { color: C.navy }, rectRadius: 0.12,
    line: { color: C.navy, width: 0 },
  });
  s.addText('73%',                                            { x: 6.55, y: 1.68, w: 2.8, h: 0.9, fontSize: 42, bold: true, fontFace: SYNE, color: C.white, charSpacing: -1 });
  s.addText('of Thrissur students\nprint at least once a month', { x: 6.55, y: 2.55, w: 2.8, h: 0.55, fontSize: 8.5, fontFace: DMSANS, color: C.dimWhite, lineSpacingMultiple: 1.4 });
  s.addShape('rect', { x: 6.75, y: 3.25, w: 0.42, h: 0.04, fill: { color: C.accent }, line: { color: C.accent, width: 0 } });
  s.addText('0',                                              { x: 6.55, y: 3.38, w: 2.8, h: 0.7,  fontSize: 42, bold: true, fontFace: SYNE, color: C.white, charSpacing: -1 });
  s.addText('of those shops have a\ndigital ordering system', { x: 6.55, y: 4.05, w: 2.8, h: 0.6,  fontSize: 8.5, fontFace: DMSANS, color: C.dimWhite, lineSpacingMultiple: 1.4 });
}

// ── 3 · SOLUTION ────────────────────────────────────────────────
{
  const s = prs.addSlide();
  s.background = { color: C.navy };

  addChip(s, 'The Solution', 0.5, 0.35);
  addHeading(s, 'One WhatsApp number.\nEvery print shop in Thrissur.', { y: 0.65, fontSize: 30, lineSpacingMultiple: 1.2 });
  addBody(s,  'Printosky is a unified print marketplace — customers message once, we route the job to the best available store, they pay online, and pick up with a secure code.', {
    y: 2.45, w: 8, fontSize: 11.5, color: 'AACCEE',
  });

  addStat(s, '1',  'WhatsApp number for all stores',      0.5, 3.6);
  addStat(s, 'N',  'Partner stores sharing the job stream', 4.4, 3.6);
  addStat(s, '0',  'Software installs required to join',  8.2, 3.6);

  s.addShape('ellipse', { x: 10.8, y: 2.4, w: 3.6, h: 3.6, fill: { color: C.white, transparency: 97 }, line: { color: C.white, width: 0 } });
}

// ── 4 · HOW IT WORKS ────────────────────────────────────────────
{
  const s = prs.addSlide();
  s.background = { color: C.white };

  addChip(s, 'Customer Journey', 0.5, 0.3);
  addHeading(s, 'How it works — in 4 steps.', { color: C.dark, y: 0.6, fontSize: 26 });

  addStep(s, '1', 'Send file via WhatsApp',        'Customer messages +91 94957 06405 with their PDF. Bot responds with a quote in seconds.',              0.45, 1.6,  true);
  addStep(s, '2', 'Confirm & pay online',          'Customer approves and pays via Razorpay (UPI, card, net banking). Full amount collected upfront.',     0.45, 2.42, true);
  addStep(s, '3', 'Job auto-routed to best store', 'Routing engine scores every partner by capacity and queue depth, assigns in real time.',               0.45, 3.24, true);
  addStep(s, '4', 'Pickup with secure code',       'Customer gets a unique P-XXXX code via WhatsApp. Show it at the counter, collect the job.',           0.45, 4.06, true);

  // chat preview panel
  const cx = 6.8;
  s.addShape('rect', { x: cx, y: 1.35, w: 3.6, h: 3.9, fill: { color: C.light }, rectRadius: 0.12, line: { color: 'DDD8D0', width: 0.3 } });

  const msgs = [
    { from: true,  who: '💬 Customer',  msg: '"Hi, 40 pages B&W A4, single side."',         bg: C.white,   whoC: C.green, msgC: C.dark  },
    { from: false, who: '🤖 Printosky', msg: '40 pages B&W A4 = ₹20. Pay here →',          bg: C.accent,  whoC: C.white, msgC: C.white },
    { from: true,  who: '✅ Confirmed', msg: '"Order confirmed. Routed to OSP Thriprayar."', bg: C.white,   whoC: C.mid,   msgC: C.dark  },
    { from: false, who: '🔔 Ready',     msg: 'Pickup code: P-4K8X\nAddress: OSP, Thriprayar', bg: C.navy, whoC: C.white, msgC: C.white },
  ];
  msgs.forEach(({ who, msg, bg, whoC, msgC }, i) => {
    const ry = 1.52 + i * 0.85;
    s.addShape('rect', { x: cx + 0.18, y: ry, w: 3.25, h: 0.72, fill: { color: bg }, rectRadius: 0.07, line: { color: bg === C.white ? 'E5E7EB' : bg, width: bg === C.white ? 0.3 : 0 } });
    s.addText(who,  { x: cx + 0.3, y: ry + 0.06, w: 3.0, h: 0.18, fontSize: 7,   bold: true, fontFace: DMSANS, color: whoC });
    s.addText(msg,  { x: cx + 0.3, y: ry + 0.27, w: 3.0, h: 0.4,  fontSize: 7.5, fontFace: DMSANS, color: msgC, lineSpacingMultiple: 1.3 });
  });
}

// ── 5 · FOR PARTNER STORES ──────────────────────────────────────
{
  const s = prs.addSlide();
  s.background = { color: C.light };

  addChip(s, 'For Partner Stores', 0.5, 0.3);
  addHeading(s, 'Join Printosky. Get jobs. No software needed.', { color: C.dark, y: 0.6, fontSize: 24 });
  addBody(s,  'Partner stores receive jobs via WhatsApp — no API, no software install. Tap ACCEPT, print, mark ready.', {
    y: 1.32, w: 9, fontSize: 10.5, color: C.mid,
  });

  const cards = [
    ['📱', 'WhatsApp dispatch',       'Jobs arrive as WhatsApp messages with the file, specs, and pickup code. Tap ACCEPT to claim.'],
    ['⚡', 'Automatic routing',       'Engine sends jobs to the best available store. No bidding. No chasing customers.'],
    ['💳', 'Instant Razorpay payouts', 'Payment splits on completion. Your share lands in your account — zero manual settlement.'],
    ['🔒', 'Pickup code verification', 'Customers show a P-XXXX code at counter. No name lookup, no receipt confusion.'],
    ['📊', 'Live order tracking',     'Customers track at printosky.com/track — no calls asking "is it done yet?"'],
    ['🛠️', 'Keep your setup',         'Your printers, your folder system — we wrap around what you have. Nothing changes.'],
  ];
  const xs = [0.45, 3.42, 6.38];
  const ys = [1.88, 3.42];
  cards.forEach(([ico, title, desc], i) => addCard(s, ico, title, desc, xs[i % 3], ys[Math.floor(i / 3)], 2.88, false));
}

// ── 6 · SMART ROUTING ───────────────────────────────────────────
{
  const s = prs.addSlide();
  s.background = { color: C.dark };

  addChip(s, 'The Technology', 0.5, 0.35);
  addHeading(s, 'Smart routing. Every job to the right store.', { y: 0.65, w: 8.5, fontSize: 26 });
  addBody(s,  'When a customer pays, our routing engine scores every partner store in real time — by capacity, queue depth, and paper availability — then assigns automatically.', {
    y: 1.62, w: 8, fontSize: 11,
  });

  // flow boxes
  const fy = 2.78;
  [
    { bx: 0.5,  label: 'Incoming Job',   val: '40pg B&W A4',  engine: false },
    { bx: 3.08, label: 'Routing Engine', val: '⚡ Scoring...', engine: true  },
    { bx: 5.65, label: 'Best Store',     val: 'OSP Thriprayar', engine: false },
  ].forEach(({ bx, label, val, engine }) => {
    s.addShape('rect', { x: bx, y: fy, w: 2.3, h: 1.08, fill: { color: engine ? C.accent : C.panel }, rectRadius: 0.08, line: { color: engine ? C.accent : C.panelBdr, width: 0 } });
    s.addText(label.toUpperCase(), { x: bx + 0.1, y: fy + 0.1, w: 2.1, h: 0.22, fontSize: 6.5, fontFace: DMSANS, color: engine ? 'FFDDCC' : C.dimWhite, align: 'center', charSpacing: 1.2 });
    s.addText(val,                  { x: bx + 0.1, y: fy + 0.36, w: 2.1, h: 0.52, fontSize: 11, bold: true, fontFace: SYNE, color: C.white, align: 'center' });
  });
  s.addText('→', { x: 2.78, y: fy + 0.28, w: 0.32, h: 0.52, fontSize: 13, color: '444455', align: 'center' });
  s.addText('→', { x: 5.35, y: fy + 0.28, w: 0.32, h: 0.52, fontSize: 13, color: '444455', align: 'center' });

  addCard(s, '', '60 s ack timeout',  "No response in 60 s? Job auto-routes to the next best store. No job left behind.", 0.45, 4.1, 2.88, true);
  addCard(s, '', 'Capacity-aware',    'Stores declare daily capacity and paper types. Engine never overloads a store.',    3.42, 4.1, 2.88, true);
  addCard(s, '', 'Full audit trail',  'Every routing decision is logged. Fairness disputes resolved with data, not arguments.', 6.38, 4.1, 2.88, true);
}

// ── 7 · AUTOMATIC PAYOUTS ───────────────────────────────────────
{
  const s = prs.addSlide();
  s.background = { color: C.white };

  addChip(s, 'Payments', 0.5, 0.3);
  addHeading(s, 'Razorpay Route splits every payment instantly.', { color: C.dark, y: 0.6, fontSize: 24, w: 9 });

  addStep(s, '₹', 'Customer pays full amount upfront',         'Collected at order time via UPI, card, or net banking.', 0.45, 1.65, true);
  addStep(s, '⚡', 'Razorpay Route splits on payment capture', "Platform fee separated automatically. Partner's share transferred directly to their account.", 0.45, 2.5, true);
  addStep(s, '✓', 'Settlement in your Razorpay account',       "No intermediaries. Direct to the partner's registered bank account.", 0.45, 3.35, true);

  // payment split panel
  const px = 6.6;
  s.addShape('rect', { x: px, y: 1.42, w: 3.5, h: 3.68, fill: { color: C.light }, rectRadius: 0.12, line: { color: 'DDD8D0', width: 0.3 } });
  s.addText('EXAMPLE: ₹100 PRINT JOB', { x: px + 0.2, y: 1.6, w: 3.1, h: 0.25, fontSize: 7, fontFace: DMSANS, color: C.mid, charSpacing: 1.2 });

  [
    { y: 2.06, bg: C.white,  bdr: 'E5E7EB', lc: C.dark,  ac: C.dark,  lbl: 'Customer pays',          amt: '₹100' },
    { y: 2.82, bg: C.accent, bdr: C.accent, lc: C.white, ac: C.white, lbl: 'Printosky platform fee',  amt: '₹10'  },
    { y: 3.58, bg: C.navy,   bdr: C.navy,   lc: C.white, ac: C.white, lbl: 'Partner store receives',  amt: '₹90'  },
  ].forEach(({ y, bg, bdr, lc, ac, lbl, amt }) => {
    s.addShape('rect', { x: px + 0.2, y, w: 3.1, h: 0.58, fill: { color: bg }, rectRadius: 0.07, line: { color: bdr, width: bg === C.white ? 0.4 : 0 } });
    s.addText(lbl, { x: px + 0.36, y: y + 0.15, w: 1.9, h: 0.28, fontSize: 9, fontFace: DMSANS, color: lc });
    s.addText(amt, { x: px + 2.3,  y: y + 0.1,  w: 0.85, h: 0.38, fontSize: 13, bold: true, fontFace: SYNE, color: ac, align: 'right' });
  });
  s.addText('Take-rate is configurable per partner.', { x: px + 0.2, y: 4.26, w: 3.1, h: 0.28, fontSize: 7.5, fontFace: DMSANS, color: C.mid, align: 'center' });
}

// ── 8 · PICKUP EXPERIENCE ───────────────────────────────────────
{
  const s = prs.addSlide();
  s.background = { color: C.light };

  addChip(s, 'Customer Experience', 0.5, 0.3);
  addHeading(s, 'Frictionless pickup. Zero confusion.', { color: C.dark, y: 0.6, fontSize: 26 });
  addBody(s,  'Every paid job gets a cryptographically random 4-character code. Customer shows it at the counter — no name lookup, no receipt, no phone call.', {
    y: 1.35, w: 5.8, fontSize: 11, color: C.mid,
  });

  // pickup code badge
  s.addShape('rect', { x: 0.45, y: 2.5, w: 2.55, h: 0.82, fill: { color: C.accent }, rectRadius: 0.1, line: { color: C.accent, width: 0 } });
  s.addText('P‑4K8X', { x: 0.45, y: 2.5, w: 2.55, h: 0.82, fontSize: 26, bold: true, fontFace: SYNE, color: C.white, align: 'center', valign: 'middle', charSpacing: 3 });

  addStep(s, '📲', 'Sent via WhatsApp when job is ready', 'Store marks ready → WhatsApp notification fires instantly.',               0.45, 3.5,  true);
  addStep(s, '🔍', 'Live tracking at printosky.com/track', 'No login. 5 stages: Received → Paid → Printing → Ready → Delivered.', 0.45, 4.32, true);

  // status tracker panel
  const tx = 6.55;
  s.addShape('rect', { x: tx, y: 1.38, w: 3.6, h: 3.75, fill: { color: C.white }, rectRadius: 0.12, line: { color: 'DDD8D0', width: 0.4 } });
  s.addText('ORDER #P‑4K8X · LIVE STATUS', { x: tx + 0.22, y: 1.56, w: 3.18, h: 0.22, fontSize: 6.5, fontFace: DMSANS, color: C.mid, charSpacing: 1.2 });

  [
    { state: 'done',   label: 'Received',         badge: '✓'           },
    { state: 'done',   label: 'Paid',             badge: '✓'           },
    { state: 'active', label: 'Printing',         badge: '⟳ In progress' },
    { state: 'future', label: 'Ready for pickup', badge: ''            },
    { state: 'future', label: 'Delivered',        badge: ''            },
  ].forEach(({ state, label, badge }, i) => {
    const ty  = 1.98 + i * 0.54;
    const dotC = state === 'done' ? C.mid : state === 'active' ? C.accent : 'DDDDDD';
    const lblC  = state === 'future' ? 'CCCCCC' : state === 'active' ? C.dark : C.mid;

    s.addShape('ellipse', { x: tx + 0.28, y: ty + 0.1, w: 0.16, h: 0.16, fill: { color: dotC }, line: { color: dotC, width: 0 } });
    s.addText(label, { x: tx + 0.58, y: ty + 0.05, w: 1.8, h: 0.3, fontSize: 9, fontFace: DMSANS, color: lblC, bold: state === 'active' });
    if (badge) {
      s.addText(badge, { x: tx + 2.4, y: ty + 0.05, w: 1.0, h: 0.3, fontSize: 7.5, fontFace: DMSANS, color: state === 'active' ? C.accent : C.mid, align: 'right' });
    }
  });
  s.addText('printosky.com/track · No login required', { x: tx + 0.22, y: 4.88, w: 3.18, h: 0.22, fontSize: 7.5, fontFace: DMSANS, color: C.mid, align: 'center' });
}

// ── 9 · TRACTION ────────────────────────────────────────────────
{
  const s = prs.addSlide();
  s.background = { color: C.navy };

  addChip(s, 'Traction', 0.5, 0.35);
  addHeading(s, 'Built on a real, live store.', { y: 0.65, fontSize: 30 });
  addBody(s,  'Printosky is not a concept deck. It runs live at Oxygen Students Paradise, Thriprayar, Thrissur — processing real print jobs, real payments, real customers every day.', {
    y: 1.65, w: 8.5, fontSize: 11.5, color: 'AACCEE',
  });

  addStat(s, '134+', 'Jobs processed via the platform',           0.5,  3.25);
  addStat(s, '2+',   'Production printers (Konica + Epson)',      4.5,  3.25);
  addStat(s, '₹0',   'Cash — 100% digital payments via Razorpay', 8.2,  3.25);

  s.addShape('rect', { x: 0.5, y: 4.6, w: 9.5, h: 0.68, fill: { color: '142050' }, rectRadius: 0.08, line: { color: '142050', width: 0 } });
  s.addText('Full stack since 2025: Store PC → Watcher → Konica / Epson  ·  WhatsApp Cloud API (Meta)  ·  Razorpay  ·  Supabase  ·  Vercel API  ·  Netlify', {
    x: 0.65, y: 4.72, w: 9.2, h: 0.45,
    fontSize: 8, fontFace: DMSANS, color: C.dimWhite, lineSpacingMultiple: 1.3,
  });

  s.addShape('ellipse', { x: 10.5, y: 4.3, w: 3.4, h: 3.4, fill: { color: C.white, transparency: 97 }, line: { color: C.white, width: 0 } });
}

// ── 10 · CTA ────────────────────────────────────────────────────
{
  const s = prs.addSlide();
  s.background = { color: C.dark };

  addChip(s, 'Join the Marketplace', 0.5, 0.35);
  addHeading(s, 'Ready to receive pre-paid\njobs — automatically?', { y: 0.65, fontSize: 30, lineSpacingMultiple: 1.2 });
  addBody(s,  "We're onboarding the first wave of partner stores in Thrissur. If your shop handles print jobs and you want a guaranteed pipeline of digital customers — let's talk.", {
    y: 2.45, w: 7.5, fontSize: 11.5,
  });

  // WhatsApp button
  s.addShape('rect', { x: 0.5, y: 3.92, w: 3.2, h: 0.62, fill: { color: C.green }, rectRadius: 0.08, line: { color: C.green, width: 0 } });
  s.addText('Message us on WhatsApp', { x: 0.5, y: 3.92, w: 3.2, h: 0.62, fontSize: 10.5, bold: true, fontFace: DMSANS, color: C.white, align: 'center', valign: 'middle' });

  // Download button
  s.addShape('rect', { x: 3.85, y: 3.92, w: 2.5, h: 0.62, fill: { color: C.panel }, rectRadius: 0.08, line: { color: C.panelBdr, width: 0.5 } });
  s.addText('↓  Download this deck', { x: 3.85, y: 3.92, w: 2.5, h: 0.62, fontSize: 10, fontFace: DMSANS, color: 'CCCCCC', align: 'center', valign: 'middle' });

  s.addText('wa.me/919495706405  ·  printosky.com', { x: 0.5, y: 4.68, w: 5.5, h: 0.3, fontSize: 9, fontFace: DMSANS, color: C.dimWhite });

  s.addShape('ellipse', { x: 10.3, y: 3.8, w: 3.6, h: 3.6, fill: { color: C.accent, transparency: 94 }, line: { color: C.accent, width: 0 } });
}

// ── WRITE ────────────────────────────────────────────────────────
prs.writeFile({ fileName: OUT })
  .then(() => {
    console.log('\n✅  Deck written to:', OUT);
    console.log('   10 slides | 16:9 wide | Printosky brand colours');
    console.log('   Open in PowerPoint or Google Slides to verify.\n');
  })
  .catch(err => {
    console.error('\n❌  Failed to write pptx:', err.message);
    process.exit(1);
  });
