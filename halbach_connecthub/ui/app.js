"use strict";
(function () {
  var TOKEN = document.querySelector('meta[name="halbach-token"]').content;
  var $ = function (s) { return document.querySelector(s); };
  var $$ = function (s) { return Array.prototype.slice.call(document.querySelectorAll(s)); };
  var S = { modules: [], settings: {}, last_deploy: null, pending: [] };
  var sel = null;            // module id open in the drawer
  var view = "dash";
  var add = { step: 1, kind: null };
  var ICON = { live: "i-eye", static: "i-file" };
  var LV = { port: null, stats: {}, tunnel: {} };
  var busy = null;           // {preview} while a publish runs
  var seenNew = null;        // ids of unreviewed submissions already announced (null until the first load)

  function esc(t) {
    return String(t == null ? "" : t).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }

  function api(path, method, body) {
    return fetch(path, {
      method: method || "GET",
      headers: { "X-Halbach-Token": TOKEN, "Content-Type": "application/json" },
      body: body ? JSON.stringify(body) : undefined,
    }).then(function (r) {
      return r.json().then(function (j) {
        if (!r.ok) throw new Error(j.error || ("Error " + r.status));
        return j;
      });
    });
  }

  function toast(t) {
    var e = $("#toast"); e.textContent = t; e.classList.add("show");
    clearTimeout(toast.t); toast.t = setTimeout(function () { e.classList.remove("show"); }, 2600);
  }
  function fail(e) { toast(e.message || "Something went wrong"); }

  function ago(ts) {
    if (!ts) return "never";
    var s = Math.max(0, Date.now() / 1000 - ts);
    if (s < 90) return "just now";
    if (s < 5400) return Math.round(s / 60) + " min ago";
    if (s < 129600) return Math.round(s / 3600) + " h ago";
    return Math.round(s / 86400) + " days ago";
  }
  function mb(b) { return b >= 1048576 ? (b / 1048576).toFixed(1) + " MB" : Math.max(1, Math.round(b / 1024)) + " KB"; }
  function find(id) { return S.modules.filter(function (m) { return m.id === id; })[0]; }

  // ---------- rendering ----------
  function status(m) {
    if (m.missing) return '<i class="dot bad"></i><span style="color:var(--red)">source missing</span>';
    if (m.pending) {
      var w = { add: "WILL PUBLISH", update: "CHANGED", remove: "WILL REMOVE" }[m.pending];
      return '<i class="dot warn"></i><span class="chg">' + w + "</span>";
    }
    if (m.kind === "live") {
      var vv = (LV.stats[m.id] || {}).viewers || 0, tn = LV.tunnel || {};
      return m.on ? '<i class="dot ' + (tn.connected ? "ok pulse" : "warn") + '"></i>' + (tn.connected ? "serving · " + vv + " viewing" : "on · tunnel not running") : '<i class="dot"></i>off';
    }
    return m.on ? '<i class="dot ok"></i>on the site' : '<i class="dot"></i>off';
  }

  function card(m, toggle) {
    var tail = m.kind === "static" ? (m.file_count + " file" + (m.file_count === 1 ? "" : "s") + " · " + mb(m.bytes)) : m.source;
    return '<div class="mod ' + (m.on ? "" : "off") + " " + (sel === m.id ? "sel" : "") + '" data-id="' + esc(m.id) + '">' +
      '<div class="head"><div class="ico"><svg width="20" height="20"><use href="#' + ICON[m.kind] + '"/></svg></div>' +
      '<div style="min-width:0"><h3>' + esc(m.name) + ' <span class="tag ' + m.kind + '">' + m.kind.toUpperCase() + "</span>" +
      (m.from_inbox && m.new ? ' <span class="tag new">FROM A CHAT</span>' : "") + "</h3>" +
      "<p>" + esc(m.desc || (m.kind === "live" ? "Service on this PC." : "Files from this PC.")) + "</p></div>" +
      (toggle ? '<label class="sw"><input type="checkbox" data-t="' + esc(m.id) + '"' + (m.on ? " checked" : "") + "><i></i></label>" : "") +
      '</div><div class="meta"><span style="display:flex;align-items:center;gap:7px">' + status(m) + '</span><span class="sp"></span>' +
      '<span style="font-family:var(--mono);color:var(--dim);overflow:hidden;text-overflow:ellipsis;white-space:nowrap;max-width:170px">' + esc(tail) + "</span></div></div>";
  }

  function render() {
    var live = S.modules.filter(function (m) { return m.kind === "live"; });
    var stat = S.modules.filter(function (m) { return m.kind === "static"; });
    var onc = function (a) { return a.filter(function (m) { return m.on; }).length; };
    $("#tLive").textContent = onc(live) + " / " + live.length;
    $("#tStatic").textContent = onc(stat) + " / " + stat.length;
    $("#tPend").textContent = S.pending.length;
    var waiting = S.modules.filter(function (m) { return m.from_inbox && m.new; }).length;
    $("#subBanner").hidden = !waiting;
    $("#subBannerText").textContent = waiting + (waiting === 1 ? " submission" : " submissions") + " from chats waiting for review. They are switched off until you switch them on.";
    var vw = 0; Object.keys(LV.stats || {}).forEach(function (k) { vw += LV.stats[k].viewers; });
    $("#tViewers").textContent = vw;
    var tn = LV.tunnel || {};
    $("#pillTunnel").textContent = tn.running ? (tn.connected ? "connected" : "starting") : (tn.installed ? "stopped" : "not set up");
    $("#dotTunnel").className = "dot " + (tn.connected ? "ok pulse" : (tn.running ? "warn" : ""));
    $("#dotLive").className = "dot " + (onc(live) ? "ok" : "");
    $("#modGrid").innerHTML = S.modules.map(function (m) { return card(m, true); }).join("") ||
      '<p class="empty">No modules yet. Click <b>Add module</b> to put something on your site.</p>';
    var on = S.modules.filter(function (m) { return m.on; });
    $("#dashGrid").innerHTML = on.map(function (m) { return card(m, false); }).join("") ||
      '<p class="empty">Nothing is switched on, so nothing is public.</p>';

    var p = S.pending, n = p.length;
    $("#app").classList.toggle("has-bar", n > 0 || !!busy);
    $("#barN").textContent = busy ? "Publishing" : n + (n === 1 ? " change" : " changes");
    if (!busy) $("#barN").nextElementSibling.textContent = " waiting to go on the site";
    $("#pubBtn").disabled = !!busy; $("#pubPrev").disabled = !!busy;
    $("#pubBtn").textContent = busy && !busy.preview ? "Publishing…" : "Publish";
    var nb = $("#navBadge"); nb.style.display = n ? "" : "none"; nb.textContent = n;
    $("#pubBtn2").disabled = !!busy;                       // always available: republishing rebuilds the site even when nothing changed
    $("#pubSummary").innerHTML = n ? p.map(function (x) {
      return { add: "+", update: "~", remove: "−" }[x.change] + " " + esc(x.name) + " (" + x.change + ")";
    }).join("<br>") : "Nothing waiting.";

    var st = S.settings || {};
    $("#domainLabel").textContent = st.domain || "set a domain in Settings";
    $("#pillSite").textContent = st.project || "not set";
    $("#dotSite").className = "dot " + (st.project ? "ok" : "");
    $("#pillDeploy").textContent = ago(S.last_deploy);
    $("#pubTarget").textContent = st.project ? "Pages project " + st.project + (st.site_dir ? " · site folder " + st.site_dir : "") : "Set the site folder and project in Settings.";
    $$("[data-id]").forEach(function (e) { e.classList.toggle("sel", e.dataset.id === sel); });
  }

  function renderPlan() {
    api("/api/publish/plan").then(function (pl) {
      var lines = [];
      if (pl.modular) {
        lines.push("SITE PAGES AND DESIGN");
        (pl.site_problems || []).forEach(function (e) { lines.push("  ✖ " + e.message + "   (fix this before publishing)"); });
        (pl.site_changes || []).forEach(function (c) { lines.push("  ~ " + c); });
        if (!(pl.site_changes || []).length && !(pl.site_problems || []).length) lines.push("  no changes since the last publish");
        lines.push("");
        lines.push("FILES");
      }
      lines = lines.concat(pl.files.length
        ? pl.files.map(function (f) { return (f.module ? f.module + "/" : "") + f.path + "  (" + mb(f.bytes) + ")"; })
        : [pl.modular ? "No extra files." : "Nothing yet."]);
      $("#pubFiles").textContent = lines.join("\n");
    }).catch(fail);
  }

  function renderLog() {
    api("/api/activity").then(function (a) {
      $("#logBox").innerHTML = a.rows.length ? a.rows.map(function (l) {
        return '<div class="l"><span>' + esc(l.t) + '</span><b class="' + esc(l.level) + '">' + esc(l.level.toUpperCase()) + '</b><span style="color:var(--text)">' + esc(l.msg) + "</span></div>";
      }).join("") : '<p class="empty">Nothing yet.</p>';
    }).catch(fail);
  }

  function fillSettings() {
    var st = S.settings || {};
    $("#setSite").value = st.site_dir || ""; $("#setProject").value = st.project || ""; $("#setDomain").value = st.domain || ""; $("#setLive").value = st.live_host || ""; $("#setTunnel").value = st.tunnel_name || ""; $("#setOrig").value = st.extra_origins || ""; $("#setCf").value = st.cloudflared_path || "";
  }

  function refresh(keepDrawer) {
    return Promise.all([api("/api/state"), api("/api/live").catch(function () { return LV; })]).then(function (r) {
      var before = seenNew;
      S = r[0]; LV = r[1];
      var fresh = S.modules.filter(function (m) { return m.from_inbox && m.new; });
      seenNew = {}; fresh.forEach(function (m) { seenNew[m.id] = 1; });
      if (before) fresh.filter(function (m) { return !before[m.id]; }).forEach(function (m) { toast("New submission from a chat: " + m.name); });
      render(); renderTunnel(); if (keepDrawer && sel) drawer(sel);
    }).catch(fail);
  }

  // ---------- tunnel ----------
  function renderTunnel() {
    var t = LV.tunnel || {};
    $("#tunState").textContent = t.running ? (t.connected ? "Connected" : "Starting…") : (t.installed ? "Stopped" : "cloudflared not found");
    $("#tunSub").textContent = t.running ? ((t.lines || []).slice(-1)[0] || "") : "Live modules only work while the tunnel is running.";
    $("#tunSteps").textContent = (t.setup || []).map(function (x, i) { return (i + 1) + ". " + x; }).join("\n");
  }

  // ---------- publishing ----------
  function pubMsg(title, text, bad) {
    $("#pubMsgRow").style.display = ""; $("#pubMsgTitle").textContent = title;
    $("#pubMsg").textContent = text || ""; $("#pubMsgTitle").style.color = bad ? "var(--red)" : "var(--green)";
  }
  function startPublish(preview) {
    api("/api/publish", "POST", { preview: !!preview }).then(function () {
      busy = { preview: !!preview }; $("#barN").nextElementSibling.textContent = "building and uploading"; render(); pollPublish();
    }).catch(fail);
  }
  function pollPublish() {
    api("/api/publish/status").then(function (st) {
      if (st.state === "running") {
        $("#barN").nextElementSibling.textContent = (st.step || "working").toLowerCase();
        return setTimeout(pollPublish, 800);
      }
      var wasPreview = busy && busy.preview; busy = null;
      if (st.state === "done") {
        toast(st.message); pubMsg(st.message, st.url ? "Address: " + st.url : "", false);
      } else if (st.state === "error") {
        toast("Publish failed"); pubMsg("Publish failed", st.message, true);
      }
      refresh().then(function () { if (view === "pub") renderPlan(); if (view === "logs") renderLog(); });
    }).catch(function (e) { busy = null; fail(e); render(); });
  }

  // ---------- drawer ----------
  function drawer(id) {
    var m = find(id); if (!m) return closeDrawer();
    sel = id;
    $("#dIco").innerHTML = '<svg width="20" height="20"><use href="#' + ICON[m.kind] + '"/></svg>';
    $("#dName").textContent = m.name;
    $("#dKind").innerHTML = '<span class="tag ' + m.kind + '">' + m.kind.toUpperCase() + "</span>";
    var h = '<div class="kv"><span>Source</span><span style="font-family:var(--mono);font-size:12px;word-break:break-all">' + esc(m.source) + "</span>" +
      "<span>Status</span><span style=\"display:flex;align-items:center;gap:7px\">" + status(m) + "</span></div>";
    if (m.kind === "static") {
      h += '<h2 class="h2" style="margin-top:22px">What gets uploaded</h2><div class="code">' + m.file_count + " files · " + mb(m.bytes) + "</div>";
      h += '<div class="field" style="margin-top:14px"><div><code>Show a card in the MORE list</code><small>Turn off for files that only support something else, like sprite sheets</small></div>' +
        '<label class="sw"><input type="checkbox" id="optListed"' + (m.listed === false ? "" : " checked") + "><i></i></label></div>";
      (m.problems || []).forEach(function (p) { h += '<div class="prob">⚠ ' + esc(p) + "</div>"; });
      h += '<p class="hint" style="margin-top:12px">Dotfiles, keys, env files, logs, backups and working notes are never included.</p>';
      if (m.from_inbox) {
        h += '<h2 class="h2" style="margin-top:22px">Review before switching on</h2>' +
          '<div id="reviewBox" class="hint">Loading preview…</div>' +
          (m.new ? '<button class="btn" data-act="ack" style="margin-top:10px">Mark as reviewed</button>' : "");
      }
    } else {
      h += '<h2 class="h2" style="margin-top:22px">What visitors can see</h2>' +
        '<div style="color:var(--muted);font-size:12.5px;margin-bottom:6px">Nothing is sent unless you allow it. Locked fields are never sent, whatever the service returns.</div>';
      h += (m.fields || []).map(function (f, i) {
        return f.locked
          ? '<div class="field blocked"><div><code>' + esc(f.name) + '</code><small>private</small></div><span class="lockbadge">🔒 LOCKED</span></div>'
          : '<div class="field"><div><code>' + esc(f.name) + '</code></div><label class="sw"><input type="checkbox" data-f="' + i + '"' + (f.allowed ? " checked" : "") + "><i></i></label></div>";
      }).join("") || '<p class="hint">No fields yet. Add the names of the values you want to share.</p>';
      h += '<div class="field add"><input type="text" id="newField" placeholder="field name, e.g. level"><button class="btn" data-act="addField">Add</button></div>' +
        '<button class="btn" data-act="discover" style="margin-top:8px">Discover fields from the service</button>';
      h += '<h2 class="h2" style="margin-top:22px">How the site shows it</h2>' +
        '<select id="optView" style="width:100%;padding:8px;background:#050806;color:var(--text);border:1px solid var(--line2);border-radius:8px"><option value="auto"' + ((m.view || "auto") === "auto" ? " selected" : "") + '>Generic cards</option><option value="pets"' + (m.view === "pets" ? " selected" : "") + '>Pets (own PETS tab, animated sprites)</option></select>' +
        '<h2 class="h2" style="margin-top:18px">Hide these (by name)</h2><input type="text" id="optHide" placeholder="e.g. Alpha, Beta" value="' + esc((m.hide || []).join(", ")) + '">' +
        '<div class="hint" style="margin-top:6px">Matching items are dropped here on your PC, so they never reach the site. The list itself is not published.</div>' +
        '<h2 class="h2" style="margin-top:18px">Refresh every (seconds)</h2><input type="text" id="optPoll" placeholder="3" value="' + esc(m.poll || "") + '" style="width:90px">';
      h += '<h2 class="h2" style="margin-top:22px">Viewer cap</h2><input class="range" id="capRange" type="range" min="5" max="200" value="' + m.viewer_cap + '">' +
        '<div style="color:var(--muted);font-size:12.5px" id="capLabel">' + m.viewer_cap + " viewers</div>";
      var allowed = (m.fields || []).filter(function (f) { return f.allowed; }).map(function (f) { return '  "' + esc(f.name) + '": …'; });
      h += '<h2 class="h2" style="margin-top:22px">Shape of what is sent</h2><div class="code">{\n' + (allowed.join(",\n") || "  (nothing)") + "\n}</div>";
    }
    $("#dBody").innerHTML = h;
    if (m.kind === "static" && m.from_inbox) loadReview(m.id);
    $("#drawer").classList.add("open");
    $$(".mod").forEach(function (e) { e.classList.toggle("sel", e.dataset.id === id); });
  }
  function loadReview(id) {
    api("/api/modules/" + id + "/preview").then(function (pv) {
      var box = $("#reviewBox"); if (!box || sel !== id) return;
      var text = pv.text == null ? "" : pv.text;
      box.className = ""; box.innerHTML = "";
      var info = document.createElement("div");
      info.className = "hint"; info.textContent = "Shows on the site as a " + pv.type + ". " + pv.files.length + (pv.more ? "+" : "") + " file(s):";
      box.appendChild(info);
      var list = document.createElement("div"); list.className = "code"; list.style.maxHeight = "110px";
      list.textContent = pv.files.map(function (f) { return f.path + "  (" + mb(f.bytes) + ")"; }).join("\n");
      box.appendChild(list);
      if (text) {
        var cap = document.createElement("div"); cap.className = "hint"; cap.style.marginTop = "10px"; cap.textContent = "Start of " + pv.entry + ":";
        var pre = document.createElement("div"); pre.className = "code"; pre.textContent = text;   // text only, never HTML
        box.appendChild(cap); box.appendChild(pre);
      }
    }).catch(function () { var b = $("#reviewBox"); if (b) b.textContent = "Could not load a preview."; });
  }
  function closeDrawer() { sel = null; $("#drawer").classList.remove("open"); $$(".mod").forEach(function (e) { e.classList.remove("sel"); }); }

  function saveFields() {
    var m = find(sel); if (!m) return;
    var boxes = $$("#dBody [data-f]");
    var fields = m.fields.map(function (f, i) {
      var b = boxes.filter(function (x) { return +x.dataset.f === i; })[0];
      return { name: f.name, allowed: b ? b.checked : false };
    });
    api("/api/modules/" + m.id + "/fields", "POST", { fields: fields }).then(function () { return refresh(true); }).catch(fail);
  }

  // ---------- navigation ----------
  function go(v) {
    view = v;
    $$(".view").forEach(function (e) { e.classList.toggle("on", e.id === "v-" + v); });
    $$("#nav button").forEach(function (b) { b.classList.toggle("on", b.dataset.v === v); });
    $("#title").textContent = { dash: "Dashboard", mods: "Modules", site: "Site", pub: "Publish", logs: "Activity", set: "Settings" }[v];
    closeDrawer();
    if (v === "site" && window.SiteUI) window.SiteUI.open();
    if (v === "pub") renderPlan();
    if (v === "logs") renderLog();
    if (v === "set") fillSettings();
  }

  // ---------- add-module wizard ----------
  function addOpen() { add = { step: 1, kind: null, source: "", name: "" }; $("#scrim").classList.add("open"); addStep(1); }
  function addClose() { $("#scrim").classList.remove("open"); }
  function addStep(n) {
    add.step = n;
    $("#addBack").style.display = n > 1 ? "" : "none";
    $("#addNext").textContent = n === 3 ? "Add (switched off)" : "Next";
    $("#addNext").disabled = n === 1 && !add.kind;
    var b = $("#addBody");
    if (n === 1) {
      b.innerHTML = '<div style="color:var(--muted);margin-bottom:14px">What do you want to put on the site?</div><div class="choices">' +
        '<div class="choice ' + (add.kind === "static" ? "sel" : "") + '" data-act="kind:static"><span class="tag static">STATIC</span><h4>A file or folder</h4><p>A game, images, a page, JSON. Uploaded to your host, so it stays up when this PC is off.</p></div>' +
        '<div class="choice ' + (add.kind === "live" ? "sel" : "") + '" data-act="kind:live"><span class="tag live">LIVE</span><h4>Something running on this PC</h4><p>A local web service. Reached through a tunnel, only while this PC is on.</p></div></div>';
    } else if (n === 2) {
      b.innerHTML = (add.kind === "static"
        ? '<label class="lb" style="margin-top:0">File or folder</label><input type="text" id="srcIn" placeholder="path to a file or folder"><div class="row" style="margin-top:10px"><button class="btn" data-act="browse">Browse…</button><span class="hint">Max 25 MB per file.</span></div>'
        : '<label class="lb" style="margin-top:0">Local address</label><input type="text" id="srcIn" placeholder="127.0.0.1:8787/overlay"><div class="hint" style="margin-top:8px">Only services on this PC are allowed.</div>') +
        '<label class="lb">Name on the site</label><input type="text" id="nameIn" placeholder="e.g. Screenshots"><div class="err" id="addErr"></div>';
      $("#srcIn").value = add.source || ""; $("#nameIn").value = add.name || "";
      $("#addNext").disabled = false;
    } else {
      b.innerHTML = '<div style="color:var(--muted);margin-bottom:12px">Review. It is added <b style="color:var(--text)">switched off</b>; nothing goes public until you flip it on.</div>' +
        '<div class="kv"><span>Kind</span><span><span class="tag ' + add.kind + '">' + add.kind.toUpperCase() + "</span></span><span>Source</span><span style=\"font-family:var(--mono);font-size:12px;word-break:break-all\">" + esc(add.source) + "</span><span>Name</span><span>" + esc(add.name) + "</span></div><div class=\"err\" id=\"addErr\"></div>";
    }
  }
  function addNext() {
    if (add.step === 2) {
      add.source = $("#srcIn").value.trim(); add.name = $("#nameIn").value.trim();
      if (!add.source || !add.name) { $("#addErr").textContent = "Fill in both fields."; return; }
      return addStep(3);
    }
    if (add.step === 3) {
      api("/api/modules", "POST", { kind: add.kind, name: add.name, source: add.source }).then(function () {
        addClose(); return refresh().then(function () { go("mods"); toast("Module added. Switch it on when you are ready."); });
      }).catch(function (e) { $("#addErr").textContent = e.message; });
      return;
    }
    addStep(add.step + 1);
  }

  // ---------- events ----------
  document.addEventListener("click", function (ev) {
    var nav = ev.target.closest("#nav button");
    if (nav) return go(nav.dataset.v);
    var a = ev.target.closest("[data-act]");
    if (a) {
      var act = a.dataset.act, arg = null;
      if (act.indexOf(":") > 0) { arg = act.split(":")[1]; act = act.split(":")[0]; }
      switch (act) {
        case "go": return go(arg);
        case "addOpen": return addOpen();
        case "addClose": return addClose();
        case "addBack": return addStep(add.step - 1);
        case "addNext": return addNext();
        case "kind": add.kind = arg; return addStep(1);
        case "browse":
          return api("/api/pick", "POST", { kind: add.kind === "static" ? "any" : "dir" }).then(function (r) { if (r.path) $("#srcIn").value = r.path; }).catch(fail);
        case "drawerClose": return closeDrawer();
        case "removeModule":
          if (sel && confirm("Remove this module? Its files on your PC are not touched.")) {
            return api("/api/modules/" + sel + "/remove", "POST", {}).then(function () { closeDrawer(); return refresh(); }).catch(fail);
          }
          return;
        case "addField": {
          var v = $("#newField").value.trim(), m = find(sel);
          if (!v || !m) return;
          var fields = (m.fields || []).map(function (f) { return { name: f.name, allowed: f.allowed }; });
          fields.push({ name: v, allowed: false });
          return api("/api/modules/" + m.id + "/fields", "POST", { fields: fields }).then(function () { return refresh(true); }).catch(fail);
        }
        case "tunnelStart": case "tunnelStop":
          return api("/api/tunnel/" + (act === "tunnelStart" ? "start" : "stop"), "POST", {})
            .then(function () { $("#tunErr").textContent = ""; return refresh(); })
            .catch(function (e) { $("#tunErr").textContent = e.message; });
        case "ack":
          return api("/api/modules/" + sel + "/ack", "POST", {}).then(function () { toast("Marked as reviewed"); return refresh(true); }).catch(fail);
        case "discover":
          return api("/api/modules/" + sel + "/discover", "POST", {}).then(function () { toast("Fields found. Allow the ones you want to share."); return refresh(true); }).catch(fail);
        case "quit":
          if (!confirm("Quit Halbach ConnectHub? Live modules and the tunnel go offline.")) return;
          return api("/api/quit", "POST", {}).then(function () {
            document.body.innerHTML = '<p style="font:16px system-ui;color:#7f9d8b;padding:40px">Halbach ConnectHub has stopped. You can close this window.</p>';
          }).catch(fail);
        case "wtChoose": return openThemeMaker({ current: savedWindowTheme(), onSave: function (t) { saveWindowTheme(t); },
                                                  onDone: function (t) { saveWindowTheme(t); toast("Window theme saved"); } });
        case "wtReset": window.HB.saveWindowTheme(null); return toast("Window theme reset");
        case "publish": return startPublish(false);
        case "previewPublish": return startPublish(true);
        case "saveSettings":
          return api("/api/settings", "POST", { site_dir: $("#setSite").value, project: $("#setProject").value, domain: $("#setDomain").value, live_host: $("#setLive").value, tunnel_name: $("#setTunnel").value, extra_origins: $("#setOrig").value, cloudflared_path: $("#setCf").value })
            .then(function () { $("#setErr").textContent = ""; toast("Settings saved"); return refresh(); })
            .catch(function (e) { $("#setErr").textContent = e.message; });
      }
      return;
    }
    if (ev.target.closest("label.sw")) return;
    var c = ev.target.closest(".mod[data-id]");
    if (c) drawer(c.dataset.id);
    else if (ev.target.id === "scrim") addClose();
  });

  document.addEventListener("change", function (ev) {
    var t = ev.target;
    if (t.dataset.t) {
      api("/api/modules/" + t.dataset.t + "/toggle", "POST", { on: t.checked }).then(function () { return refresh(true); })
        .then(function () { var m = find(t.dataset.t); if (m) toast(m.name + (m.on ? " is on" : " is off")); }).catch(fail);
    } else if (t.dataset.f !== undefined) saveFields();
    else if (t.id === "optView" || t.id === "optHide" || t.id === "optPoll" || t.id === "optListed") {
      var body = t.id === "optView" ? { view: t.value } : t.id === "optHide" ? { hide: t.value }
        : t.id === "optListed" ? { listed: t.checked } : { poll: t.value.trim() === "" ? 3 : t.value };
      api("/api/modules/" + sel + "/options", "POST", body).then(function () { toast("Saved. Publish to apply it to the site."); return refresh(true); }).catch(fail);
    } else if (t.id === "capRange") {
      $("#capLabel").textContent = t.value + " viewers";
      api("/api/modules/" + sel + "/cap", "POST", { cap: +t.value }).then(function () { return refresh(true); }).catch(fail);
    }
  });
  document.addEventListener("input", function (ev) {
    if (ev.target.id === "capRange") $("#capLabel").textContent = ev.target.value + " viewers";
  });

  // small generic dialog for the Site screens (title, a DOM body, optional confirm button that may return a promise of true/false)
  var dlgOk = null;
  function dialog(title, bodyNode, okLabel, okFn) {
    $("#dlgTitle").textContent = title;
    var b = $("#dlgBody"); b.textContent = ""; b.appendChild(bodyNode);
    var ok = $("#dlgOk"); ok.style.display = okLabel ? "" : "none"; ok.textContent = okLabel || "";
    dlgOk = okFn || null;
    $("#dlg").classList.add("open");
  }
  function closeDialog() { $("#dlg").classList.remove("open"); dlgOk = null; }
  document.addEventListener("click", function (ev) {
    if (ev.target.id === "dlgOk" && dlgOk) { Promise.resolve(dlgOk()).then(function (done) { if (done !== false) closeDialog(); }); }
    else if (ev.target.id === "dlgClose" || ev.target.id === "dlgCancel" || ev.target.id === "dlg") closeDialog();
  });
  // ---------- FunkThemes (Funk's theme engine and maker): window theme + the shared theme library ----------
  var WT_KEY = "halbach.windowtheme";
  var WT_MAP = { "--bg": "--bg", "--bg2": "--s1", "--card": "--s1", "--card2": "--s2", "--line": "--line", "--line2": "--line2",
                 "--text": "--text", "--muted": "--dim", "--dim": "--faint", "--green": "--brand", "--green2": "--brand-line" };
  function savedWindowTheme() { try { return JSON.parse(localStorage.getItem(WT_KEY) || "null"); } catch (e) { return null; } }
  function cacheWindowTheme(t) { try { if (t) localStorage.setItem(WT_KEY, JSON.stringify(t)); else localStorage.removeItem(WT_KEY); } catch (e) {} }
  function applyWindowTheme(t) {
    var FT = window.FunkTheme; if (!FT) return;
    var root = document.documentElement.style;
    if (!t) {                                                    // back to the built-in look
      var plain = { funkTheme: 1, name: "plain", style: "solid", colors: ["#0e0f12"], strength: 0, fx: null };
      FT.apply(plain);
      Object.keys(FT.derive(plain)).concat(Object.keys(WT_MAP)).forEach(function (k) { root.removeProperty(k); });
      document.body.classList.remove("ft-on");
      var c0 = document.getElementById("wtCredit"); if (c0) c0.style.display = "none";
      return;
    }
    var vars = FT.apply(t);
    Object.keys(WT_MAP).forEach(function (k) { if (vars[WT_MAP[k]]) root.setProperty(k, vars[WT_MAP[k]]); });
    document.body.classList.add("ft-on");
    var c1 = document.getElementById("wtCredit"); if (c1) c1.style.display = "";
  }
  function downloadTheme(t) {
    var a = document.createElement("a");
    a.href = URL.createObjectURL(new Blob([JSON.stringify(t)], { type: "application/json" }));
    a.download = String(t.name || "theme").replace(/[^\w\- ]+/g, "") + ".funktheme";
    document.body.appendChild(a); a.click(); a.remove();
    setTimeout(function () { URL.revokeObjectURL(a.href); }, 1000);
  }
  // Opens Funk's theme maker with the shared library. cfg: { current, onDone(theme) } (onDone only when something changed)
  function openThemeMaker(cfg) {
    if (!window.FunkTheme) return toast("The theme maker could not be loaded.");
    api("/api/funkthemes").then(function (r) {
      var start = JSON.stringify(cfg.current || null);
      window.FunkTheme.openEditor({
        current: cfg.current || window.FunkTheme.PRESETS[1], library: r.themes, brand: ["Halbach", "ConnectHub"],
        onChange: function (t) { applyWindowTheme(t); },
        onSave: function (t) {
          return api("/api/funkthemes/save", "POST", { theme: t }).then(function (x) {
            toast(cfg.onSave ? "Saved to the shared theme library, and applied" : "Saved to the shared theme library");
            if (cfg.onSave) cfg.onSave(t);
            return x.themes;
          }).catch(function (e) { toast(e.message); });
        },
        onDelete: function (t) { return api("/api/funkthemes/delete", "POST", { name: t.name }).then(function (x) { return x.themes; }).catch(function (e) { toast(e.message); }); },
        onExport: downloadTheme, onError: toast,
        onDone: function (t) {
          applyWindowTheme(savedWindowTheme());                  // the window goes back to its own theme after a preview
          if (JSON.stringify(t) !== start) cfg.onDone(t);
        },
      });
    }).catch(fail);
  }
  applyWindowTheme(savedWindowTheme());
  // The hub keeps the window theme too, so it survives a new port, a cleared browser profile or a second window.
  api("/api/windowtheme").then(function (r) {
    var local = savedWindowTheme();
    if (r.theme && JSON.stringify(r.theme) !== JSON.stringify(local)) { cacheWindowTheme(r.theme); applyWindowTheme(r.theme); }
    else if (!r.theme && local) api("/api/windowtheme", "POST", { theme: local }).catch(function () {});      // first run after the upgrade
  }).catch(function () {});
  function saveWindowTheme(t) {
    cacheWindowTheme(t); applyWindowTheme(t);
    api("/api/windowtheme", "POST", { theme: t || null }).catch(function (e) { toast(e.message); });
  }

  window.HB = { openThemeMaker: openThemeMaker, applyWindowTheme: applyWindowTheme, savedWindowTheme: savedWindowTheme,
                saveWindowTheme: saveWindowTheme,
                api: api, toast: toast, fail: fail, esc: esc, refreshSoon: function () { return refresh(); }, dialog: dialog, closeDialog: closeDialog };

  refresh();
  api("/api/publish/status").then(function (st) { if (st.state === "running") { busy = { preview: false }; render(); pollPublish(); } }).catch(function () {});
  // picks up edits inside watched folders; leaves the open panel alone so typing isn't lost
  setInterval(function () { if (!document.hidden) refresh(false); }, 5000);
})();
