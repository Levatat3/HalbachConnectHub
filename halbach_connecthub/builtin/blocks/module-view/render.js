// Block: module-view. Shows one module in place.
HalbachSite.registerBlock("module-view", function (box, props, ctx) {
  ctx.module(props.module).then(function (m) {
    if (!m) return ctx.note(box, "That module is not available.");
    ctx.mod.view(box, m);
  });
});
