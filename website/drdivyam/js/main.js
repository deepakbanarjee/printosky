/* drdivyam.com — seasonal scroll engine (GSAP ScrollTrigger) */
(function () {
  "use strict";

  document.getElementById("year").textContent = new Date().getFullYear();

  var reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  if (reducedMotion || typeof gsap === "undefined") {
    document.body.classList.add("reduced-motion");
    return; // CSS fallback shows everything, static venal sky stays
  }

  gsap.registerPlugin(ScrollTrigger);

  var seasons = [
    { section: "#venal",        sky: ".sky--venal",   dark: false, key: "venal"   },
    { section: "#edavappathi",  sky: ".sky--monsoon", dark: true,  key: "monsoon" },
    { section: "#thulavarsham", sky: ".sky--thula",   dark: false, key: "thula"   },
    { section: "#koythu",       sky: ".sky--harvest", dark: false, key: "harvest" }
  ];

  // Drives the augmented cursor's seasonal appearance (CSS keys off this)
  document.body.dataset.season = "venal";

  var mlEl = document.getElementById("seasonMl");
  var enEl = document.getElementById("seasonEn");

  seasons.forEach(function (s) {
    var sectionEl = document.querySelector(s.section);
    var skyEl = document.querySelector(s.sky);

    // Crossfade this season's sky in as its chapter approaches,
    // out as the next chapter takes over.
    gsap.to(skyEl, {
      opacity: 1,
      ease: "none",
      scrollTrigger: {
        trigger: sectionEl,
        start: "top 90%",
        end: "top 20%",
        scrub: 0.8
      }
    });
    gsap.to(skyEl, {
      opacity: 0,
      ease: "none",
      scrollTrigger: {
        trigger: sectionEl,
        start: "bottom 80%",
        end: "bottom 10%",
        scrub: 0.8
      }
    });

    // Header season label + light/dark ink switch
    ScrollTrigger.create({
      trigger: sectionEl,
      start: "top 45%",
      end: "bottom 45%",
      onToggle: function (self) {
        if (!self.isActive) return;
        mlEl.textContent = sectionEl.dataset.seasonMl;
        enEl.textContent = sectionEl.dataset.seasonEn;
        document.body.classList.toggle("on-dark", s.dark);
        document.body.dataset.season = s.key;
      }
    });
  });

  // Staged text/content reveals
  gsap.utils.toArray(".reveal").forEach(function (el) {
    gsap.to(el, {
      opacity: 1,
      y: 0,
      duration: 0.9,
      ease: "power2.out",
      scrollTrigger: { trigger: el, start: "top 88%" }
    });
  });

  // The memorable moment: poems fall into view like monsoon rain
  gsap.utils.toArray(".poem-card").forEach(function (card, i) {
    gsap.from(card, {
      opacity: 0,
      y: -120,
      duration: 1.1,
      ease: "power3.out",
      delay: i * 0.12,
      scrollTrigger: { trigger: ".poems__fall", start: "top 80%" }
    });
  });

  // Gentle parallax on the hero name against the dawn sky
  gsap.to(".hero__name", {
    yPercent: -18,
    ease: "none",
    scrollTrigger: {
      trigger: ".hero",
      start: "top top",
      end: "bottom top",
      scrub: 0.8
    }
  });

  // ---- Augmented cursor: follow + monsoon ripples ----
  var cursor = document.getElementById("cursor");
  var cx = window.innerWidth / 2, cy = window.innerHeight / 2, tx = cx, ty = cy;
  var lastRipple = 0;

  window.addEventListener("mousemove", function (ev) {
    tx = ev.clientX; ty = ev.clientY;
    if (document.body.dataset.season === "monsoon") spawnRipple(ev.clientX, ev.clientY);
  });
  (function raf() {
    cx += (tx - cx) * 0.22; cy += (ty - cy) * 0.22;
    cursor.style.transform = "translate(" + cx + "px," + cy + "px) translate(-50%,-50%)";
    requestAnimationFrame(raf);
  })();

  function spawnRipple(x, y) {
    var now = performance.now();
    if (now - lastRipple < 90) return;
    lastRipple = now;
    var r = document.createElement("div");
    r.className = "ripple"; r.style.left = x + "px"; r.style.top = y + "px";
    document.body.appendChild(r);
    setTimeout(function () { r.remove(); }, 900);
  }

  // ---- Monsoon drizzle: random drops with prevailing wind drift ----
  var rainField = document.getElementById("rainField");
  for (var d = 0; d < 60; d++) {
    var drop = document.createElement("i");
    var w = 0.8 + Math.random() * 1.4;                 // 0.8–2.2px wide
    drop.style.left = (Math.random() * 100) + "%";
    drop.style.width = w.toFixed(2) + "px";
    drop.style.height = (w * (7 + Math.random() * 9)).toFixed(1) + "px";
    drop.style.animationDuration = (0.9 + Math.random() * 1.1) + "s";
    drop.style.animationDelay = (-Math.random() * 2) + "s";
    drop.style.opacity = (0.35 + Math.random() * 0.5).toFixed(2);
    var drift = 18 + Math.random() * 54;               // prevailing rightward wind
    if (Math.random() < 0.15) drift = -drift * 0.4;    // occasional back-gust
    drop.style.setProperty("--drift", drift.toFixed(0) + "px");
    rainField.appendChild(drop);
  }

  // ---- Koythu drifting motes ----
  var moteField = document.getElementById("moteField");
  for (var i = 0; i < 26; i++) {
    var g = document.createElement("i");
    g.style.left = (Math.random() * 100) + "%";
    g.style.top = (Math.random() * 100) + "%";
    g.style.animationDuration = (7 + Math.random() * 8) + "s";
    g.style.animationDelay = (-Math.random() * 10) + "s";
    g.style.opacity = (0.25 + Math.random() * 0.4).toFixed(2);
    moteField.appendChild(g);
  }
})();

