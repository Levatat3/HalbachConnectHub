"use strict";
// The collaborator's area window: edit one page (a folder of area.json + files/), preview it, and send it to the
// owner's site for review. Every change goes through /api/site/<action>; sending goes through /api/area/send.
// Text only (never HTML) is drawn from anything the model holds.
(function () {
  var TOKEN = (document.querySelector('meta[name="halbach-token"]') || {}).content || "";
  var SS = null;                 // last /api/site answer
  var LIMITS = null;             // last /api/area/limits answer (owner's allow-list)
  var curBlock = null;           // block id open in the editor ("new:<type>" while adding)

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
  function toast(msg) { var t = h("div", { class: "toast", text: msg }); document.body.appendChild(t); setTimeout(function () { t.remove(); }, 3500); }

  // ---------------------------------------------------------------- talking to the hub
  function api(path, method, body) {
    return fetch(path, { method: method || "GET",
      headers: Object.assign({ "X-Halbach-Token": TOKEN }, body ? { "Content-Type": "application/json" } : {}),
      body: body ? JSON.stringify(body) : undefined })
      .then(function (r) {
        return r.json().catch(function () { return {}; }).then(function (j) {
          if (!r.ok) throw new Error(j.error || ("The hub answered " + r.status + "."));
          return j;
        });
      });
  }
  function fail(e) { toast(e && e.message ? e.message : String(e)); }

  function load() {
    return api("/api/site").then(function (d) { SS = d; render(); }).catch(fail);
  }
  function loadLimits() {
    return api("/api/area/limits").then(function (d) { LIMITS = d; render(); }).catch(function () { LIMITS = { blocks: null }; render(); });
  }
  function act(name, args, msg) {
    return api("/api/site/" + name, "POST", args || {}).then(function () { if (msg) toast(msg); return load(); })
      .catch(function (e) { toast(e.message || "That change was refused"); throw e; });
  }
  function showErr(box, e) { box.textContent = e && e.message ? e.message : String(e); }

  // ---------------------------------------------------------------- render
  function page() { return SS.model.pages[0]; }
  function blockSpec(type) { return (SS.catalog.block.filter(function (b) { return b.id === type; })[0]) || { label: type }; }
  function allowed() {
    var set = LIMITS && LIMITS.blocks && LIMITS.blocks.length ? LIMITS.blocks : null;
    return set ? set : null;   // null = the owner has not limited it; still only area-safe blocks are offered below
  }
  function areaBlockTypes() { return ["hero", "text", "cards", "links", "image", "divider", "youtube", "twitch"]; }

  function render() {
    var r = document.getElementById("areaRoot"); if (!r || !SS) return;
    r.textContent = "";
    var p = page();
    var allow = allowed();
    var err = h("div", { class: "err" });
    var title = h("input", { type: "text", value: p.title });
    var desc = h("textarea", { rows: 2, value: p.description }); desc.value = p.description;

    r.appendChild(h("div", { class: "set", style: "max-width:860px" },
      h("div", { class: "r" }, h("div", null, h("b", { text: "Title" }), h("small", { text: "The heading of your page on the site." })), h("span", { style: "flex:1" }), h("div", { style: "flex:1;max-width:320px;min-width:0" }, title)),
      h("div", { class: "r" }, h("div", null, h("b", { text: "Description" }), h("small", { text: "One short line (optional)." })), h("span", { style: "flex:1" }), h("div", { style: "flex:1;max-width:320px;min-width:0" }, desc)),
      h("div", { class: "r" }, err, h("span", { style: "flex:1" }), btn("Save title", "primary", function () {
        act("page.update", { id: p.id, title: title.value, description: desc.value, label: title.value }, "Saved.").catch(function (e) { showErr(err, e); });
      }))));

    // blocks
    r.appendChild(h("div", { class: "row", style: "margin:26px 0 12px" }, h("h2", { class: "h2", style: "margin:0", text: "Content" }), h("span", { style: "flex:1" }),
      btn("＋ Add block", "primary", function () { blockPicker(p, allow); })));
    var list = h("div", { class: "set", style: "max-width:980px;padding:0 6px" });
    if (!p.blocks.length) list.appendChild(h("p", { class: "empty", text: "Nothing here yet. Add a block." }));
    p.blocks.forEach(function (blk, i) {
      var spec = blockSpec(blk.type);
      list.appendChild(h("div", { class: "r pagerow" + (blk.enabled === false ? " off" : "") },
        h("div", { style: "flex:1;min-width:0;cursor:pointer", on: { click: function () { curBlock = curBlock === blk.id ? null : blk.id; render(); } } },
          h("b", { text: spec.label || blk.type }), h("small", { text: summarize(blk) })),
        btn("▲", "ghost", function () { act("block.move", { page: p.id, id: blk.id, delta: -1 }); }, i === 0 ? { disabled: "" } : null),
        btn("▼", "ghost", function () { act("block.move", { page: p.id, id: blk.id, delta: 1 }); }, i === p.blocks.length - 1 ? { disabled: "" } : null),
        btn("Edit", "", function () { curBlock = curBlock === blk.id ? null : blk.id; render(); }),
        btn("✕", "ghost", function () { if (confirm("Remove this block?")) act("block.delete", { page: p.id, id: blk.id }, "Removed."); })));
      if (curBlock === blk.id) list.appendChild(h("div", { class: "blockform" }, blockForm(p, blk, spec)));
    });
    if (curBlock && curBlock.indexOf("new:") === 0) {
      var spec2 = blockSpec(curBlock.slice(4));
      if (spec2) list.appendChild(h("div", { class: "blockform" }, h("b", { text: "New: " + spec2.label }), blockForm(p, { id: null, type: spec2.id, props: {} }, spec2)));
    }
    r.appendChild(list);

    renderStatus(r);
  }

  function summarize(blk) {
    var pr = blk.props || {}, bits = [];
    ["heading", "channel", "user", "url", "view"].forEach(function (k) { if (pr[k]) bits.push(k + ": " + pr[k]); });
    if (pr.markdown) bits.push(String(pr.markdown).replace(/\s+/g, " ").slice(0, 60));
    if (pr.items) bits.push(pr.items.length + " item" + (pr.items.length === 1 ? "" : "s"));
    return bits.join("  ·  ") || "(no settings)";
  }

  function blockPicker(p, allow) {
    var types = areaBlockTypes().filter(function (t) { return !allow || allow.indexOf(t) >= 0; });
    var body = h("div");
    var grid = h("div", { class: "choices", style: "grid-template-columns:1fr 1fr" });
    types.forEach(function (t) {
      var spec = blockSpec(t);
      grid.appendChild(h("div", { class: "choice", on: { click: function () { closePicker(); curBlock = "new:" + t; render(); } } },
        h("h4", { style: "margin:0 0 4px", text: spec.label || t }), h("p", { text: spec.description || "" })));
    });
    if (!types.length) body.appendChild(h("p", { class: "empty", text: "The owner has not allowed any block types for this area." }));
    else body.appendChild(grid);
    showDialog("Add a block", body);
  }

  // ---------------------------------------------------------------- forms (same field types as the site editor)
  function control(field, value) {
    var t = field.type, v = value == null ? (field["default"] == null ? "" : field["default"]) : value;
    if (t === "bool") { var s = sw(!!v, function () {}); return { el: s, get: function () { return s.firstChild.checked; } }; }
    if (t === "longtext" || t === "markdown") { var ta = h("textarea", { rows: t === "markdown" ? 9 : 4, class: t === "markdown" ? "mono" : "" }); ta.value = v; return { el: ta, get: function () { return ta.value; } }; }
    if (t === "list-text") { var tl = h("textarea", { rows: 4, placeholder: "one per line" }); tl.value = (v || []).join ? (v || []).join("\n") : ""; return { el: tl, get: function () { return tl.value.split("\n").map(function (x) { return x.trim(); }).filter(Boolean); } }; }
    if (t === "number") { var n = h("input", { type: "number", min: field.min, max: field.max }); n.value = v; return { el: n, get: function () { return n.value === "" ? "" : Number(n.value); } }; }
    if (t === "select") { var sel = h("select", null, (field.options || []).map(function (o) { return h("option", { value: o, text: o }); })); sel.value = v; return { el: sel, get: function () { return sel.value; } }; }
    if (t === "color") { var c = h("input", { type: "text", placeholder: "#39ff7a" }); c.value = v; return { el: c, get: function () { return c.value; } }; }
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
    return { el: h("div", null, box, btn("＋ Add", "", function () { addRow({}); })), get: function () { return rows.map(function (r) { return r.get(); }); } };
  }
  function cardsControl(items) {
    var box = h("div", { class: "rep" }), rows = [];
    function addRow(it) {
      it = it || {};
      var title = h("input", { type: "text", placeholder: "Title" }), text = h("input", { type: "text", placeholder: "Text" }), url = h("input", { type: "text", placeholder: "https://… or /path" });
      title.value = it.title || ""; text.value = it.text || ""; url.value = it.url || "";
      var kind = h("select", null, h("option", { value: "", text: "no link" }), h("option", { value: "url", text: "an address…" }));
      kind.value = it.url ? "url" : "";
      function sync() { url.style.display = kind.value === "url" ? "" : "none"; }
      kind.addEventListener("change", sync); sync();
      var line = h("div", { class: "reprow cardrow" }, title, text, kind, url, btn("✕", "ghost", function () { rows.splice(rows.indexOf(rec), 1); line.remove(); }));
      var rec = { get: function () { var o = { title: title.value, text: text.value }; if (kind.value === "url") o.url = url.value; return o; } };
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

  // ---------------------------------------------------------------- dialog (one at a time)
  var dlg = null;
  function showDialog(title, body) {
    if (dlg) dlg.remove();
    var box = h("div", { class: "scrim open" }, h("div", { class: "dlg" }, h("h3", { text: title }), body, h("div", { class: "row", style: "margin-top:14px" }, h("span", { style: "flex:1" }), btn("Close", "ghost", closePicker))));
    document.body.appendChild(box);
    dlg = box;
  }
  function closePicker() { if (dlg) { dlg.remove(); dlg = null; } }

  // ---------------------------------------------------------------- preview, send, status
  function renderStatus(r) {
    var box = h("div", { class: "set", style: "max-width:860px;margin-top:26px" });
    box.appendChild(h("h2", { class: "h2", style: "margin:0 0 6px", text: "What the owner has received" }));
    api("/api/area/status").then(function (st) {
      var subs = (st && st.submissions) || [];
      var dot = document.getElementById("dotArea"), pill = document.getElementById("pillArea");
      if (!subs.length) { box.appendChild(h("p", { class: "hint", text: "Nothing sent yet." })); pill.textContent = "nothing sent"; }
      else {
        var last = subs[0]; pill.textContent = last.state + (last.note ? " — " + last.note : "");
        dot.className = "dot " + (last.state === "approved" ? "" : "warn");
        subs.slice(0, 12).forEach(function (s) {
          box.appendChild(h("div", { class: "r" },
            h("div", null, h("b", { text: when(s.received) }), h("small", { text: s.id })),
            h("span", { style: "flex:1" }),
            h("span", { class: "tag " + (s.state === "approved" ? "live" : (s.state === "rejected" ? "new" : "static")), text: s.state }),
            s.note ? h("span", { class: "hint", style: "margin-left:10px", text: s.note }) : null));
        });
      }
    }).catch(function () { box.appendChild(h("p", { class: "hint", text: "Not connected yet — connect with: halbach area connect" })); });
    r.appendChild(box);
  }
  function when(ts) { return ts ? new Date(ts * 1000).toLocaleString() : "—"; }

  document.getElementById("areaPreview").addEventListener("click", function () {
    api("/api/site/preview", "POST", {}).then(function (res) {
      if (res.errors && res.errors.length) { toast("Fix the problems first: " + res.errors[0].message); render(); }
      else { window.open("/preview/", "_blank"); }
    }).catch(fail);
  });
  document.getElementById("areaSend").addEventListener("click", function () {
    var b = document.getElementById("areaSend"); b.disabled = true; b.textContent = "Sending…";
    api("/api/area/send", "POST", {}).then(function (body) {
      b.disabled = false; b.textContent = "Send to the site";
      toast("Sent. It is " + body.state + (body.warnings && body.warnings.length ? " (see below)" : "") + ".");
      loadLimits();
    }).catch(function (e) { b.disabled = false; b.textContent = "Send to the site"; fail(e); });
  });

  loadLimits();
  load();
})();
