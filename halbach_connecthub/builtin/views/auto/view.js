// View: auto. One object becomes rows; a list becomes a card per item; "x" and "max_x" show as "x / max".
HalbachSite.registerView("auto", function (table, d, ctx) {
  table.innerHTML = "";
  var list = Array.isArray(d);
  table.className = list ? "hs-livegrid" : "hs-livebox";
  (list ? d : [d]).forEach(function (item) {
    var holder = list ? ctx.el("div", "hs-livecard") : table;
    if (list) {
      if (item.name != null) holder.appendChild(ctx.el("h4", null, String(item.name)));
      table.appendChild(holder);
    }
    Object.keys(item).forEach(function (k) {
      if (list && k === "name") return;
      if (/^max_|_max$/.test(k) && (k.replace(/^max_|_max$/, "") in item)) return;
      var v = item[k], mk = ("max_" + k in item) ? "max_" + k : (k + "_max" in item ? k + "_max" : null);
      var text = typeof v === "object" && v !== null ? JSON.stringify(v) : String(v);
      if (mk && typeof v === "number") text = v + " / " + item[mk];
      var row = ctx.el("div", "hs-liverow");
      row.appendChild(ctx.el("span", "k", k)); row.appendChild(ctx.el("span", "v", text));
      holder.appendChild(row);
    });
  });
  table.querySelectorAll(".hs-livecard").forEach(function (c) {
    if (!c.querySelector(".hs-liverow")) c.appendChild(ctx.el("div", "hs-liverow", "no data right now"));
  });
  if (!table.children.length) table.appendChild(ctx.el("p", "hs-empty", "Online, but nothing is being shared."));
});
