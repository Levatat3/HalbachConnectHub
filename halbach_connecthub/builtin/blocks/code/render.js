// Block: code. A monospace box with a language label. The code is only ever shown, never run.
// Text goes in with ctx.el (textContent), so anything pasted in stays plain text.
HalbachSite.registerBlock("code", function (box, props, ctx) {
  var wrap = ctx.el("div", "hs-code");
  var head = ctx.el("div", "hs-code-head");
  head.appendChild(ctx.el("span", "hs-code-lang", props.lang || "code"));
  wrap.appendChild(head);
  var pre = ctx.el("pre", "hs-code-pre");
  pre.appendChild(ctx.el("code", "hs-code-body", props.code || ""));
  wrap.appendChild(pre);
  box.appendChild(wrap);
});
