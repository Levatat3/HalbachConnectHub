// Block: text. Markdown, rendered without HTML.
HalbachSite.registerBlock("text", function (box, props, ctx) {
  box.appendChild(ctx.markdown(props.markdown || "", props.base || ""));
});
