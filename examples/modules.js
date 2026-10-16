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
  function stopPoll() { if (poll) { clearInterval(poll); poll = null; } }

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

  // ---- live modules ----
  function viewLive(box, m, man) {
    if (!man.live_base) return note(box, "Live data isn't connected yet.");
    note(box, "Checking if it's online...");
    fetch(man.live_base + "/status", { signal: timeout(5000), cache: "no-store" })
      .then(function (r) { if (!r.ok) throw 0; return r.json(); })
      .then(function (st) {
        if (!st.modules || st.modules.indexOf(m.id) < 0) throw 0;
        var table = el("div", "livebox"); box.innerHTML = ""; box.appendChild(table);
        function tick() {
          fetch(man.live_base + "/m/" + encodeURIComponent(m.id), { signal: timeout(5000), cache: "no-store" })
            .then(function (r) { if (!r.ok) throw 0; return r.json(); })
            .then(function (d) {
              table.innerHTML = "";
              Object.keys(d).forEach(function (k) {
                var row = el("div", "liverow"); row.appendChild(el("span", "k", k));
                var v = d[k]; row.appendChild(el("span", "v", typeof v === "object" ? JSON.stringify(v) : String(v)));
                table.appendChild(row);
              });
              if (!table.children.length) table.appendChild(el("p", "empty", "Online, but nothing is being shared."));
            })
            .catch(function () { stopPoll(); note(box, "Went offline. The owner's PC is probably off."); });
        }
        tick(); stopPoll(); poll = setInterval(tick, 3000);
      })
      .catch(function () { note(box, "Offline right now. This one only works while the owner's PC is on."); });
  }

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
    if (m.kind === "live") viewLive(box, m, man);
    else if (m.type === "page") viewPage(box, m);
    else if (m.type === "game") viewSwf(box, m);
    else if (m.type === "image") viewImage(box, m);
    else if (m.type === "gallery") viewGallery(box, m);
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
        mods.forEach(function (m) {
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

  window.ConnectModules = { init: init };
})();
