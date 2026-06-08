/* Printosky promo banner — announces "Sell your notes" + "Refer & Earn".
 * Self-contained: injects its own styles, dismissible via localStorage.
 * Include once per page:  <script src="banner.js" defer></script>
 */
(function () {
  try { if (localStorage.getItem('psky_promo_dismissed') === '1') return; } catch (e) {}

  var css = document.createElement('style');
  css.textContent = [
    '#psky-promo{position:relative;z-index:200;display:flex;align-items:center;justify-content:center;',
    'gap:8px;background:linear-gradient(90deg,#E8500A,#1B3F8B);color:#fff;',
    'font-family:"DM Sans",system-ui,sans-serif;font-size:13.5px;font-weight:500;',
    'padding:9px 42px 9px 16px;text-align:center;line-height:1.35;}',
    '#psky-promo a{color:#fff;text-decoration:none;}',
    '#psky-promo a strong{font-weight:800;}',
    '#psky-promo .psky-arrow{text-decoration:underline;white-space:nowrap;}',
    '#psky-promo button{position:absolute;right:10px;top:50%;transform:translateY(-50%);',
    'background:rgba(255,255,255,.18);border:none;color:#fff;width:22px;height:22px;',
    'border-radius:50%;font-size:15px;line-height:1;cursor:pointer;padding:0;}',
    '#psky-promo button:hover{background:rgba(255,255,255,.32);}',
    '@media(max-width:480px){#psky-promo{font-size:12.5px;padding:8px 36px 8px 12px;}}'
  ].join('');
  document.head.appendChild(css);

  var bar = document.createElement('div');
  bar.id = 'psky-promo';

  var a = document.createElement('a');
  a.href = '/account';
  a.innerHTML = '🎁 <strong>New:</strong> Sell your class notes &amp; refer friends — earn store credit ' +
                '<span class="psky-arrow">Start now &rarr;</span>';

  var btn = document.createElement('button');
  btn.type = 'button';
  btn.setAttribute('aria-label', 'Dismiss');
  btn.textContent = '×';
  btn.onclick = function () {
    bar.remove();
    try { localStorage.setItem('psky_promo_dismissed', '1'); } catch (e) {}
  };

  bar.appendChild(a);
  bar.appendChild(btn);

  function mount() { if (document.body) document.body.insertBefore(bar, document.body.firstChild); }
  if (document.body) mount();
  else document.addEventListener('DOMContentLoaded', mount);
})();
