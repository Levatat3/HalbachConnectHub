// halbach-site.js: the small generic runtime every generated page loads.
//
// Each generated page is a real HTML file containing the page shell and its blocks as inert JSON:
//   <script type="application/json" id="hs-page">{ site, page, blocks, assets, theme }</script>
// This file reads that JSON, draws the page's blocks, and loads each block's script only when needed.
//
// Host API v1 (see docs/design/PLUGINS.md). Block and view scripts register themselves:
//   HalbachSite.registerBlock(type, function (container, props, ctx) {})
//   HalbachSite.registerView(name, function (container, data, ctx) {})     // live-data looks
//   HalbachSite.registerEffect(name, function (ctx) {})                    // theme effects
// Data is only ever put into the page with textContent (ctx.el); nothing here builds HTML from data.
(function () {
  "use strict";
  var HS = window.HalbachSite = window.HalbachSite || {};
  HS.blocks = HS.blocks || {}; HS.views = HS.views || {}; HS.effects = HS.effects || {};
  HS.registerBlock = function (t, fn) { HS.blocks[t] = fn; };
  HS.registerView = function (n, fn) { HS.views[n] = fn; };
  HS.registerEffect = function (n, fn) { HS.effects[n] = fn; };

  var PAGE = null, MANIFEST = null, MANIFEST_P = null, SCRIPTS = {};
  var REDUCED = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  // ---------------------------------------------------------------- small helpers
  function el(tag, cls, text) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text != null) e.textContent = text;
    return e;
  }
  function note(box, text) { box.innerHTML = ""; box.appendChild(el("p", "hs-empty", text)); }
  function timeout(ms) { var c = new AbortController(); setTimeout(function () { c.abort(); }, ms); return c.signal; }
  function base() { return (PAGE && PAGE.site && PAGE.site.base) || ""; }
  // A site address with the preview prefix when previewing. Idempotent, so an address that already has it is left alone.
  function url(path) {
    var b = base();
    if (b && (path === b || path.indexOf(b + "/") === 0)) return path;
    return b + (path.charAt(0) === "/" ? path : "/" + path);
  }

  function loadScript(src) {
    if (!src) return Promise.resolve();
    if (SCRIPTS[src]) return SCRIPTS[src];
    SCRIPTS[src] = new Promise(function (ok, bad) {
      var s = document.createElement("script"); s.src = src; s.async = true;
      s.onload = ok; s.onerror = function () { delete SCRIPTS[src]; bad(new Error("Could not load " + src)); };
      document.head.appendChild(s);
    });
    return SCRIPTS[src];
  }
  function manifest() {
    if (!MANIFEST_P) {
      MANIFEST_P = fetch(url("/manifest.json"), { cache: "no-store" }).then(function (r) { if (!r.ok) throw 0; return r.json(); })
        .then(function (m) { MANIFEST = m; return m; }).catch(function () { MANIFEST = { modules: [], live_base: null }; return MANIFEST; });
    }
    return MANIFEST_P;
  }
  function findModule(id) { var m = (MANIFEST && MANIFEST.modules || []).filter(function (x) { return x.id === id; })[0]; return m || null; }

  // ---------------------------------------------------------------- Markdown -> DOM (safe)
  function safeUrl(u, dir) {
    u = (u || "").trim();
    if (/^(https?:|mailto:)/i.test(u) || u.charAt(0) === "#") return u;
    if (/^[a-z][a-z0-9+.\-]*:/i.test(u) || u.charAt(0) === "/" || u.indexOf("..") >= 0) return null;
    return dir + u.replace(/^\.\//, "");
  }
  var INLINE = /(`[^`\n]+`)|(!?\[[^\]\n]*\]\([^)\s]+(?:\s+"[^"]*")?\))|(\*\*[^*\n]+\*\*)|(__[^_\n]+__)|(\*[^*\n]+\*)|(_[^_\n]+_)/g;
  function inline(parent, text, dir) {
    var last = 0, m; INLINE.lastIndex = 0;
    while ((m = INLINE.exec(text))) {
      if (m.index > last) parent.appendChild(document.createTextNode(text.slice(last, m.index)));
      var tok = m[0];
      if (m[1]) parent.appendChild(el("code", null, tok.slice(1, -1)));
      else if (m[2]) {
        var img = tok.charAt(0) === "!", mm = /^!?\[([^\]]*)\]\(([^)\s]+)/.exec(tok), u = safeUrl(mm[2], dir);
        if (!u) parent.appendChild(document.createTextNode(mm[1]));
        else if (img) {
          if (/^https:/i.test(u) || u.indexOf(dir) === 0) { var im = el("img"); im.src = u; im.alt = mm[1]; im.loading = "lazy"; parent.appendChild(im); }
          else parent.appendChild(document.createTextNode(mm[1]));
        } else {
          var a = el("a", null, mm[1] || u); a.href = u;
          if (/^https?:/i.test(u)) { a.target = "_blank"; a.rel = "noopener noreferrer"; }
          parent.appendChild(a);
        }
      } else {
        var under = tok.charAt(0) === "_";
        if (under && (/\w/.test(text.charAt(m.index - 1)) || /\w/.test(text.charAt(m.index + tok.length)))) parent.appendChild(document.createTextNode(tok));
        else if (m[3] || m[4]) parent.appendChild(el("strong", null, tok.slice(2, -2)));
        else parent.appendChild(el("em", null, tok.slice(1, -1)));
      }
      last = m.index + tok.length;
    }
    if (last < text.length) parent.appendChild(document.createTextNode(text.slice(last)));
  }
  function markdown(src, dir) {
    dir = dir || "";
    var root = el("div", "hs-doc"), lines = String(src).replace(new RegExp("^" + String.fromCharCode(0xFEFF)), "").replace(/\r\n?/g, "\n").split("\n"), i = 0;
    function startsBlock(l) { return /^(#{1,6}\s|```|>\s?|[-*+]\s|\d+[.)]\s|\s*(-{3,}|\*{3,})\s*$)/.test(l) || /^\|.*\|\s*$/.test(l); }
    while (i < lines.length) {
      var l = lines[i], m;
      if (!l.trim()) { i++; continue; }
      if (/^```/.test(l)) {
        var code = []; i++;
        while (i < lines.length && !/^```\s*$/.test(lines[i])) code.push(lines[i++]);
        i++; var pre = el("pre"); pre.appendChild(el("code", null, code.join("\n"))); root.appendChild(pre); continue;
      }
      if ((m = /^(#{1,6})\s+(.*?)\s*#*\s*$/.exec(l))) { var h = el("h" + Math.min(6, m[1].length + 1)); inline(h, m[2], dir); root.appendChild(h); i++; continue; }
      if (/^\s*(-{3,}|\*{3,})\s*$/.test(l)) { root.appendChild(el("hr")); i++; continue; }
      if (/^>\s?/.test(l)) {
        var q = []; while (i < lines.length && /^>\s?/.test(lines[i])) q.push(lines[i++].replace(/^>\s?/, ""));
        var bq = el("blockquote"), p1 = el("p"); inline(p1, q.join(" "), dir); bq.appendChild(p1); root.appendChild(bq); continue;
      }
      if (/^[-*+]\s+/.test(l) || /^\d+[.)]\s+/.test(l)) {
        var ordered = /^\d/.test(l), list = el(ordered ? "ol" : "ul");
        while (i < lines.length && (ordered ? /^\d+[.)]\s+/ : /^[-*+]\s+/).test(lines[i])) {
          var li = el("li"); inline(li, lines[i].replace(/^([-*+]|\d+[.)])\s+/, ""), dir); list.appendChild(li); i++;
        }
        root.appendChild(list); continue;
      }
      if (/^\|.*\|\s*$/.test(l) && i + 1 < lines.length && /^\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$/.test(lines[i + 1])) {
        var cells = function (row) { return row.replace(/^\||\|\s*$/g, "").split("|").map(function (c) { return c.trim(); }); };
        var tbl = el("table"), thead = el("thead"), tr = el("tr");
        cells(l).forEach(function (c) { var th = el("th"); inline(th, c, dir); tr.appendChild(th); });
        thead.appendChild(tr); tbl.appendChild(thead); i += 2;
        var tbody = el("tbody");
        while (i < lines.length && /^\|.*\|\s*$/.test(lines[i])) {
          var r = el("tr"); cells(lines[i]).forEach(function (c) { var td = el("td"); inline(td, c, dir); r.appendChild(td); }); tbody.appendChild(r); i++;
        }
        tbl.appendChild(tbody); var wrap = el("div", "hs-tablewrap"); wrap.appendChild(tbl); root.appendChild(wrap); continue;
      }
      var para = [l]; i++;
      while (i < lines.length && lines[i].trim() && !startsBlock(lines[i])) para.push(lines[i++]);
      var pp = el("p"); inline(pp, para.join(" "), dir); root.appendChild(pp);
    }
    return root;
  }

  // ---------------------------------------------------------------- live data engine
  function viewFor(name) {
    var v = (PAGE.assets && PAGE.assets.views || {})[name || "auto"] || (PAGE.assets.views || {}).auto;
    if (HS.views[name || "auto"]) return Promise.resolve(HS.views[name || "auto"]);
    return loadScript(v).then(function () { return HS.views[name || "auto"] || HS.views.auto; });
  }
  // Polls a live module with online/offline handling. Returns a stop function.
  function runLive(box, moduleId, render, opts) {
    opts = opts || {};
    var stopped = false, timer = null, out = null, retry = null;
    function stop() { stopped = true; if (timer) clearInterval(timer); if (retry) clearTimeout(retry); }
    manifest().then(function (man) {
      if (!man.live_base) return note(box, "Live data isn't connected yet.");
      var entry = findModule(moduleId) || {}, seconds = opts.poll || entry.poll || 3;
      function offline(text) { if (out) { out.innerHTML = ""; out.appendChild(el("p", "hs-empty", text)); } else note(box, text); }
      function tick() {
        if (stopped) return;
        if (!document.body.contains(box)) return stop();
        if (box.offsetParent === null) return;
        fetch(man.live_base + "/m/" + encodeURIComponent(moduleId), { signal: timeout(5000), cache: "no-store" })
          .then(function (r) { if (!r.ok) throw 0; return r.json(); })
          .then(function (d) { if (!out) { box.innerHTML = ""; out = el("div"); box.appendChild(out); } render(out, d, ctxFor(null)); })
          .catch(function () { offline("Offline right now. This only works while the owner's PC is on. Trying again..."); });
      }
      function start() {
        if (stopped) return;
        note(box, "Checking if it's online...");
        fetch(man.live_base + "/status", { signal: timeout(5000), cache: "no-store" })
          .then(function (r) { if (!r.ok) throw 0; return r.json(); })
          .then(function (st) { if (!st.modules || st.modules.indexOf(moduleId) < 0) throw 0; tick(); timer = setInterval(tick, seconds * 1000); })
          .catch(function () { note(box, "Offline right now. This only works while the owner's PC is on."); retry = setTimeout(function () { if (document.body.contains(box)) start(); }, 20000); });
      }
      start();
    });
    return stop;
  }

  // ---------------------------------------------------------------- module viewers
  function viewModule(box, m) {
    box.innerHTML = "";
    if (m.kind === "live") return viewFor(m.view || "auto").then(function (fn) { runLive(box, m.id, fn, {}); });
    var type = m.type;
    if (type === "page") {
      var f = el("iframe", "hs-frame"); f.src = url("/" + m.entry); f.title = m.name; f.loading = "lazy";
      f.setAttribute("sandbox", "allow-scripts allow-popups allow-forms"); box.appendChild(f);
    } else if (type === "doc") {
      note(box, "Loading...");
      fetch(url("/" + m.entry), { cache: "no-store" }).then(function (r) { if (!r.ok) throw 0; return r.text(); })
        .then(function (t) { box.innerHTML = ""; box.appendChild(markdown(t.slice(0, 500000), url("/" + m.entry.replace(/[^\/]*$/, "")))); })
        .catch(function () { note(box, "Could not load this page."); });
    } else if (type === "game") {
      if (HS.blocks["ruffle-game"] && PAGE.assets.blocks["ruffle-game"]) HS.playSwf(box, url("/" + m.entry));
      else loadScript(PAGE.assets.blocks["ruffle-game"]).then(function () { HS.playSwf(box, url("/" + m.entry)); }).catch(function () { note(box, "The Flash player block is not available on this page."); });
    } else if (type === "image") {
      var im = el("img", "hs-img"); im.src = url("/" + m.entry); im.alt = m.name; box.appendChild(im);
    } else if (type === "gallery") {
      var g = el("div", "hs-gallery");
      (m.files || []).forEach(function (u) { var a = el("a"); a.href = url("/" + u); a.target = "_blank"; a.rel = "noopener"; var i2 = el("img"); i2.src = url("/" + u); i2.loading = "lazy"; i2.alt = ""; a.appendChild(i2); g.appendChild(a); });
      if (!g.children.length) note(box, "Nothing in this gallery yet."); else box.appendChild(g);
    } else {
      var p = el("p", "hs-empty"), a2 = el("a", "hs-button", "Download " + m.name); a2.href = url("/" + m.entry); a2.setAttribute("download", ""); p.appendChild(a2); box.appendChild(p);
    }
    return Promise.resolve();
  }

  // ---------------------------------------------------------------- the context given to blocks and views
  function ctxFor(blockInfo) {
    var cleanups = [];
    return {
      el: el, markdown: markdown, url: url, note: note, theme: (PAGE.theme || {}), page: PAGE.page, site: PAGE.site,
      reducedMotion: REDUCED, manifest: MANIFEST,
      module: function (id) { return manifest().then(function () { return findModule(id); }); },
      modules: function () { return manifest().then(function (m) { return m.modules || []; }); },
      moduleSync: findModule,
      mod: { view: viewModule },
      view: viewFor,
      live: { run: runLive },
      loadScript: loadScript,
      onVisible: function (fn) {
        var host = blockInfo && blockInfo.container || document.body, done = false;
        function go() { if (!done) { done = true; fn(); } }
        var r = host.getBoundingClientRect();
        if (r.top < (window.innerHeight || 800) + 200 && r.bottom > -200) return go();     // already (nearly) on screen
        if (!("IntersectionObserver" in window)) return go();
        var io = new IntersectionObserver(function (es) { if (es.some(function (e) { return e.isIntersecting; })) { io.disconnect(); go(); } }, { rootMargin: "200px" });
        io.observe(host);
      },
      onRemove: function (fn) { cleanups.push(fn); }, _cleanups: cleanups
    };
  }

  // ---------------------------------------------------------------- boot
  function legacyHash() {
    var h = (location.hash || "").replace(/^#/, "");
    var map = PAGE.site && PAGE.site.hash_map;
    if (h && map && map[h] && map[h] !== (PAGE.page.path || "")) { location.replace(url(map[h])); return true; }
    return false;
  }
  function renderBlocks() {
    var main = document.getElementById("hs-main");
    (PAGE.blocks || []).forEach(function (b) {
      var sec = el("section", "hs-block hs-block-" + b.type); sec.setAttribute("data-block", b.id);
      main.appendChild(sec);
      var ctx = ctxFor({ container: sec });
      loadScript(PAGE.assets.blocks[b.type]).then(function () {
        var fn = HS.blocks[b.type];
        if (!fn) throw new Error("block '" + b.type + "' did not register");
        fn(sec, b.props || {}, ctx);
      }).catch(function (e) { sec.appendChild(el("p", "hs-empty", "This block could not be shown.")); if (window.console) console.warn("halbach:", b.type, e); });
    });
  }
  function runEffects() {
    var list = (PAGE.theme && PAGE.theme.effects) || [];
    var go = function () { list.forEach(function (n) { if (HS.effects[n]) try { HS.effects[n](ctxFor(null)); } catch (e) { if (window.console) console.warn("effect", n, e); } }); };
    if (PAGE.assets.theme_script) loadScript(PAGE.assets.theme_script).then(go).catch(function () {}); else go();
  }
  function markCurrentNav() {
    var links = document.querySelectorAll(".hs-nav a[data-page]");
    for (var i = 0; i < links.length; i++) if (links[i].getAttribute("data-page") === PAGE.page.id) links[i].setAttribute("aria-current", "page");
  }
  function boot() {
    var node = document.getElementById("hs-page");
    if (!node) return;
    try { PAGE = JSON.parse(node.textContent); } catch (e) { return; }
    HS.page = PAGE;
    if (legacyHash()) return;
    markCurrentNav(); renderBlocks(); runEffects();
  }
  HS.boot = boot; HS.ctx = ctxFor; HS.util = { el: el, markdown: markdown, runLive: runLive, loadScript: loadScript, url: url };
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot); else boot();
})();
