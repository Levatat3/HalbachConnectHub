// Block: module-list. A grid of the modules the owner chose to list; click one to open it below.
HalbachSite.registerBlock("module-list", function (box, props, ctx) {
  var grid = ctx.el("div", "hs-cards"), view = ctx.el("div", "hs-modview");
  view.hidden = true;
  box.appendChild(grid); box.appendChild(view);
  ctx.modules().then(function (mods) {
    var ids = props.ids || [];
    var list = mods.filter(function (m) {
      if (m.listed === false || m.tab) return false;
      if (ids.length && ids.indexOf(m.id) < 0) return false;
      if (props.hide_live && m.kind === "live") return false;
      return true;
    });
    if (!list.length) return grid.appendChild(ctx.el("p", "hs-empty", "Nothing here yet. New things show up as they are added."));
    list.forEach(function (m) {
      var c = ctx.el("button", "hs-card"); c.type = "button";
      c.appendChild(ctx.el("b", null, m.name));
      c.appendChild(ctx.el("span", null, m.desc || (m.kind === "live" ? "Live from the owner's PC." : "")));
      c.appendChild(document.createElement("br"));
      c.appendChild(ctx.el("span", "hs-tag", m.kind === "live" ? "live" : (m.type || "file")));
      c.onclick = function () {
        view.hidden = false; view.innerHTML = "";
        var head = ctx.el("div", "hs-modhead"), close = ctx.el("button", "hs-button", "Close");
        head.appendChild(ctx.el("h3", null, m.name)); close.type = "button";
        close.onclick = function () { view.hidden = true; view.innerHTML = ""; };
        head.appendChild(close); view.appendChild(head);
        if (m.desc) view.appendChild(ctx.el("p", "hs-lede", m.desc));
        var body = ctx.el("div", "hs-screen"); view.appendChild(body);
        ctx.mod.view(body, m);
        view.scrollIntoView({ behavior: ctx.reducedMotion ? "auto" : "smooth", block: "start" });
      };
      grid.appendChild(c);
    });
  });
});
