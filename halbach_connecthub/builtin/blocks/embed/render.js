// Block: embed. Shows a page module or an https address inside a sandboxed frame.
HalbachSite.registerBlock("embed", function (box, props, ctx) {
  function frame(src, title, own) {
    var f = ctx.el("iframe", "hs-frame"); f.src = src; f.title = title; f.loading = "lazy";
    f.style.height = (props.height || 560) + "px";
    // The owner's own pages (a path on this site) run as normal; modules and outside addresses stay sandboxed.
    // sameorigin is added by the builder only, for an address on an area's owner-approved host list. Such a frame is a
    // different origin from this site, so allow-same-origin gives it its own storage without any access to this page.
    if (!own) f.setAttribute("sandbox", "allow-scripts allow-popups allow-forms" + (props.sameorigin ? " allow-same-origin" : ""));
    var screen = ctx.el("div", "hs-screen"); screen.appendChild(f); box.appendChild(screen);
  }
  if (props.module) {
    ctx.module(props.module).then(function (m) {
      if (!m || m.type !== "page") return ctx.note(box, "That module is not a page.");
      frame(ctx.url("/" + m.entry), m.name);
    });
  } else if (props.url) frame(/^\//.test(props.url) ? ctx.url(props.url) : props.url, "Embedded page", /^\//.test(props.url));
  else ctx.note(box, "Choose a page module or give an address.");
});
