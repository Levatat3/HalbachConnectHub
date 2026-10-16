// Block: gallery. A grid of images from a gallery module.
HalbachSite.registerBlock("gallery", function (box, props, ctx) {
  ctx.module(props.module).then(function (m) {
    if (!m || m.type !== "gallery") return ctx.note(box, "That module is not a gallery.");
    ctx.mod.view(box, m);
  });
});
