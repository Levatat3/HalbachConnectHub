// Block: files. Download links for the files of a module.
HalbachSite.registerBlock("files", function (box, props, ctx) {
  ctx.module(props.module).then(function (m) {
    if (!m || m.kind !== "static") return ctx.note(box, "That module is not available.");
    var ul = ctx.el("ul", "hs-files");
    (m.files && m.files.length ? m.files : [m.entry]).forEach(function (f) {
      var li = ctx.el("li"), a = ctx.el("a", null, f.split("/").pop()); a.href = ctx.url("/" + f); a.setAttribute("download", "");
      li.appendChild(a); ul.appendChild(li);
    });
    box.appendChild(ul);
  });
});
