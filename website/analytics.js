// Printosky site analytics (GA4).
// Set MEASUREMENT_ID once a GA4 property exists for printosky.com — everything
// below stays completely inert (no network requests, no cookies) until then.
// analytics.google.com -> Admin -> Create Property -> Web data stream -> copy the
// Measurement ID (format G-XXXXXXXXXX) into the constant below.
(function () {
  var MEASUREMENT_ID = 'G-2TDPDR5215';
  if (!MEASUREMENT_ID) return;

  window.dataLayer = window.dataLayer || [];
  function gtag() { dataLayer.push(arguments); }
  window.gtag = gtag;
  gtag('js', new Date());
  gtag('config', MEASUREMENT_ID);

  var s = document.createElement('script');
  s.async = true;
  s.src = 'https://www.googletagmanager.com/gtag/js?id=' + MEASUREMENT_ID;
  document.head.appendChild(s);
})();
