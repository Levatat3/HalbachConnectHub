// Block: live-view. Live data from the owner's PC, drawn by a view (cards, pets, or a plugin's).
HalbachSite.registerBlock("live-view", function (box, props, ctx) {
  ctx.module(props.module).then(function (m) {
    var name = props.view && props.view !== "auto" ? props.view : ((m && m.view) || "auto");
    return ctx.view(name).then(function (fn) { ctx.live.run(box, props.module, fn, { poll: props.poll }); });
  }).catch(function () { ctx.note(box, "This live view could not be started."); });
});
