// Block: links. Buttons or a plain list.
HalbachSite.registerBlock("links", function (box, props, ctx) {
  var ul = ctx.el("ul", "hs-links " + (props.style === "list" ? "hs-list" : "hs-links-buttons"));
  (props.items || []).forEach(function (l) {
    var li = ctx.el("li"), a = ctx.el("a", null, l.label);
    a.href = l.url;
    if (/^https?:/i.test(l.url)) { a.target = "_blank"; a.rel = "noopener noreferrer"; }
    li.appendChild(a); ul.appendChild(li);
  });
  box.appendChild(ul);
});
