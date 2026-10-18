// Block: ruffle-game. Plays a Flash file through the Ruffle emulator (loaded from a pinned CDN version).
HalbachSite.playSwf = function (box, swfUrl, autoplay) {
  var ctx = HalbachSite.ctx(null), host = ctx.el("div", "hs-screen");
  box.appendChild(host);
  ctx.loadScript("https://unpkg.com/@ruffle-rs/ruffle@0.7.1/ruffle.js").then(function () {
    var R = window.RufflePlayer && window.RufflePlayer.newest();
    if (!R) return ctx.note(host, "The Flash player failed to load.");
    var p = R.createPlayer(); host.appendChild(p);
    p.style.width = "100%"; p.style.height = "600px";
    p.load({ url: swfUrl, autoplay: autoplay || "auto", unmuteOverlay: "visible", letterbox: "on" });
  }).catch(function () { ctx.note(host, "Could not load the Flash player. Check your connection."); });
};
HalbachSite.registerBlock("ruffle-game", function (box, props, ctx) {
  if (props.swf && !props.module) return HalbachSite.playSwf(box, /^https?:/i.test(props.swf) ? props.swf : ctx.url(props.swf), props.autoplay);
  if (!props.module) return ctx.note(box, "Choose the module or file that holds the game.");
  ctx.module(props.module).then(function (m) {
    if (!m || m.type !== "game") return ctx.note(box, "That module is not a Flash game file (.swf).");
    HalbachSite.playSwf(box, ctx.url("/" + m.entry), props.autoplay);
  });
});
