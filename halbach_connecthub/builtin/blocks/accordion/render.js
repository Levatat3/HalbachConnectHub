// Block: accordion. A stack of <details> rows; the body is Markdown rendered with ctx.markdown.
// Everything is created with ctx.el, so text is set with textContent and raw HTML stays text.
HalbachSite.registerBlock("accordion", function (box, props, ctx) {
  var items = props.items || [];
  if (!items.length) return ctx.note(box, "Add a row: a title and the text to show when it is open.");
  var wrap = ctx.el("div", "hs-accordion");
  items.forEach(function (it) {
    var row = ctx.el("details", "hs-acc-item");
    row.appendChild(ctx.el("summary", "hs-acc-summary", it.title || "Untitled"));
    var body = ctx.el("div", "hs-acc-body");
    body.appendChild(ctx.markdown(it.text || "", ""));
    row.appendChild(body);
    wrap.appendChild(row);
  });
  box.appendChild(wrap);
});
