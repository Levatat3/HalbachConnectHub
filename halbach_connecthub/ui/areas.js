"use strict";
// The Areas screen: lanes of the site that other people can send content into. Everything they send waits here for
// review. Text only (never HTML) is ever drawn from what a sender provided.
(function () {
  var HB = window.HB, h = HB.h, btn = HB.btn, sw = HB.sw;
  var DATA = null, openArea = null, openPanel = "review";
  var ALL_BLOCKS = ["hero", "text", "cards", "links", "image", "divider", "youtube", "twitch"];

  function root() { return document.getElementById("areasRoot"); }
  function when(ts) { return ts ? new Date(ts * 1000).toLocaleString() : "never"; }
  function mb(b) { return b >= 1048576 ? (b / 1048576).toFixed(1) + " MB" : Math.max(1, Math.round(b / 1024)) + " KB"; }
  function load() { return HB.api("/api/areas").then(function (d) { DATA = d; setBadge(d.pending); render(); }).catch(HB.fail); }
  function post(path, body, msg) { return HB.api(path, "POST", body || {}).then(function (r) { if (msg) HB.toast(msg); return load().then(function () { HB.refreshSoon(); return r; }); }); }
  function setBadge(n) { var b = document.getElementById("areaBadge"); if (!b) return; b.style.display = n ? "" : "none"; b.textContent = n; }

  function render() {
    var r = root(); if (!r || !DATA) return;
    r.textContent = "";
    r.appendChild(h("div", { class: "row", style: "margin-bottom:6px" },
      h("div", null, h("h2", { class: "h2", style: "margin:0 0 4px", text: "Areas" }),
        h("div", { style: "color:var(--muted);font-size:13px", text: "A page someone else can send content to, without any of your keys. Everything they send waits here for you to review." })),
      h("span", { style: "flex:1" }), btn("＋ New area", "primary", newAreaDialog)));
    if (!DATA.drop_url) r.appendChild(h("div", { class: "prob", text: "⚠ Set the live host in Settings and start the tunnel, so collaborators can reach your drop address." }));
    else r.appendChild(h("div", { class: "hint", style: "margin-bottom:12px", text: "Drop address for collaborators: " + DATA.drop_url + "  (reached through your tunnel, so your PC and the tunnel must be on while they send)." }));
    if (!DATA.areas.length) r.appendChild(h("p", { class: "empty", text: "No areas yet. Create one, then give its passphrase to the person who will fill it." }));
    DATA.areas.forEach(function (a) { r.appendChild(areaCard(a)); });
  }

  function chip(text, cls) { return h("span", { class: "tag " + (cls || "static"), style: "margin-left:8px", text: text }); }

  function areaCard(a) {
    var card = h("div", { class: "set", style: "max-width:980px;margin-bottom:16px" });
    var head = h("div", { class: "r" },
      h("div", { style: "flex:1;min-width:0" }, h("b", { text: a.name }), a.paused ? chip("PAUSED", "new") : null, !a.enabled ? chip("OFF", "new") : null,
        a.pending ? chip(a.pending + " WAITING", "live") : null,
        h("small", { text: "/" + a.slug + "/  ·  trust: " + a.trust + "  ·  " + (a.current ? "live version " + a.current : "nothing live yet") + "  ·  " + mb(a.bytes) + " used" })),
      btn(openArea === a.id ? "Close" : "Open", "", function () { openArea = openArea === a.id ? null : a.id; render(); }));
    card.appendChild(head);
    card.appendChild(h("div", { class: "r" }, h("div", null, h("b", { text: "Passphrase" }),
      h("small", { text: a.key.active ? "ends …" + a.key.hint + "  ·  last used " + when(a.key.last_used) : "revoked (nobody can send)" })), h("span", { style: "flex:1" }),
      btn("New passphrase", "", function () {
        if (confirm("Make a new passphrase for '" + a.name + "'? The old one stops working immediately.")) post("/api/areas/" + a.id + "/regenerate").then(function (r) { showKey(a.name, a.slug, r.key); });
      }),
      btn("Revoke", "ghost", function () { if (confirm("Revoke the passphrase? Nobody can send to '" + a.name + "' until you make a new one.")) post("/api/areas/" + a.id + "/revoke", {}, "Passphrase revoked."); })));
    if (openArea === a.id) card.appendChild(areaPanel(a));
    return card;
  }

  function areaPanel(a) {
    var box = h("div", { style: "padding:10px 14px 16px" });
    var tabs = h("div", { class: "subnav", role: "tablist", "aria-label": "Area panels" });
    [["review", "Review (" + a.pending + ")"], ["versions", "Versions"], ["settings", "Settings"], ["audit", "Activity"]].forEach(function (t) {
      var on = openPanel === t[0];
      tabs.appendChild(h("button", { class: "sub" + (on ? " on" : ""), type: "button", role: "tab", id: "areatab-" + a.id + "-" + t[0],
        "aria-selected": on ? "true" : "false", "aria-controls": "areapanel-" + a.id, text: t[1], on: { click: function () { openPanel = t[0]; render(); } } }));
    });
    box.appendChild(tabs);
    var panel = h("div", { id: "areapanel-" + a.id, role: "tabpanel", "aria-labelledby": "areatab-" + a.id + "-" + openPanel });
    ({ review: reviewPanel, versions: versionsPanel, settings: settingsPanel, audit: auditPanel }[openPanel] || reviewPanel)(panel, a);
    box.appendChild(panel);
    return box;
  }

  // ---------------------------------------------------------------- review
  function reviewPanel(box, a) {
    if (!a.submissions.length) return box.appendChild(h("p", { class: "empty", text: "Nothing has been sent yet." }));
    a.submissions.forEach(function (s) {
      var line = h("div", { class: "blockform", style: "margin:8px 0" });
      var stateCls = { pending: "live", approved: "static", rejected: "new", invalid: "new" }[s.state] || "static";
      line.appendChild(h("div", { class: "row" }, h("b", { text: (s.title || "Submission") + "  " }), chip(s.state.toUpperCase(), stateCls),
        h("span", { style: "flex:1" }), h("small", { class: "hint", text: when(s.received) + "  ·  " + mb(s.size) })));
      if (s.state === "invalid") (s.reasons || []).forEach(function (r) { line.appendChild(h("div", { class: "errline", text: "✖ " + r })); });
      else {
        line.appendChild(h("div", { class: "hint", text: s.pages + " page(s), " + s.blocks + " block(s), " + s.files + " file(s)" + (s.version ? "  ·  became " + s.version : "") }));
        var c = s.changes;
        if (c) {
          var parts = [];
          if (c.first) parts.push("first content for this area");
          ["pages_added", "pages_removed", "files_added", "files_changed", "files_removed"].forEach(function (k) { if (c[k] && c[k].length) parts.push(k.replace("_", " ") + ": " + c[k].slice(0, 6).join(", ") + (c[k].length > 6 ? "…" : "")); });
          line.appendChild(h("div", { class: "hint", style: "white-space:pre-line", text: parts.join("\n") || "no changes from what is live" }));
        }
        (s.warnings || []).forEach(function (w) { line.appendChild(h("div", { class: "prob", text: "⚠ " + w })); });
        if (s.note) line.appendChild(h("div", { class: "hint", text: "Note: " + s.note }));
      }
      if (s.state === "pending") {
        line.appendChild(h("div", { class: "row", style: "margin-top:10px" },
          btn("Preview it", "", function () {
            HB.api("/api/site/preview", "POST", { area: a.id, sid: s.id }).then(function (r) {
              if (!r.ok) return HB.toast("The site has problems: " + (r.errors[0] || {}).message);
              window.open("/preview/" + a.slug + "/", "_blank", "noopener");
            }).catch(HB.fail);
          }),
          h("span", { style: "flex:1" }),
          btn("Reject…", "", function () {
            var note = prompt("A note for the sender (they can read it):", "");
            if (note !== null) post("/api/areas/" + a.id + "/reject", { sid: s.id, note: note }, "Rejected.");
          }),
          btn("Approve", "primary", function () {
            if (confirm("Approve this? It becomes the area's live content at your next Publish.")) post("/api/areas/" + a.id + "/approve", { sid: s.id }, "Approved. Publish to put it on the site.");
          })));
      }
      box.appendChild(line);
    });
  }

  function versionsPanel(box, a) {
    if (!a.versions.length) return box.appendChild(h("p", { class: "empty", text: "No approved versions yet." }));
    a.versions.slice().reverse().forEach(function (v) {
      box.appendChild(h("div", { class: "r" }, h("div", { style: "flex:1" }, h("b", { text: v }), v === a.current ? chip("LIVE", "static") : null),
        v === a.current ? null : btn("Make this the live one", "", function () { if (confirm("Roll back to " + v + "?")) post("/api/areas/" + a.id + "/rollback", { version: v }, "Rolled back. Publish to apply it."); })));
    });
  }

  function auditPanel(box, a) {
    var out = h("div", { class: "log" });
    box.appendChild(out);
    HB.api("/api/areas/" + a.id + "/audit").then(function (r) {
      if (!r.rows.length) out.appendChild(h("p", { class: "empty", text: "No activity yet." }));
      r.rows.forEach(function (x) { out.appendChild(h("div", { class: "l" }, h("span", { text: new Date(x.t * 1000).toLocaleTimeString() }), h("b", { class: /fail|reject|error|timeout|incomplete/.test(x.what) ? "warn" : "info", text: x.what }), h("span", { style: "color:var(--text)", text: x.who + (x.detail ? "  ·  " + x.detail : "") + (x.size ? "  ·  " + mb(x.size) : "") }))); });
    }).catch(HB.fail);
  }

  // ---------------------------------------------------------------- settings
  function settingsPanel(box, a) {
    var name = h("input", { type: "text", value: a.name });
    var trust = h("select", null, h("option", { value: "review", text: "Review every submission (recommended)" }), h("option", { value: "trusted", text: "Trusted: approve valid submissions automatically" }));
    trust.value = a.trust;
    var en = sw(a.enabled, function () {}), pa = sw(a.paused, function () {}), nav = sw(a.nav, function () {});
    var blockBoxes = ALL_BLOCKS.map(function (b) { var i = h("input", { type: "checkbox", checked: a.blocks.indexOf(b) >= 0 }); return [b, i]; });
    var lim = {}, limRow = h("div", { class: "row", style: "flex-wrap:wrap" });
    [["max_file_mb", "Biggest file (MB)"], ["max_bundle_mb", "Biggest upload (MB)"], ["max_total_mb", "Total for the area (MB)"], ["max_files", "Files per upload"]].forEach(function (k) {
      lim[k[0]] = h("input", { type: "number", value: a.limits[k[0]], style: "width:90px" }); limRow.appendChild(h("label", { class: "hint", style: "margin-right:10px" }, k[1] + " ", lim[k[0]]));
    });
    var err = h("div", { class: "err" });
    box.appendChild(h("div", { class: "set", style: "max-width:860px" },
      row("Name", "", name), row("On", "Off hides the area's page and refuses uploads.", en), row("Paused", "Stops new submissions at once; the live page stays up.", pa),
      row("Show in the menu", "", nav), row("Review", "Even when trusted, every bundle is still checked and every approval is a saved version.", trust),
      h("div", { class: "r" }, h("div", { style: "width:100%" }, h("b", { text: "Blocks they may use" }), h("div", { class: "row", style: "flex-wrap:wrap;margin-top:6px" },
        blockBoxes.map(function (p) { return h("label", { class: "hint", style: "margin-right:12px" }, p[1], " " + p[0]); })))),
      h("div", { class: "r" }, h("div", { style: "width:100%" }, h("b", { text: "Limits" }), limRow)),
      h("div", { class: "r" }, err, h("span", { style: "flex:1" }), btn("Delete area…", "ghost", function () {
        if (confirm("Delete '" + a.name + "' and everything it received? Its live page disappears at your next Publish.")) post("/api/areas/" + a.id + "/delete", {}, "Area deleted.").then(function () { openArea = null; render(); });
      }), btn("Save", "primary", function () {
        var limits = {}; Object.keys(lim).forEach(function (k) { limits[k] = parseInt(lim[k].value, 10); });
        post("/api/areas/" + a.id + "/update", { name: name.value, enabled: en.firstChild.checked, paused: pa.firstChild.checked, nav: nav.firstChild.checked, trust: trust.value,
          blocks: blockBoxes.filter(function (p) { return p[1].checked; }).map(function (p) { return p[0]; }), limits: limits }, "Area saved.").catch(function (e) { err.textContent = e.message; });
      }))));
  }
  function row(title, small, control) { return h("div", { class: "r" }, h("div", null, h("b", { text: title }), small ? h("small", { text: small }) : null), h("span", { style: "flex:1" }), control); }

  // ---------------------------------------------------------------- creating one and showing its passphrase (once)
  function showKey(name, slug, key) {
    var drop = (DATA && DATA.drop_url) ? DATA.drop_url.replace(/\/drop\/$/, "") : "https://live.example.com";
    var box = h("div", null,
      h("p", { text: "This is the passphrase for “" + name + "”. It is shown only now; Halbach keeps just a fingerprint of it. Send it to the person privately." }),
      h("div", { class: "code", style: "font-size:15px;user-select:all", text: key }),
      h("div", { class: "row", style: "margin-top:10px" }, btn("Copy", "", function () { try { navigator.clipboard.writeText(key); HB.toast("Copied"); } catch (e) { HB.toast("Select it and copy"); } })),
      h("p", { class: "hint", style: "margin-top:14px", text: "What they run on their PC (any Halbach):" }),
      h("div", { class: "code", style: "user-select:all", text: "halbach area init my-area\nhalbach area connect --url " + drop + " --area " + slug + "\nhalbach area send my-area --wait 60" }));
    HB.dialog("Passphrase", box, null);
  }

  function newAreaDialog() {
    var name = h("input", { type: "text", placeholder: "e.g. Funk's Workshop" }), slug = h("input", { type: "text", placeholder: "address, e.g. funkworkshop" });
    var err = h("div", { class: "err" }), touched = false;
    slug.addEventListener("input", function () { touched = true; });
    name.addEventListener("input", function () { if (!touched) slug.value = name.value.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "").slice(0, 40); });
    HB.dialog("New area", h("div", null, h("label", { class: "lb", style: "margin-top:0", text: "Name" }), name, h("label", { class: "lb", text: "Address on your site" }),
      h("div", { class: "row" }, h("span", { class: "hint", text: "/" }), slug), err,
      h("p", { class: "hint", text: "Every submission waits for your review. You can change the limits and the blocks they may use afterwards." })),
      "Create", function () {
        return HB.api("/api/areas/create", "POST", { name: name.value, slug: slug.value }).then(function (r) {
          return load().then(function () { openArea = r.area.id; HB.closeDialog(); showKey(r.area.name, r.area.slug, r.key); render(); return false; });
        }).catch(function (e) { err.textContent = e.message; return false; });
      });
  }

  window.AreasUI = { open: load };
  setInterval(function () { if (!document.hidden) HB.api("/api/areas").then(function (d) { DATA = d; setBadge(d.pending); }).catch(function () {}); }, 8000);
  load();
})();
