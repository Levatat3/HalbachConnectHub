// Block: linked-sites. Cards (or a list) for linked sites; the builder fills props.items from the owner's linked sites.
HalbachSite.registerBlock("linked-sites", function (box, props, ctx) {
  var items = props.items || [];
  if (!items.length) return ctx.note(box, "No linked sites yet.");
  var list = props.style === "list";
  var wrap = ctx.el(list ? "ul" : "div", list ? "hs-links hs-list" : "hs-cards");
  items.forEach(function (it) {
    var a = ctx.el("a", list ? null : "hs-card");
    a.href = it.href; a.target = "_blank"; a.rel = "noopener noreferrer";
    if (list) { a.textContent = it.title; var li = ctx.el("li"); li.appendChild(a); wrap.appendChild(li); return; }
    a.appendChild(ctx.el("b", null, it.title));
    if (it.text) a.appendChild(ctx.el("span", null, it.text));
    wrap.appendChild(a);
  });
  box.appendChild(wrap);
});
