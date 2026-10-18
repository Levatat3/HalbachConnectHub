// Block: image. One image from a module or an https address.
HalbachSite.registerBlock("image", function (box, props, ctx) {
  if (props.module) {
    return ctx.module(props.module).then(function (m) {
      if (!m || m.type !== "image") return ctx.note(box, "That module is not an image.");
      var im = ctx.el("img", "hs-img"); im.src = ctx.url("/" + m.entry); im.alt = props.alt || m.name; box.appendChild(im);
    });
  }
  if (props.url) { var im2 = ctx.el("img", "hs-img"); im2.src = /^\//.test(props.url) ? ctx.url(props.url) : props.url; im2.alt = props.alt || ""; im2.loading = "lazy"; box.appendChild(im2); }
  else ctx.note(box, "Choose an image module or give an address.");
});