/* ---- Voices: live testimonials from the drdivyam Supabase project ----
   Separate IIFE so it runs even when the motion engine above early-returns
   (reduced motion / no GSAP). The anon key is public by design; RLS on the
   `testimonials` table restricts anonymous reads to status = 'approved'. */
(function () {
  "use strict";

  var SB_URL = "https://kygngswtynxaawrpxkuq.supabase.co";
  var SB_ANON = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Imt5Z25nc3d0eW54YWF3cnB4a3VxIiwicm9sZSI6ImFub24iLCJpYXQiOjE3ODMzNjgyNjQsImV4cCI6MjA5ODk0NDI2NH0.cAG4ncF4J5sGmUUu_AAlm3wRw5GgYN01j5oklWMmGJ4";

  if (typeof fetch === "undefined") return;

  function sbGet(pathAndQuery) {
    return fetch(SB_URL + "/rest/v1/" + pathAndQuery, {
      headers: { apikey: SB_ANON, Authorization: "Bearer " + SB_ANON }
    }).then(function (res) { return res.ok ? res.json() : []; });
  }

  // ---- Voices (approved testimonials) ----
  (function () {
    var wrap = document.getElementById("testimonials");
    var grid = document.getElementById("testimonialsGrid");
    if (!wrap || !grid) return;

    sbGet("testimonials?select=author_name,author_role,body_ml,body_en,received_on"
      + "&status=eq.approved&order=featured.desc,sort_order.asc,received_on.desc.nullslast")
      .then(function (rows) {
        if (!Array.isArray(rows) || rows.length === 0) return; // none → stay hidden

        rows.forEach(function (row, idx) {
          var fig = document.createElement("figure");
          fig.className = "quote quote--live";
          fig.style.animationDelay = (idx * 0.12).toFixed(2) + "s";

          var bq = document.createElement("blockquote");
          bq.setAttribute("lang", "ml");
          bq.textContent = row.body_ml || "";          // textContent = no HTML injection
          fig.appendChild(bq);

          var cap = document.createElement("figcaption");
          cap.appendChild(document.createTextNode("— "));
          var nm = document.createElement("span");
          nm.setAttribute("lang", "ml");
          nm.textContent = row.author_name || "";
          cap.appendChild(nm);
          if (row.author_role) cap.appendChild(document.createTextNode(", " + row.author_role));
          fig.appendChild(cap);

          grid.appendChild(fig);
        });
        wrap.hidden = false;
      })
      .catch(function () { /* fail quietly: section stays hidden */ });
  })();

  // ---- Study materials (published) ----
  (function () {
    var wrap = document.getElementById("resources");
    var grid = document.getElementById("resourcesGrid");
    if (!wrap || !grid) return;

    sbGet("study_materials?select=title,description,subject,class_level,material_type,file_url,external_url"
      + "&published=eq.true&order=featured.desc,sort_order.asc,created_at.desc")
      .then(function (rows) {
        if (!Array.isArray(rows) || rows.length === 0) return; // none → stay hidden

        rows.forEach(function (row, idx) {
          var card = document.createElement("article");
          card.className = "material-card material-card--live";
          card.style.animationDelay = (idx * 0.08).toFixed(2) + "s";

          var h = document.createElement("h4");
          h.textContent = row.title || "Untitled";
          card.appendChild(h);

          var metaBits = [];
          if (row.subject) metaBits.push(row.subject);
          if (row.class_level) metaBits.push(row.class_level);
          if (metaBits.length) {
            var meta = document.createElement("p");
            meta.className = "material-card__meta";
            meta.textContent = metaBits.join(" · ");
            card.appendChild(meta);
          }

          if (row.description) {
            var desc = document.createElement("p");
            desc.className = "material-card__desc";
            desc.textContent = row.description;
            card.appendChild(desc);
          }

          var href = row.file_url || row.external_url;
          if (href) {
            var a = document.createElement("a");
            a.className = "btn";
            a.href = href;
            a.rel = "noopener";
            if (row.material_type === "file") {
              a.setAttribute("download", "");
              a.textContent = "Download ↓";
            } else {
              a.target = "_blank";
              a.textContent = "Open ↗";
            }
            card.appendChild(a);
          }

          grid.appendChild(card);
        });
        wrap.hidden = false;
      })
      .catch(function () { /* fail quietly: section stays hidden */ });
  })();
})();
