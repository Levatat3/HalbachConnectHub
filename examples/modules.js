// The MORE tab: everything listed in manifest.json (written by ConnectHub) shows up here.
// Static modules are files on this site; live modules are data from the owner's PC and
// fall back to an "offline" message when that PC is off.
(function () {
  "use strict";
  var $ = function (id) { return document.getElementById(id); };
  var started = false, poll = null;

  function el(tag, cls, text) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text != null) e.textContent = text;
    return e;
  }
  function note(box, text) { box.innerHTML = ""; box.appendChild(el("p", "empty", text)); }
  var stopper = null;
  function stopPoll() { if (stopper) { stopper(); stopper = null; } }

  function timeout(ms) {
    var c = new AbortController();
    setTimeout(function () { c.abort(); }, ms);
    return c.signal;
  }

  // ---- viewers, one per module type ----
  function viewPage(box, m) {
    var f = el("iframe"); f.src = m.entry; f.title = m.name; f.loading = "lazy";
    f.setAttribute("sandbox", "allow-scripts allow-popups allow-forms");
    f.style.height = "max(520px, calc(100vh - 320px))";
    box.appendChild(f);
  }
  function viewSwf(box, m) {
    var host = el("div"); box.appendChild(host);
    var s = document.createElement("script");
    s.src = "https://unpkg.com/@ruffle-rs/ruffle@0.7.1/ruffle.js";
    s.onload = function () {
      var R = window.RufflePlayer && window.RufflePlayer.newest();
      if (!R) return note(host, "Ruffle failed to load.");
      var p = R.createPlayer(); host.appendChild(p);
      p.load({ url: m.entry, autoplay: "auto", unmuteOverlay: "visible", letterbox: "on" });
    };
    s.onerror = function () { note(host, "Couldn't load the Flash player."); };
    document.head.appendChild(s);
  }
  function viewImage(box, m) {
    var i = el("img"); i.src = m.entry; i.alt = m.name; i.style.maxWidth = "100%"; i.style.display = "block"; i.style.margin = "0 auto";
    box.appendChild(i);
  }
  function viewGallery(box, m) {
    var g = el("div", "gallery");
    (m.files || []).forEach(function (u) {
      var a = el("a"); a.href = u; a.target = "_blank"; a.rel = "noopener";
      var i = el("img"); i.src = u; i.loading = "lazy"; i.alt = "";
      a.appendChild(i); g.appendChild(a);
    });
    if (!g.children.length) note(box, "Nothing in this gallery yet."); else box.appendChild(g);
  }
  function viewFile(box, m) {
    var p = el("p", "empty"); var a = el("a", "bigbtn", "Download " + m.name); a.href = m.entry; a.setAttribute("download", "");
    p.appendChild(a); box.appendChild(p);
  }


  // ---- Markdown -> DOM (safe: text goes in with textContent, never as HTML) ----
  function safeUrl(u, base) {
    u = (u || "").trim();
    if (/^(https?:|mailto:)/i.test(u) || u.charAt(0) === "#") return u;
    if (/^[a-z][a-z0-9+.\-]*:/i.test(u) || u.charAt(0) === "/" || u.indexOf("..") >= 0) return null;   // other schemes, root paths, traversal
    return base + u.replace(/^\.\//, "");
  }
  var INLINE = /(`[^`\n]+`)|(!?\[[^\]\n]*\]\([^)\s]+(?:\s+"[^"]*")?\))|(\*\*[^*\n]+\*\*)|(__[^_\n]+__)|(\*[^*\n]+\*)|(_[^_\n]+_)/g;
  function inline(parent, text, base) {
    var last = 0, m;
    INLINE.lastIndex = 0;
    while ((m = INLINE.exec(text))) {
      if (m.index > last) parent.appendChild(document.createTextNode(text.slice(last, m.index)));
      var tok = m[0];
      if (m[1]) parent.appendChild(el("code", null, tok.slice(1, -1)));
      else if (m[2]) {
        var img = tok.charAt(0) === "!", mm = /^!?\[([^\]]*)\]\(([^)\s]+)/.exec(tok), url = safeUrl(mm[2], base);
        if (!url) parent.appendChild(document.createTextNode(mm[1]));
        else if (img) {
          if (/^https:/i.test(url) || url.indexOf(base) === 0) { var im = el("img"); im.src = url; im.alt = mm[1]; im.loading = "lazy"; parent.appendChild(im); }
          else parent.appendChild(document.createTextNode(mm[1]));
        } else {
          var a = el("a", null, mm[1] || url); a.href = url;
          if (/^https?:/i.test(url)) { a.target = "_blank"; a.rel = "noopener noreferrer"; }
          parent.appendChild(a);
        }
      }
      else {
        var under = tok.charAt(0) === "_";                                // _x_ and __x__ only count at word edges
        if (under && (/\w/.test(text.charAt(m.index - 1)) || /\w/.test(text.charAt(m.index + tok.length)))) parent.appendChild(document.createTextNode(tok));
        else if (m[3] || m[4]) parent.appendChild(el("strong", null, tok.slice(2, -2)));
        else parent.appendChild(el("em", null, tok.slice(1, -1)));
      }
      last = m.index + tok.length;
    }
    if (last < text.length) parent.appendChild(document.createTextNode(text.slice(last)));
  }
  function renderMarkdown(src, base) {
    var root = el("div", "doc"), lines = src.replace(new RegExp("^" + String.fromCharCode(0xFEFF)), "").replace(/\r\n?/g, "\n").split("\n"), i = 0;
    function startsBlock(l) { return /^(#{1,6}\s|```|>\s?|[-*+]\s|\d+[.)]\s|\s*(-{3,}|\*{3,})\s*$)/.test(l) || /^\|.*\|\s*$/.test(l); }
    while (i < lines.length) {
      var l = lines[i], m;
      if (!l.trim()) { i++; continue; }
      if ((m = /^```(.*)$/.exec(l))) {                                   // fenced code
        var code = []; i++;
        while (i < lines.length && !/^```\s*$/.test(lines[i])) code.push(lines[i++]);
        i++; var pre = el("pre"); pre.appendChild(el("code", null, code.join("\n"))); root.appendChild(pre); continue;
      }
      if ((m = /^(#{1,6})\s+(.*?)\s*#*\s*$/.exec(l))) {                  // heading
        var h = el("h" + Math.min(6, m[1].length + 1)); inline(h, m[2], base); root.appendChild(h); i++; continue;
      }
      if (/^\s*(-{3,}|\*{3,})\s*$/.test(l)) { root.appendChild(el("hr")); i++; continue; }
      if (/^>\s?/.test(l)) {                                              // blockquote
        var q = []; while (i < lines.length && /^>\s?/.test(lines[i])) q.push(lines[i++].replace(/^>\s?/, ""));
        var bq = el("blockquote"), p1 = el("p"); inline(p1, q.join(" "), base); bq.appendChild(p1); root.appendChild(bq); continue;
      }
      if (/^[-*+]\s+/.test(l) || /^\d+[.)]\s+/.test(l)) {                // list (one level)
        var ordered = /^\d/.test(l), list = el(ordered ? "ol" : "ul");
        while (i < lines.length && (ordered ? /^\d+[.)]\s+/ : /^[-*+]\s+/).test(lines[i])) {
          var li = el("li"); inline(li, lines[i].replace(/^([-*+]|\d+[.)])\s+/, ""), base); list.appendChild(li); i++;
        }
        root.appendChild(list); continue;
      }
      if (/^\|.*\|\s*$/.test(l) && i + 1 < lines.length && /^\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$/.test(lines[i + 1])) {   // table
        var cells = function (row) { return row.replace(/^\||\|\s*$/g, "").split("|").map(function (c) { return c.trim(); }); };
        var tbl = el("table"), thead = el("thead"), tr = el("tr");
        cells(l).forEach(function (c) { var th = el("th"); inline(th, c, base); tr.appendChild(th); });
        thead.appendChild(tr); tbl.appendChild(thead); i += 2;
        var tbody = el("tbody");
        while (i < lines.length && /^\|.*\|\s*$/.test(lines[i])) {
          var r = el("tr"); cells(lines[i]).forEach(function (c) { var td = el("td"); inline(td, c, base); r.appendChild(td); });
          tbody.appendChild(r); i++;
        }
        tbl.appendChild(tbody); var wrap = el("div", "tablewrap"); wrap.appendChild(tbl); root.appendChild(wrap); continue;
      }
      var para = [l]; i++;                                                // paragraph
      while (i < lines.length && lines[i].trim() && !startsBlock(lines[i])) para.push(lines[i++]);
      var pp = el("p"); inline(pp, para.join(" "), base); root.appendChild(pp);
    }
    return root;
  }
  function viewDoc(box, m) {
    var base = m.entry.replace(/[^\/]*$/, "");
    note(box, "Loading...");
    fetch(m.entry, { cache: "no-store" }).then(function (r) { if (!r.ok) throw 0; return r.text(); })
      .then(function (t) { box.innerHTML = ""; box.appendChild(renderMarkdown(t.slice(0, 500000), base)); })
      .catch(function () { note(box, "Couldn't load this page."); });
  }

  // ---- live modules ----
  // One engine for every live view: check the module is on, then poll it, redrawing with `render`.
  // It keeps retrying if the owner's PC goes away, and does not fetch while its panel is hidden.
  function runLive(box, m, man, render) {
    if (!man.live_base) return note(box, "Live data isn't connected yet.");
    var seconds = m.poll || (m.view === "pets" ? 15 : 3), timer = null, out = null;
    function stop() { if (timer) { clearInterval(timer); timer = null; } }
    function offline(text) { if (out) { out.innerHTML = ""; out.appendChild(el("p", "empty", text)); } else note(box, text); }
    function tick() {
      if (!document.body.contains(box)) return stop();
      if (box.offsetParent === null) return;                                  // panel hidden: skip this round
      fetch(man.live_base + "/m/" + encodeURIComponent(m.id), { signal: timeout(5000), cache: "no-store" })
        .then(function (r) { if (!r.ok) throw 0; return r.json(); })
        .then(function (d) { if (!out) { box.innerHTML = ""; out = el("div"); box.appendChild(out); } render(out, d, man); })
        .catch(function () { offline("Offline right now. This only works while the owner's PC is on. Trying again..."); });
    }
    function start() {
      note(box, "Checking if it's online...");
      fetch(man.live_base + "/status", { signal: timeout(5000), cache: "no-store" })
        .then(function (r) { if (!r.ok) throw 0; return r.json(); })
        .then(function (st) {
          if (!st.modules || st.modules.indexOf(m.id) < 0) throw 0;
          tick(); stop(); timer = setInterval(tick, seconds * 1000);
        })
        .catch(function () {
          note(box, "Offline right now. This only works while the owner's PC is on.");
          setTimeout(function () { if (document.body.contains(box)) start(); }, 20000);
        });
    }
    start();
    return stop;
  }

  // Generic: one object becomes rows, a list becomes a card per item.
  function renderGeneric(table, d) {
    table.innerHTML = "";
    table.className = Array.isArray(d) ? "livegrid" : "livebox";
    (Array.isArray(d) ? d : [d]).forEach(function (item) {
      var holder = Array.isArray(d) ? el("div", "livecard") : table;
      if (Array.isArray(d)) {
        if (item.name != null) holder.appendChild(el("h4", null, String(item.name)));
        table.appendChild(holder);
      }
      Object.keys(item).forEach(function (k) {
        if (Array.isArray(d) && k === "name") return;                                   // already the card's title
        if (/^max_|_max$/.test(k) && (k.replace(/^max_|_max$/, "") in item)) return;    // shown next to its value
        var v = item[k], mk = ("max_" + k in item) ? "max_" + k : (k + "_max" in item ? k + "_max" : null);
        var text = typeof v === "object" && v !== null ? JSON.stringify(v) : String(v);
        if (mk && typeof v === "number") text = v + " / " + item[mk];
        var row = el("div", "liverow"); row.appendChild(el("span", "k", k)); row.appendChild(el("span", "v", text));
        holder.appendChild(row);
      });
    });
    table.querySelectorAll(".livecard").forEach(function (c) {
      if (!c.querySelector(".liverow")) c.appendChild(el("div", "liverow", "no data right now"));
    });
    if (!table.children.length) table.appendChild(el("p", "empty", "Online, but nothing is being shared."));
  }

  // Pets: each bot's active pet drawn from a sprite sheet and animated (stand, walk, stand, walk),
  // with its abilities and what the bot plans to do next. Sheets come from a static module "pet-sprites".
  var RARITY = { Common: "rc", Uncommon: "ru", Rare: "rr", Legendary: "rl", Divine: "rd" };
  var sheets = {};
  function sheet(base, name) {
    var key = base + name;
    if (!sheets[key]) { var im = new Image(); im.src = base + encodeURIComponent(name) + ".png"; sheets[key] = im; }
    return sheets[key];
  }
  function spriteBase(man) {
    var s = (man.modules || []).filter(function (x) { return x.id === "pet-sprites"; })[0];
    return s && s.entry ? s.entry.replace(/[^\/]*$/, "") : null;
  }
  function renderPets(out, d, man) {
    var base = spriteBase(man), bots = (d && d.bots) || [];
    out.innerHTML = "";
    var grid = el("div", "petgrid"); out.appendChild(grid);
    if (!bots.length) return grid.appendChild(el("p", "empty", "No bots are reporting right now."));
    bots.forEach(function (b) {
      var card = el("div", "petcard"), pets = b.pets || [];
      var a = pets.filter(function (p) { return p.id === b.active; })[0] || pets.filter(function (p) { return p.id === b.best; })[0];
      var best = pets.filter(function (p) { return p.id === b.best; })[0];
      var canvas = el("canvas", "petsprite"); canvas.width = 8; canvas.height = 8;
      card.appendChild(canvas);
      var info = el("div", "petinfo"); card.appendChild(info);
      var head = el("div", "pethead"); head.appendChild(el("b", null, String(b.name)));
      info.appendChild(head);
      if (!a) {
        head.appendChild(el("small", null, "no pets yet"));
      } else {
        head.appendChild(el("span", "petname " + (RARITY[a.rarity] || "rc"), String(a.name)));
        head.appendChild(el("small", null, String(a.rarity)));
        var ab = el("div", "petabil");
        (a.abilities || []).forEach(function (x) {
          var chip = el("span", x.works ? "ok" : "no", x.n + " " + x.lvl + "/" + a.max);
          if (!x.works) chip.title = "does nothing on this server";
          ab.appendChild(chip);
        });
        info.appendChild(ab);
        var plan = (b.active === b.best ? "plans to keep it" : "plans to switch to " + (best ? best.name + " (" + best.rarity + ")" : "a better pet")) +
          " · " + Math.max(0, (b.total || pets.length) - 1) + " spare" + (a.gained ? " · +" + a.gained + " levels" : "") +
          (b.fuse ? " · fuse ready: 2 " + b.fuse.rarity : "");
        info.appendChild(el("div", "petplan", plan));
        if (base && a.sprite) canvas._sp = a.sprite;
      }
      grid.appendChild(card);
    });
    if (out._anim) clearInterval(out._anim);
    out._anim = setInterval(function () {                                  // 0,1,2,1: the walk cycle
      if (!document.body.contains(out)) { clearInterval(out._anim); return; }
      if (out.offsetParent === null || !base) return;
      var frame = Math.floor(Date.now() / 240);
      out.querySelectorAll("canvas.petsprite").forEach(function (c) {
        var sp = c._sp; if (!sp) return;
        var im = sheet(base, sp.s); if (!im.complete || !im.naturalWidth) return;
        var cyc = sp.frames.length > 2 ? [sp.frames[0], sp.frames[1], sp.frames[2], sp.frames[1]] : sp.frames;
        var g = c.getContext("2d");
        g.imageSmoothingEnabled = false; g.clearRect(0, 0, 8, 8);
        g.drawImage(im, (sp.x || 0) + cyc[frame % cyc.length] * sp.w, sp.y, sp.w, sp.h, 0, 0, 8, 8);
      });
    }, 120);
  }

  var VIEWS = { auto: renderGeneric, pets: renderPets };
  function viewLive(box, m, man) { return runLive(box, m, man, VIEWS[m.view] || renderGeneric); }

  // Live modules with a "tab" (a purpose-built view) get their own nav tab, added here at load time.
  function bootTabs() {
    fetch("manifest.json", { cache: "no-store" }).then(function (r) { if (!r.ok) throw 0; return r.json(); })
      .then(function (man) {
        var nav = document.querySelector(".tabs"), main = document.querySelector("main"), more = document.querySelector('.tabs a[data-tab="more"]');
        window.SITE_LOADERS = window.SITE_LOADERS || {};
        (man.modules || []).filter(function (m) { return m.kind === "live" && m.tab && /^[a-z0-9-]+$/.test(m.tab.id); }).forEach(function (m) {
          var id = m.tab.id;
          if (document.getElementById(id) || !nav || !main) return;
          var a = el("a", null, String(m.tab.label || id).toUpperCase()); a.href = "#" + id; a.dataset.tab = id; a.setAttribute("role", "tab");
          if (more && more.nextSibling) nav.insertBefore(a, more.nextSibling); else nav.appendChild(a);
          var sec = el("section", "panel"); sec.id = id;
          sec.appendChild(el("h2", null, String(m.tab.label || id).toUpperCase()));
          if (m.desc) sec.appendChild(el("p", "lede", m.desc));
          var box = el("div", "screen"); sec.appendChild(box); main.appendChild(sec);
          window.SITE_LOADERS[id] = function () { viewLive(box, m, man); };
          if (location.hash === "#" + id) window.dispatchEvent(new HashChangeEvent("hashchange"));
        });
      }).catch(function () {});
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", bootTabs); else bootTabs();

  function open(m, man) {
    stopPoll();
    var v = $("moreView"); v.hidden = false; v.innerHTML = "";
    var head = el("div", "morehead");
    head.appendChild(el("h3", null, m.name));
    var close = el("button", "mini", "Close"); close.onclick = function () { stopPoll(); v.hidden = true; v.innerHTML = ""; };
    head.appendChild(close);
    v.appendChild(head);
    if (m.desc) v.appendChild(el("p", "lede", m.desc));
    var box = el("div", "screen"); v.appendChild(box);
    if (m.kind === "live") stopper = viewLive(box, m, man);
    else if (m.type === "page") viewPage(box, m);
    else if (m.type === "game") viewSwf(box, m);
    else if (m.type === "image") viewImage(box, m);
    else if (m.type === "gallery") viewGallery(box, m);
    else if (m.type === "doc") viewDoc(box, m);
    else viewFile(box, m);
    v.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  function init() {
    if (started) return; started = true;
    var grid = $("moreGrid");
    fetch("manifest.json", { cache: "no-store" }).then(function (r) { if (!r.ok) throw 0; return r.json(); })
      .then(function (man) {
        var mods = man.modules || [];
        if (!mods.length) return note(grid, "Nothing here yet. New things show up as they're added.");
        var listed = mods.filter(function (m) { return m.listed !== false && !m.tab; });
        if (!listed.length) return note(grid, "Nothing here yet. New things show up as they're added.");
        listed.forEach(function (m) {
          var c = el("button", "card"); c.type = "button";
          c.appendChild(el("b", null, m.name));
          c.appendChild(el("span", null, m.desc || (m.kind === "live" ? "Live from the owner's PC." : "")));
          c.appendChild(document.createElement("br"));
          c.appendChild(el("span", "tag", m.kind === "live" ? "live" : (m.type || "file")));
          c.onclick = function () { open(m, man); };
          grid.appendChild(c);
        });
      })
      .catch(function () { note(grid, "Nothing here yet. New things show up as they're added."); });
  }

  window.ConnectModules = { init: init, renderMarkdown: renderMarkdown };
})();
