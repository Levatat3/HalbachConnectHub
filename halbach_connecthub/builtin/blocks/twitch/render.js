// Block: twitch. The official player; `parent` must be this page's host name.
HalbachSite.registerBlock("twitch", function (box, props, ctx) {
  var screen = ctx.el("div", "hs-screen");
  box.appendChild(screen);
  var host = location.hostname;
  if (!host || !props.channel) return ctx.note(screen, "Set a Twitch channel for this block.");
  ctx.onVisible(function () {
    var f = ctx.el("iframe");
    f.src = "https://player.twitch.tv/?channel=" + encodeURIComponent(props.channel) + "&parent=" + encodeURIComponent(host) + "&muted=" + (props.muted === false ? "false" : "true");
    f.title = "Twitch stream"; f.allowFullscreen = true;
    screen.appendChild(f);
  });
});
