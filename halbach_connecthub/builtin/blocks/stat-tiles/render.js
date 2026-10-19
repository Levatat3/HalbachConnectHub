// Block: stat-tiles. A grid of value/label tiles, drawn with ctx.el (textContent only).
HalbachSite.registerBlock("stat-tiles", function (box, props, ctx) {
  var items = props.items || [];
  if (!items.length) return ctx.note(box, "Add a tile: a value and a label for each.");
  var grid = ctx.el("div", "hs-stats");
  items.forEach(function (it) {
    var tile = ctx.el("div", "hs-stat");
    tile.appendChild(ctx.el("strong", "hs-stat-value", it.title));
    if (it.text) tile.appendChild(ctx.el("span", "hs-stat-label", it.text));
    grid.appendChild(tile);
  });
  box.appendChild(grid);
});
