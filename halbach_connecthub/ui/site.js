"use strict";
// The Site screens of the Halbach window: pages, the page editor (forms generated from each block's manifest),
// design, menu and redirects, settings, history, and a live preview. Everything shown comes from /api/site;
// every change goes through /api/site/<action>, which refuses bad input and leaves the site as it was.
(function () {
  var HB = window.HB;
  var SS = null;                 // last /api/site answer
  var sub = "overview";          // which sub-screen is showing
  var curPage = null;            // page id open in the editor
  var curBlock = null;           // block id open in the editor ("new:<type>" while adding)
  var previewWidth = 1100;
  var FUNK_DEFAULT = { funkTheme: 1, name: "Funk", style: "gradient", colors: ["#462a50", "#2f0a66"], angle: 135, strength: 0.45, accent: null, blur: 0, fx: "ascii", fxColor: "#c604fb", fxSpeed: 2.6, fxAmount: 0.88 };

  // ---------------------------------------------------------------- tiny DOM helpers (text only, never HTML)
  function h(tag, attrs) {
    var e = document.createElement(tag);
    Object.keys(attrs || {}).forEach(function (k) {
      var v = attrs[k];
      if (v == null || v === false) return;
      if (k === "class") e.className = v;
      else if (k === "text") e.textContent = v;
      else if (k === "on") Object.keys(v).forEach(function (ev) { e.addEventListener(ev, v[ev]); });
      else if (k === "value") e.value = v;
      else if (k === "checked") e.checked = !!v;
      else e.setAttribute(k, v === true ? "" : v);
    });
    for (var i = 2; i < arguments.length; i++) {
      var c = arguments[i];
      if (c == null || c === false) continue;
      if (Array.isArray(c)) c.forEach(function (x) { if (x) e.appendChild(x); });
      else e.appendChild(typeof c === "string" ? document.createTextNode(c) : c);
    }
    return e;
  }
  function btn(label, cls, fn, extra) { return h("button", Object.assign({ class: "btn " + (cls || ""), type: "button", text: label, on: { click: fn } }, extra || {})); }
  function sw(checked, fn) {
    var i = h("input", { type: "checkbox", checked: checked, on: { change: function () { fn(i.checked); } } });
    return h("label", { class: "sw" }, i, h("i"));
  }
  function row(title, small, control) {
    return h("div", { class: "r" }, h("div", null, h("b", { text: title }), small ? h("small", { text: small }) : null), h("span", { style: "flex:1" }), control);
  }
  function section(title, intro) { return [h("h2", { class: "h2", style: "margin-top:22px", text: title }), intro ? h("p", { class: "hint", style: "margin:-4px 0 10px", text: intro }) : null]; }
  function pageById(id) { return SS.model.pages.filter(function (p) { return p.id === id; })[0]; }
  function blockSpec(type) { return SS.catalog.block.filter(function (b) { return b.id === type; })[0]; }
  function root() { return document.getElementById("siteRoot"); }
  function pathOf(p) { return p.slug === "" ? "/" : "/" + p.slug + "/"; }

  // ---------------------------------------------------------------- talking to the hub
  function load() {
    return HB.api("/api/site").then(function (d) { SS = d; render(); }).catch(HB.fail);
  }
  function act(name, args, msg, done) {
    return HB.api("/api/site/" + name, "POST", args || {}).then(function (chk) {
      if (msg) HB.toast(msg);
      return load().then(function () { HB.refreshSoon(); if (done) done(chk); return chk; });
    }).catch(function (e) { HB.toast(e.message || "That change was refused"); throw e; });
  }
  function showErr(box, e) { box.textContent = e && e.message ? e.message : String(e); }

  // ---------------------------------------------------------------- frame
  var SUBS = [["overview", "Overview"], ["pages", "Pages"], ["design", "Design"], ["menu", "Menu & redirects"], ["linked", "Linked sites"],
              ["settings", "Site settings"], ["history", "History"], ["preview", "Preview"]];
  function render() {
    var r = root(); if (!r || !SS) return;
    r.textContent = "";
    var bar = h("div", { class: "subnav" });
    SUBS.forEach(function (s) {
      var label = s[1], extra = s[0] === "overview" && SS.errors.length ? " (" + SS.errors.length + ")" : "";
      bar.appendChild(h("button", { class: "sub" + (sub === s[0] || (s[0] === "pages" && sub === "page") ? " on" : ""), type: "button", text: label + extra,
        on: { click: function () { sub = s[0]; curBlock = null; render(); } } }));
    });
    r.appendChild(bar);
    var body = h("div", { class: "subbody" });
    r.appendChild(body);
    ({ overview: overview, pages: pages, page: pageEditor, design: design, menu: menu, linked: linkedSites, settings: siteSettings, history: history, preview: preview }[sub] || overview)(body);
  }

  // ---------------------------------------------------------------- overview
  function overview(b) {
    var modular = SS.mode === "modular";
    var fresh = !SS.exists || !SS.model.pages.length;
    b.appendChild(h("div", { class: "set", style: "max-width:860px" },
      row(modular ? "Modular site is ON" : "Classic site is ON",
        modular ? "Publishing builds your pages from this window." : "Publishing uses your site folder exactly as before. Nothing here changes your live site until you switch.",
        modular ? btn("Switch back to classic", "", function () {
          if (confirm("Publish from your site folder again? Your modular pages stay saved here.")) HB.api("/api/settings", "POST", { site_mode: "classic" }).then(function () { HB.refreshSoon(); return load(); }).catch(HB.fail);
        }) : btn("Use the modular site", "primary", function () {
          if (fresh) return HB.toast("Start from your current site or a template first (below).");
          if (SS.errors.length) return HB.toast("Fix the problems listed below first.");
          if (confirm("Publish from the modular site? Preview it first. You can switch back any time.")) HB.api("/api/settings", "POST", { site_mode: "modular" }).then(function () { HB.refreshSoon(); return load(); }).catch(HB.fail);
        }))));

    if (fresh || sub === "overview") {
      b.appendChild(h("div", { style: "margin-top:20px" }, section("Start from", fresh ? "Nothing here yet. Pick a starting point; you can change everything afterwards." : "Replacing the site starts over (the old version stays in History).")));
      var cards = h("div", { class: "choices", style: "grid-template-columns:repeat(auto-fill,minmax(230px,1fr));max-width:860px" });
      cards.appendChild(h("div", { class: "choice", on: { click: function () {
        if (!SS.has_site_dir) return HB.toast("Set the site folder in Settings first.");
        if (fresh || confirm("Replace the current modular site with your classic site?")) act("import.classic", {}, "Imported your current site. Preview it before publishing.");
      } } }, h("span", { class: "tag static", text: "IMPORT" }), h("h4", { text: "My current site" }), h("p", { text: "Reads the site folder (the one visitors see today) into pages. Nothing in the folder is changed." })));
      SS.catalog.template.forEach(function (t) {
        cards.appendChild(h("div", { class: "choice", on: { click: function () {
          if (fresh || confirm("Replace the current modular site with this template?")) act("template.load", { id: t.id }, "Template loaded.");
        } } }, h("span", { class: "tag live", text: "TEMPLATE" }), h("h4", { text: t.name }), h("p", { text: t.description })));
      });
      b.appendChild(cards);
    }
    if (SS.import_notes && SS.import_notes.length) {
      SS.import_notes.forEach(function (n) { b.appendChild(h("div", { class: "prob", text: "⚠ " + n })); });
    }
    if (!fresh) {
      var out = h("div", { style: "margin-top:20px;max-width:860px" });
      out.appendChild(h("h2", { class: "h2", text: "Checks" }));
      if (!SS.errors.length && !SS.warnings.length) out.appendChild(h("div", { class: "okline", text: "✔ Everything checks out. Preview it, then publish." }));
      SS.errors.forEach(function (e) { out.appendChild(h("div", { class: "errline", text: "✖ " + e.message + "  (" + e.path + ")" })); });
      SS.warnings.forEach(function (e) { out.appendChild(h("div", { class: e.level === "info" ? "hint" : "prob", style: e.level === "info" ? "padding:4px 0" : null, text: (e.level === "info" ? "ℹ " : "⚠ ") + e.message })); });
      b.appendChild(out);
    }
  }

  // ---------------------------------------------------------------- pages
  function pages(b) {
    var d = SS.model;
    b.appendChild(h("div", { class: "row", style: "margin-bottom:14px" },
      h("div", null, h("h2", { class: "h2", style: "margin:0 0 4px", text: "Pages" }), h("div", { class: "hint", text: "Each page gets its own address on your site. Switch a page off to take it down without deleting it." })),
      h("span", { style: "flex:1" }), btn("＋ Add page", "primary", addPageDialog)));
    var tbl = h("div", { class: "set", style: "max-width:980px;padding:0 6px" });
    d.pages.forEach(function (p, i) {
      var line = h("div", { class: "r pagerow" + (p.enabled ? "" : " off") },
        h("div", { style: "min-width:0;cursor:pointer;flex:1", on: { click: function () { curPage = p.id; sub = "page"; render(); } } },
          h("b", { text: p.label }), h("span", { class: "tag " + (p.draft ? "new" : "static"), style: "margin-left:8px", text: p.draft ? "DRAFT" : (p.listed ? "PUBLIC" : "UNLISTED") }),
          h("small", { text: pathOf(p) + "  ·  " + p.blocks.length + " block" + (p.blocks.length === 1 ? "" : "s") + (p.nav ? "  ·  in the menu" : "") })),
        btn("▲", "ghost", function () { act("page.move", { id: p.id, delta: -1 }); }, i === 0 ? { disabled: "" } : null),
        btn("▼", "ghost", function () { act("page.move", { id: p.id, delta: 1 }); }, i === d.pages.length - 1 ? { disabled: "" } : null),
        btn("Edit", "", function () { curPage = p.id; sub = "page"; render(); }),
        sw(p.enabled, function (on) { act("page.update", { id: p.id, enabled: on }).catch(load); }));
      tbl.appendChild(line);
    });
    b.appendChild(tbl);
  }

  function addPageDialog() {
    var label = h("input", { type: "text", placeholder: "e.g. Funk's Workshop" }), slug = h("input", { type: "text", placeholder: "address, e.g. funkworkshop" });
    var err = h("div", { class: "err" }), touched = false;
    slug.addEventListener("input", function () { touched = true; });
    label.addEventListener("input", function () { if (!touched) slug.value = label.value.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "").slice(0, 40); });
    HB.dialog("Add a page", h("div", null, h("label", { class: "lb", style: "margin-top:0", text: "Name" }), label,
      h("label", { class: "lb", text: "Address on your site" }), h("div", { class: "row" }, h("span", { class: "hint", text: "levmorium.com/" }), slug), err,
      h("p", { class: "hint", text: "The page starts empty and as a draft-free page. Add blocks to it next." })),
      "Add page", function () {
        return act("page.add", { label: label.value, slug: slug.value }, "Page added.", function () {
          var np = SS.model.pages[SS.model.pages.length - 1]; curPage = np.id; sub = "page"; render();
        }).then(function () { return true; }).catch(function (e) { showErr(err, e); return false; });
      });
  }

  // ---------------------------------------------------------------- page editor
  function pageEditor(b) {
    var p = curPage && pageById(curPage);
    if (!p) { sub = "pages"; return pages(b); }
    b.appendChild(h("div", { class: "row", style: "margin-bottom:14px" }, btn("← Pages", "ghost", function () { sub = "pages"; curBlock = null; render(); }),
      h("h2", { class: "h2", style: "margin:0", text: p.label + "   " + pathOf(p) }), h("span", { style: "flex:1" }),
      btn("Duplicate", "", function () { act("page.duplicate", { id: p.id }, "Duplicated as a hidden draft."); }),
      p.slug === "" ? null : btn("Delete page", "", function () {
        if (confirm("Delete the page '" + p.label + "'? It stays in History, so it can be brought back.")) act("page.delete", { id: p.id }, "Page deleted.", function () { sub = "pages"; render(); });
      })));

    // page settings
    var err = h("div", { class: "err" });
    var f = { label: h("input", { type: "text", value: p.label }), slug: h("input", { type: "text", value: p.slug, placeholder: "(home)" }),
              title: h("input", { type: "text", value: p.title }), description: h("textarea", { rows: 2, text: p.description })};
    f.description.value = p.description;
    var theme = h("select", null, h("option", { value: "", text: "Same as the site" }), SS.catalog.theme.map(function (t) { return h("option", { value: t.id, text: t.name }); }));
    theme.value = p.theme || "";
    var navChk = sw(p.nav, function () {}), listChk = sw(p.listed, function () {}), draftChk = sw(p.draft, function () {});
    var creditChk = sw(p.credit !== false, function () {});
    var navIn = navChk.firstChild, listIn = listChk.firstChild, draftIn = draftChk.firstChild, creditIn = creditChk.firstChild;
    b.appendChild(h("div", { class: "set", style: "max-width:860px" },
      row("Name in the menu", "", f.label),
      row("Address", p.slug === "" ? "This is the home page." : "Letters, digits and dashes.", f.slug),
      row("Title", "Browser tab and search results.", f.title),
      row("Description", "One sentence for search results and link previews.", f.description),
      row("Show in the menu", "", navChk),
      row("Listed in search and the sitemap", "Switch off for a page only people with the link should find.", listChk),
      row("Draft", "Drafts show in Preview but are never published.", draftChk),
      row("Theme for this page", "Optional.", theme),
      row("Show “Powered by FunkThemes”", "A credit line in the footer, shown only on pages that use the Funk theme. Funk built the theme engine, so please leave it on.", creditChk),
      h("div", { class: "r" }, err, h("span", { style: "flex:1" }), btn("Save page settings", "primary", function () {
        act("page.update", { id: p.id, label: f.label.value, slug: f.slug.value, title: f.title.value, description: f.description.value,
                             nav: navIn.checked, listed: listIn.checked, draft: draftIn.checked, credit: creditIn.checked, theme: theme.value || null }, "Page saved.")
          .then(function () { curPage = p.id; }).catch(function (e) { showErr(err, e); });
      }))));

    // blocks
    b.appendChild(h("div", { class: "row", style: "margin:26px 0 12px" }, h("h2", { class: "h2", style: "margin:0", text: "Blocks on this page" }), h("span", { style: "flex:1" }),
      btn("＋ Add block", "primary", function () { blockPicker(p); })));
    var list = h("div", { class: "set", style: "max-width:980px;padding:0 6px" });
    if (!p.blocks.length) list.appendChild(h("p", { class: "empty", text: "No blocks yet. Add one to put something on this page." }));
    p.blocks.forEach(function (blk, i) {
      var spec = blockSpec(blk.type) || { label: blk.type };
      list.appendChild(h("div", { class: "r pagerow" + (blk.enabled === false ? " off" : "") },
        h("div", { style: "flex:1;min-width:0;cursor:pointer", on: { click: function () { curBlock = curBlock === blk.id ? null : blk.id; render(); } } },
          h("b", { text: spec.label || blk.type }), h("small", { text: summarize(blk) })),
        btn("▲", "ghost", function () { act("block.move", { page: p.id, id: blk.id, delta: -1 }); }, i === 0 ? { disabled: "" } : null),
        btn("▼", "ghost", function () { act("block.move", { page: p.id, id: blk.id, delta: 1 }); }, i === p.blocks.length - 1 ? { disabled: "" } : null),
        btn("Edit", "", function () { curBlock = curBlock === blk.id ? null : blk.id; render(); }),
        btn("Copy", "ghost", function () { act("block.duplicate", { page: p.id, id: blk.id }, "Block copied."); }),
        sw(blk.enabled !== false, function (on) { act("block.update", { page: p.id, id: blk.id, enabled: on }).catch(load); }),
        btn("✕", "ghost", function () { if (confirm("Remove this block from the page?")) act("block.delete", { page: p.id, id: blk.id }, "Block removed."); })));
      if (curBlock === blk.id) list.appendChild(h("div", { class: "blockform" }, blockForm(p, blk, spec)));
    });
    if (curBlock && curBlock.indexOf("new:") === 0) {
      var spec2 = blockSpec(curBlock.slice(4));
      if (spec2) list.appendChild(h("div", { class: "blockform" }, h("b", { text: "New: " + spec2.label }), blockForm(p, { id: null, type: spec2.id, props: {} }, spec2)));
    }
    b.appendChild(list);
  }

  function summarize(blk) {
    var pr = blk.props || {}, bits = [];
    ["heading", "channel", "user", "module", "id", "url", "view", "swf"].forEach(function (k) { if (pr[k]) bits.push(k + ": " + pr[k]); });
    if (pr.markdown) bits.push(String(pr.markdown).replace(/\s+/g, " ").slice(0, 60));
    if (pr.items) bits.push(pr.items.length + " item" + (pr.items.length === 1 ? "" : "s"));
    return bits.join("  ·  ") || "(no settings)";
  }

  function blockPicker(p) {
    var cats = {};
    SS.catalog.block.filter(function (x) { return x.ok && x.enabled && x.status !== "planned"; }).forEach(function (x) { (cats[x.category || "other"] = cats[x.category || "other"] || []).push(x); });
    var body = h("div");
    Object.keys(cats).sort().forEach(function (c) {
      body.appendChild(h("h2", { class: "h2", style: "margin:14px 0 8px", text: c }));
      var grid = h("div", { class: "choices", style: "grid-template-columns:1fr 1fr" });
      cats[c].forEach(function (x) {
        grid.appendChild(h("div", { class: "choice", on: { click: function () { HB.closeDialog(); curBlock = "new:" + x.id; sub = "page"; render(); } } },
          h("h4", { style: "margin:0 0 4px", text: x.label }), h("p", { text: x.description })));
      });
      body.appendChild(grid);
    });
    HB.dialog("Add a block", body, null);
  }

  // ---------------------------------------------------------------- forms generated from a block's manifest
  function modulesFor(field) {
    var kinds = field.modules || [];
    return SS.modules.filter(function (m) {
      if (!m.on) return false;
      if (!kinds.length) return true;
      return kinds.indexOf(m.kind) >= 0 || kinds.indexOf(m.type) >= 0;
    });
  }
  function control(field, value) {
    // returns {el, get()} for one field
    var t = field.type, v = value == null ? (field["default"] == null ? "" : field["default"]) : value;
    if (t === "bool") { var s = sw(!!v, function () {}); return { el: s, get: function () { return s.firstChild.checked; } }; }
    if (t === "longtext" || t === "markdown") { var ta = h("textarea", { rows: t === "markdown" ? 9 : 4, class: t === "markdown" ? "mono" : "" }); ta.value = v; return { el: ta, get: function () { return ta.value; } }; }
    if (t === "list-text") { var tl = h("textarea", { rows: 4, placeholder: "one per line" }); tl.value = (v || []).join ? (v || []).join("\n") : ""; return { el: tl, get: function () { return tl.value.split("\n").map(function (x) { return x.trim(); }).filter(Boolean); } }; }
    if (t === "number") { var n = h("input", { type: "number", min: field.min, max: field.max }); n.value = v; return { el: n, get: function () { return n.value === "" ? "" : Number(n.value); } }; }
    if (t === "select") {
      var sel = h("select", null, (field.options || []).map(function (o) { return h("option", { value: o, text: o }); })); sel.value = v; return { el: sel, get: function () { return sel.value; } };
    }
    if (t === "module") {
      var ms = modulesFor(field), mo = h("select", null, h("option", { value: "", text: ms.length ? "— choose —" : "(no matching module is switched on)" }), ms.map(function (m) { return h("option", { value: m.id, text: m.name + " (" + (m.type || m.kind) + ")" }); }));
      mo.value = v; return { el: mo, get: function () { return mo.value; } };
    }
    if (t === "page") {
      var pg = h("select", null, h("option", { value: "", text: "— choose —" }), SS.model.pages.map(function (p) { return h("option", { value: p.id, text: p.label }); })); pg.value = v; return { el: pg, get: function () { return pg.value; } };
    }
    if (t === "color") {
      var c = h("input", { type: "text", placeholder: "#39ff7a" }); c.value = v; return { el: c, get: function () { return c.value; } };
    }
    if (t === "links") return repeater(v, [["label", "Label"], ["url", "Address"]], function (it) { return { label: it.label || "", url: it.url || "" }; });
    if (t === "cards") return cardsControl(v);
    var inp = h("input", { type: "text", placeholder: t === "url" ? "https://… or /path" : "" }); inp.value = v; return { el: inp, get: function () { return inp.value; } };
  }
  function repeater(items, cols, norm) {
    var box = h("div", { class: "rep" }), rows = [];
    function addRow(it) {
      it = norm(it || {});
      var ins = cols.map(function (c) { var i = h("input", { type: "text", placeholder: c[1] }); i.value = it[c[0]]; return i; });
      var line = h("div", { class: "reprow" }, ins, btn("✕", "ghost", function () { rows.splice(rows.indexOf(rec), 1); line.remove(); }));
      var rec = { get: function () { var o = {}; cols.forEach(function (c, i) { o[c[0]] = ins[i].value; }); return o; } };
      rows.push(rec); box.appendChild(line);
    }
    (items || []).forEach(addRow);
    var wrap = h("div", null, box, btn("＋ Add", "", function () { addRow({}); }));
    return { el: wrap, get: function () { return rows.map(function (r) { return r.get(); }); } };
  }
  function cardsControl(items) {
    var box = h("div", { class: "rep" }), rows = [];
    function addRow(it) {
      it = it || {};
      var title = h("input", { type: "text", placeholder: "Title" }), text = h("input", { type: "text", placeholder: "Text" }), url = h("input", { type: "text", placeholder: "https://… or /path" });
      title.value = it.title || ""; text.value = it.text || ""; url.value = it.url || "";
      var kind = h("select", null, h("option", { value: "", text: "no link" }), SS.model.pages.map(function (p) { return h("option", { value: "page:" + p.id, text: "page: " + p.label }); }), h("option", { value: "url", text: "an address…" }));
      kind.value = it.page ? "page:" + it.page : (it.url ? "url" : "");
      function sync() { url.style.display = kind.value === "url" ? "" : "none"; }
      kind.addEventListener("change", sync); sync();
      var line = h("div", { class: "reprow cardrow" }, title, text, kind, url, btn("✕", "ghost", function () { rows.splice(rows.indexOf(rec), 1); line.remove(); }));
      var rec = { get: function () {
        var o = { title: title.value, text: text.value };
        if (kind.value.indexOf("page:") === 0) o.page = kind.value.slice(5); else if (kind.value === "url") o.url = url.value;
        return o;
      } };
      rows.push(rec); box.appendChild(line);
    }
    (items || []).forEach(addRow);
    return { el: h("div", null, box, btn("＋ Add card", "", function () { addRow({}); })), get: function () { return rows.map(function (r) { return r.get(); }); } };
  }

  function blockForm(p, blk, spec) {
    var fields = spec.fields || [], ctrls = {}, wrap = h("div", { class: "form" });
    var err = h("div", { class: "err" });
    if (!fields.length) wrap.appendChild(h("p", { class: "hint", text: "This block has no settings." }));
    fields.forEach(function (f) {
      var c = control(f, (blk.props || {})[f.key]); ctrls[f.key] = c;
      wrap.appendChild(h("div", { class: "fld" }, h("label", { class: "lb", text: (f.label || f.key) + (f.required ? " *" : "") }), c.el, f.help ? h("div", { class: "hint", text: f.help }) : null));
    });
    var isNew = !blk.id;
    wrap.appendChild(h("div", { class: "row", style: "margin-top:12px" }, err, h("span", { style: "flex:1" }),
      btn("Cancel", "ghost", function () { curBlock = null; render(); }),
      btn(isNew ? "Add to page" : "Save block", "primary", function () {
        var props = {}; fields.forEach(function (f) { props[f.key] = ctrls[f.key].get(); });
        (isNew ? act("block.add", { page: p.id, type: spec.id, props: props }, "Block added.") : act("block.update", { page: p.id, id: blk.id, props: props }, "Block saved."))
          .then(function () { curBlock = null; render(); }).catch(function (e) { showErr(err, e); });
      })));
    return wrap;
  }

  // ---------------------------------------------------------------- design
  var KNOWN_VARS = [["--bg", "Page background"], ["--panel", "Panels and cards"], ["--text", "Text"], ["--muted", "Quiet text"], ["--line", "Borders"],
                    ["--accent", "Main accent"], ["--accent-2", "Second accent"], ["--danger", "Warnings"]];
  function design(b) {
    var d = SS.model, brand = d.brand, err = h("div", { class: "err" });
    var f = { name: h("input", { type: "text", value: brand.name }), mark: h("input", { type: "text", value: brand.mark, style: "width:90px" }),
              tagline: h("input", { type: "text", value: brand.tagline }), handle: h("input", { type: "text", value: brand.handle }), footer: h("input", { type: "text", value: brand.footer }) };
    b.appendChild(h("div", null, section("Brand")));
    b.appendChild(h("div", { class: "set", style: "max-width:860px" },
      row("Site name", "Shown top left and in the browser tab.", f.name), row("Mark", "1 to 4 characters in the logo box and the generated icon.", f.mark),
      row("Tagline", "", f.tagline), row("Handle", "Your name or channel.", f.handle), row("Footer text", "Replaces the handle in the footer.", f.footer),
      h("div", { class: "r" }, err, h("span", { style: "flex:1" }), btn("Save brand", "primary", function () {
        act("brand", { name: f.name.value, mark: f.mark.value, tagline: f.tagline.value, handle: f.handle.value, footer: f.footer.value }, "Brand saved.").catch(function (e) { showErr(err, e); });
      }))));

    b.appendChild(h("div", null, section("Theme", "A theme is a complete look. Pick one, then tweak colours below.")));
    var grid = h("div", { class: "choices", style: "grid-template-columns:repeat(auto-fill,minmax(230px,1fr));max-width:860px" });
    SS.catalog.theme.filter(function (t) { return t.ok && t.enabled; }).forEach(function (t) {
      var cur = d.theme.id === t.id, v = t.vars || {};
      var sample = h("div", { class: "swatch" }, ["--bg", "--panel", "--accent", "--accent-2", "--text"].map(function (k) { return h("i", { style: "background:" + ((v[k] || "#222").replace(/[;"<>]/g, "")) }); }));
      grid.appendChild(h("div", { class: "choice" + (cur ? " sel" : ""), on: { click: function () { act("theme", { id: t.id, vars: {} }, "Theme changed. Colour tweaks were cleared."); } } },
        sample, h("h4", { text: t.name + (cur ? "  ✔" : "") }), h("p", { text: t.description })));
    });
    b.appendChild(grid);

    var isFunk = d.theme.id === "funk";
    if (isFunk) {
      var ft = d.theme.funk;
      b.appendChild(h("div", { class: "set", style: "max-width:860px;margin-top:14px" },
        row("Funk theme: " + (ft ? ft.name : "Funk (default)"), "Gradients and animated backgrounds (ASCII smoke, Matrix, Synthwave, Starfield, Rain). The maker previews it live on this window and saves the result to the site. Pictures are not supported on the site yet.",
          btn("Open the theme maker", "primary", function () {
            HB.openThemeMaker({ current: ft || FUNK_DEFAULT, onDone: function (t) {
              act("theme", { id: "funk", funk: t }, "Funk theme saved to the site. Rebuild the preview to see it.").catch(function () {});
            } });
          }))));
    }

    var vars = Object.assign({}, d.theme.vars || {}), inputs = {};
    var cur = SS.catalog.theme.filter(function (t) { return t.id === d.theme.id; })[0] || { vars: {} };
    var vbox = h("div", { class: "set", style: "max-width:860px;margin-top:14px" });
    KNOWN_VARS.forEach(function (kv) {
      var base = (cur.vars || {})[kv[0]] || "", inp = h("input", { type: "text", placeholder: base || "(theme default)", value: vars[kv[0]] || "", style: "width:160px" });
      inputs[kv[0]] = inp; vbox.appendChild(row(kv[1], kv[0], inp));
    });
    var verr = h("div", { class: "err" });
    var modeSel = h("select", null, ["auto", "light", "dark"].map(function (m) { return h("option", { value: m, text: m }); })); modeSel.value = d.theme.mode || "auto";
    vbox.appendChild(row("Light/dark hint", "Tells the browser which controls to draw.", modeSel));
    vbox.appendChild(h("div", { class: "r" }, verr, h("span", { style: "flex:1" }), btn("Reset colours", "", function () { act("theme", { vars: {} }, "Colours reset to the theme's own."); }),
      btn("Save colours", "primary", function () {
        var out = {}; Object.keys(inputs).forEach(function (k) { if (inputs[k].value.trim()) out[k] = inputs[k].value.trim(); });
        act("theme", { vars: out, mode: modeSel.value }, "Colours saved.").catch(function (e) { showErr(verr, e); });
      })));
    if (!isFunk) b.appendChild(vbox);
    else b.appendChild(h("p", { class: "hint", style: "margin-top:12px", text: "The colours come from the Funk theme. Change them in the theme maker." }));

    b.appendChild(h("div", null, section("Menu layout")));
    var navSel = h("select", null, [["top", "Across the top"], ["side", "Down the side"], ["none", "No menu"]].map(function (o) { return h("option", { value: o[0], text: o[1] }); }));
    navSel.value = d.settings.nav_style;
    navSel.addEventListener("change", function () { act("settings", { nav_style: navSel.value }, "Menu layout saved."); });
    b.appendChild(h("div", { class: "set", style: "max-width:860px" }, row("Where the menu goes", "", navSel)));
  }

  // ---------------------------------------------------------------- menu & redirects
  function menu(b) {
    var d = SS.model;
    b.appendChild(h("div", null, section("Extra menu links", "Links to other sites that sit in the menu after your pages (up to 12). To hide or show one of your own pages, use its 'Show in the menu' switch.")));
    var err = h("div", { class: "err" }), links = repeater(d.nav_links, [["label", "Label"], ["url", "Address"]], function (it) { return { label: it.label || "", url: it.url || "" }; });
    b.appendChild(h("div", { class: "set", style: "max-width:860px;padding:14px 18px" }, links.el, h("div", { class: "row", style: "margin-top:12px" }, err, h("span", { style: "flex:1" }),
      btn("Save menu links", "primary", function () { act("nav_links", { links: links.get() }, "Menu links saved.").catch(function (e) { showErr(err, e); }); }))));

    b.appendChild(h("div", null, section("Redirects", "Send an old address somewhere else, so shared links keep working after you rename or remove a page.")));
    var rerr = h("div", { class: "err" });
    var reds = repeater(d.redirects.map(function (r) { return { from: r.from, to: r.to, status: String(r.status) }; }),
      [["from", "From, e.g. /old"], ["to", "To, e.g. /new/ or https://…"], ["status", "301"]], function (it) { return { from: it.from || "", to: it.to || "", status: it.status || "301" }; });
    b.appendChild(h("div", { class: "set", style: "max-width:860px;padding:14px 18px" }, reds.el, h("div", { class: "row", style: "margin-top:12px" }, rerr, h("span", { style: "flex:1" }),
      btn("Save redirects", "primary", function () {
        act("redirects", { redirects: reds.get().filter(function (r) { return r.from || r.to; }).map(function (r) { return { from: r.from, to: r.to, status: parseInt(r.status, 10) || 301 }; }) }, "Redirects saved.").catch(function (e) { showErr(rerr, e); });
      }))));
  }

  // ---------------------------------------------------------------- linked sites
  var SECRET_NAMES = ["k", "key", "token", "secret", "auth", "sig", "signature", "password", "pass", "pwd", "code", "apikey", "api_key", "access_token", "session", "sid", "otp"];
  function secretNames(url) {
    var out = [];
    String(url || "").replace(/[?&#;]([^=&#;]+)=/g, function (m, name) {
      name = decodeURIComponent(name).toLowerCase();
      if (SECRET_NAMES.indexOf(name) >= 0 && out.indexOf(name) < 0) out.push(name);
      return m;
    });
    return out;
  }
  function maskUrl(url) {               // never show a key on screen: the value is replaced
    return String(url || "").replace(/([?&#;](?:k|key|token|secret|auth|sig|signature|password|pass|pwd|code|apikey|api_key|access_token|session|sid|otp)=)[^&#;]*/gi, "$1••••");
  }
  function probeLines(r) {
    var lines = [];
    lines.push(r.reachable ? "✔ It answers" + (r.status ? " (status " + r.status + ")" : "") + "." : "✖ It could not be reached.");
    if (r.cloudflare) lines.push("☁ It is on Cloudflare.");
    if (r.embeddable === true) lines.push("✔ It can be shown inside your site.");
    (r.notes || []).forEach(function (n) { lines.push("• " + n); });
    return lines;
  }
  function linkedSites(b) {
    var d = SS.model;
    b.appendChild(h("div", { class: "row", style: "margin-bottom:14px" },
      h("div", null, h("h2", { class: "h2", style: "margin:0 0 4px", text: "Linked sites" }),
        h("div", { class: "hint", text: "Sites that live somewhere else, like a friend's Cloudflare Pages or Workers site. Halbach never hosts them or holds a key for them; visitors just reach them from here." })),
      h("span", { style: "flex:1" }), btn("＋ Link a site", "primary", addLinkedDialog)));
    var box = h("div", { class: "set", style: "max-width:980px;padding:0 6px" });
    if (!d.linked_sites.length) box.appendChild(h("p", { class: "empty", text: "Nothing linked yet." }));
    d.linked_sites.forEach(function (x) {
      var note = h("small", { text: maskUrl(x.url) + "  ·  " + (x.page ? "own page" : "card/menu only") + (x.mode === "embed" ? "  ·  shown inside this site" : "") });
      var result = h("div", { class: "hint", style: "white-space:pre-line" });
      box.appendChild(h("div", { class: "r" }, h("div", { style: "flex:1;min-width:0" }, h("b", { text: x.label }), note, result),
        btn("Check", "", function () {
          result.textContent = "Checking…";
          HB.api("/api/site/linked.check", "POST", { id: x.id }).then(function (r) { result.textContent = probeLines(r).join("\n"); }).catch(function (e) { result.textContent = e.message; });
        }),
        btn("Remove", "ghost", function () {
          if (confirm("Remove the link to '" + x.label + "'? Its page and menu entry go too. The site itself is not touched.")) act("linked.delete", { id: x.id }, "Link removed.");
        })));
      if (secretNames(x.url).length) box.appendChild(h("div", { class: "prob", style: "padding:0 14px 10px", text: "⚠ This address has a key in it (" + secretNames(x.url).join(", ") + "). Everything on your site is public, so anyone can copy it." }));
    });
    b.appendChild(box);
    b.appendChild(h("div", null, section("Show them on a page", "Add a “Linked sites” block to any page (Pages → Edit → Add block) to list them as cards.")));
  }

  function addLinkedDialog() {
    var f = { label: h("input", { type: "text", placeholder: "e.g. FunkStudio Share" }), url: h("input", { type: "text", placeholder: "https://something.workers.dev" }),
              desc: h("input", { type: "text", placeholder: "One line about it" }) };
    var mode = h("select", null, h("option", { value: "link", text: "Open it in a new tab" }), h("option", { value: "embed", text: "Show it inside my site" }));
    var own = sw(true, function () {}), menu = sw(true, function () {});
    var okKey = h("input", { type: "checkbox" }), keyBox = h("label", { class: "prob", style: "display:none;margin-top:10px" }, okKey, " I understand this key will be public.");
    var info = h("div", { class: "hint", style: "white-space:pre-line;margin-top:8px" }), err = h("div", { class: "err" });
    f.url.addEventListener("input", function () {
      var n = secretNames(f.url.value);
      keyBox.style.display = n.length ? "" : "none";
      if (n.length) keyBox.firstChild.nextSibling.textContent = " This address has a key in it (" + n.join(", ") + "). Everything on your site is public. Use the plain address instead, or tick this to publish the key.";
    });
    var body = h("div", null, h("label", { class: "lb", style: "margin-top:0", text: "Name" }), f.label, h("label", { class: "lb", text: "Address" }), f.url, keyBox,
      h("div", { class: "row", style: "margin-top:8px" }, btn("Check this address", "", function () {
        info.textContent = "Checking…";
        HB.api("/api/site/linked.check", "POST", { url: f.url.value }).then(function (r) { info.textContent = probeLines(r).join("\n"); if (r.embeddable === false) mode.value = "link"; }).catch(function (e) { info.textContent = e.message; });
      })), info,
      h("label", { class: "lb", text: "Description" }), f.desc, h("label", { class: "lb", text: "How it appears" }), mode,
      h("div", { class: "row", style: "margin-top:12px" }, own, h("span", { text: "Give it its own page on my site" })),
      h("div", { class: "row", style: "margin-top:8px" }, menu, h("span", { text: "Show it in the menu" })), err);
    HB.dialog("Link a site", body, "Link it", function () {
      return act("linked.add", { label: f.label.value, url: f.url.value, description: f.desc.value, mode: mode.value, own_page: own.firstChild.checked,
                                 menu: menu.firstChild.checked, confirm_public_key: okKey.checked }, "Site linked. Rebuild the preview to see it.")
        .then(function () { return true; }).catch(function (e) { showErr(err, e); return false; });
    });
  }

  // ---------------------------------------------------------------- site settings
  function siteSettings(b) {
    var s = SS.model.settings, err = h("div", { class: "err" });
    var base = h("input", { type: "text", value: s.base_url, placeholder: "https://example.com" }), lang = h("input", { type: "text", value: s.lang, style: "width:80px" });
    function pick(opts, val) { var x = h("select", null, opts.map(function (o) { return h("option", { value: o[0], text: o[1] }); })); x.value = val; return x; }
    var csp = pick([["report-only", "Report only (safe to start with: nothing is blocked)"], ["enforce", "Enforce (blocks anything not allowed)"], ["off", "Off"]], s.csp);
    var an = pick([["cloudflare", "Cloudflare Web Analytics"], ["none", "None"]], s.analytics);
    var idx = sw(s.indexable, function () {});
    b.appendChild(h("div", null, section("Site settings")));
    b.appendChild(h("div", { class: "set", style: "max-width:860px" },
      row("Site address", "Needed for the sitemap, link previews and canonical addresses.", base), row("Language", "Used by screen readers, e.g. en or pt-BR.", lang),
      row("Security policy", "Tells browsers what your pages may load. Start in report-only, check Preview shows no warnings, then enforce.", csp),
      row("Visitor counting", "Cloudflare's analytics are cookie-free.", an), row("Let search engines list the site", "Off asks them to stay away.", idx),
      h("div", { class: "r" }, err, h("span", { style: "flex:1" }), btn("Save settings", "primary", function () {
        act("settings", { base_url: base.value, lang: lang.value, csp: csp.value, analytics: an.value, indexable: idx.firstChild.checked }, "Settings saved.").catch(function (e) { showErr(err, e); });
      }))));
  }

  // ---------------------------------------------------------------- history
  function history(b) {
    b.appendChild(h("div", null, section("History", "Every change is saved. Restoring an older version is itself saved, so you can undo that too.")));
    var box = h("div", { class: "set", style: "max-width:860px;padding:0 6px" });
    SS.history.forEach(function (x, i) {
      box.appendChild(h("div", { class: "r" }, h("div", { style: "flex:1" }, h("b", { text: "#" + x.n + "  " + (x.label || "change") }),
        h("small", { text: new Date(x.time * 1000).toLocaleString() + "  ·  " + x.pages + " pages" })),
        i === 0 ? h("span", { class: "tag static", text: "CURRENT" }) : btn("Restore", "", function () { if (confirm("Go back to version #" + x.n + "?")) act("restore", { n: x.n }, "Restored."); })));
    });
    if (!SS.history.length) box.appendChild(h("p", { class: "empty", text: "No changes saved yet." }));
    b.appendChild(box);

    b.appendChild(h("div", null, section("Move the site", "Save the whole site (pages, design, settings) as one file, or load one. Your uploaded files and modules are not inside it.")));
    var file = h("input", { type: "file", accept: ".json,application/json", style: "display:none" });
    file.addEventListener("change", function () {
      var f = file.files[0]; if (!f) return;
      if (f.size > 2 * 1024 * 1024) return HB.toast("That file is too big to be a site export.");
      var rd = new FileReader();
      rd.onload = function () {
        var pkg; try { pkg = JSON.parse(rd.result); } catch (e) { return HB.toast("That file is not valid."); }
        if (confirm("Replace the current site with the one in this file? The current one stays in History.")) act("import.package", { package: pkg }, "Site loaded from the file.").catch(function () {});
      };
      rd.readAsText(f); file.value = "";
    });
    b.appendChild(h("div", { class: "row" }, btn("Download the site file", "", function () {
      fetch("/api/site/export", { headers: { "X-Halbach-Token": document.querySelector('meta[name="halbach-token"]').content } })
        .then(function (r) { return r.blob(); }).then(function (blob) {
          var a = h("a", { href: URL.createObjectURL(blob), download: "halbach-site.json" }); document.body.appendChild(a); a.click(); a.remove();
        }).catch(HB.fail);
    }), btn("Load a site file…", "", function () { file.click(); }), file));
  }

  // ---------------------------------------------------------------- preview
  function preview(b) {
    var info = h("div", { class: "hint", style: "margin-bottom:10px" }), frameBox = h("div", { class: "pframe" });
    function build() {
      info.textContent = "Building…";
      HB.api("/api/site/preview", "POST", {}).then(function (r) {
        info.textContent = r.ok ? r.pages.length + " pages, " + r.files + " files. This is exactly what would be published (drafts included)." : "The site has problems, so there is nothing to preview yet.";
        frameBox.textContent = "";
        if (!r.ok) {
          r.errors.forEach(function (e) { frameBox.appendChild(h("div", { class: "errline", text: "✖ " + e.message })); });
          return;
        }
        var sel = h("select", null, r.pages.map(function (p) { return h("option", { value: p.path, text: p.path }); }));
        var fr = h("iframe", { class: "pview", title: "Preview", src: "/preview" + r.pages[0].path });
        fr.style.width = previewWidth + "px";
        sel.addEventListener("change", function () { fr.src = "/preview" + sel.value; });
        var widths = h("span", { class: "row" }, [["Phone", 390], ["Tablet", 780], ["Desktop", 1100]].map(function (w) { return btn(w[0], "ghost", function () { previewWidth = w[1]; fr.style.width = w[1] + "px"; }); }));
        frameBox.appendChild(h("div", { class: "row", style: "margin-bottom:8px" }, sel, widths, h("span", { style: "flex:1" }),
          h("a", { class: "btn", href: "/preview/", target: "_blank", rel: "noopener", text: "Open in a new tab ↗" })));
        frameBox.appendChild(h("div", { class: "pscroll" }, fr));
        if (r.warnings.length) r.warnings.slice(0, 8).forEach(function (w) { frameBox.appendChild(h("div", { class: "prob", text: "⚠ " + w.message })); });
      }).catch(function (e) { info.textContent = e.message; });
    }
    b.appendChild(h("div", { class: "row", style: "margin-bottom:8px" }, h("h2", { class: "h2", style: "margin:0", text: "Preview" }), h("span", { style: "flex:1" }), btn("Rebuild preview", "primary", build)));
    b.appendChild(info); b.appendChild(frameBox);
    build();
  }

  HB.h = h; HB.btn = btn; HB.sw = sw;
  window.SiteUI = { open: function () { sub = sub === "preview" ? "overview" : sub; load(); }, reload: load };
})();
