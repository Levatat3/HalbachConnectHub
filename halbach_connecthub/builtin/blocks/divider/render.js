// Block: divider.
HalbachSite.registerBlock("divider", function (box, props, ctx) {
  if (props.label) box.appendChild(ctx.el("div", "hs-divider-label", props.label));
  else box.appendChild(ctx.el("hr", "hs-divider"));
});
