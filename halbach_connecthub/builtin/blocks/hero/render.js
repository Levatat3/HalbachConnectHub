// Block: hero. A heading (optionally typed out), a prompt line and a tagline.
HalbachSite.registerBlock("hero", function (box, props, ctx) {
  if (props.prompt) {
    var p = ctx.el("p", "hs-prompt", props.prompt);
    p.appendChild(ctx.el("span", "hs-cursor", "_"));
    box.appendChild(p);
  }
  var text = props.heading || "", h = ctx.el("h1", "hs-title", "");
  h.setAttribute("aria-label", text);
  box.appendChild(h);
  if (props.typed && !ctx.reducedMotion) {
    var i = 0;
    (function tick() { h.textContent = text.slice(0, ++i); if (i < text.length) setTimeout(tick, 38); })();
  } else h.textContent = text;
  if (props.tagline) box.appendChild(ctx.el("p", "hs-lede", props.tagline));
});
