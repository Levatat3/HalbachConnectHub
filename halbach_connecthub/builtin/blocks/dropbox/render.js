// Block: dropbox. Uploads to an area's inbox for review. (Arrives with areas; shows a notice until then.)
HalbachSite.registerBlock("dropbox", function (box, props, ctx) {
  box.appendChild(ctx.el("h2", null, props.title || "Send files"));
  box.appendChild(ctx.el("p", "hs-empty", "Uploads are not available on this site yet."));
});
