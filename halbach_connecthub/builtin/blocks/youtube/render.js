// Block: youtube. Privacy-friendly embed of a video or a channel's uploads playlist.
HalbachSite.registerBlock("youtube", function (box, props, ctx) {
  var screen = ctx.el("div", "hs-screen");
  box.appendChild(screen);
  if (!props.id) return ctx.note(screen, "Set a video or playlist id for this block.");
  ctx.onVisible(function () {
    var f = ctx.el("iframe"), base = "https://www.youtube-nocookie.com/embed/";
    f.src = props.mode === "playlist" ? base + "videoseries?list=" + encodeURIComponent(props.id) : base + encodeURIComponent(props.id);
    f.title = "YouTube"; f.allowFullscreen = true; f.loading = "lazy";
    screen.appendChild(f);
  });
});
