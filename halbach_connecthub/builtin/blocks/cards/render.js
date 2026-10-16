// Block: cards. A grid of cards; `href` is resolved at build time (page id or address).
HalbachSite.registerBlock("cards", function (box, props, ctx) {
  var grid = ctx.el("div", "hs-cards");
  (props.items || []).forEach(function (c) {
    var node = c.href ? ctx.el("a", "hs-card") : ctx.el("div", "hs-card");
    if (c.href) {
      node.href = c.href;
      if (/^https?:/i.test(c.href)) { node.target = "_blank"; node.rel = "noopener noreferrer"; }
    }
    node.appendChild(ctx.el("b", null, c.title));
    if (c.text) node.appendChild(ctx.el("span", null, c.text));
    grid.appendChild(node);
  });
  box.appendChild(grid);
});
