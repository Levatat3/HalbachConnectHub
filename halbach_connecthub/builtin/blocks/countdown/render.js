// Block: countdown. Shows the time left until a date; the numbers update once a second.
// The date is only ever read, never run; everything is drawn with ctx.el (textContent).
HalbachSite.registerBlock("countdown", function (box, props, ctx) {
  var target = Date.parse(String(props.target || "").trim());
  if (isNaN(target)) return ctx.note(box, "Set a date to count down to, for example 2027-01-01T00:00.");

  var wrap = ctx.el("div", "hs-countdown");
  if (props.label) wrap.appendChild(ctx.el("div", "hs-countdown-label", props.label));
  var row = ctx.el("div", "hs-countdown-row");
  var cells = [["Days", 86400000], ["Hours", 3600000], ["Minutes", 60000], ["Seconds", 1000]].map(function (u) {
    var cell = ctx.el("div", "hs-countdown-unit");
    var num = ctx.el("strong", "hs-countdown-num", "0");
    cell.appendChild(num);
    cell.appendChild(ctx.el("span", "hs-countdown-cap", u[0]));
    row.appendChild(cell);
    return { num: num, ms: u[1] };
  });
  wrap.appendChild(row);
  box.appendChild(wrap);

  var timer = null;
  function pad(n) { return (n < 10 ? "0" : "") + n; }
  function draw() {
    var left = target - Date.now();
    if (left <= 0) {
      if (timer) { clearInterval(timer); timer = null; }
      cells.forEach(function (c) { c.num.textContent = "0"; });
      if (props.done) { wrap.textContent = ""; wrap.appendChild(ctx.el("p", "hs-countdown-done", props.done)); }
      return;
    }
    var rest = left;
    cells.forEach(function (c) { var v = Math.floor(rest / c.ms); rest -= v * c.ms; c.num.textContent = c.ms === 1000 ? pad(v) : String(v); });
  }
  draw();
  if (!ctx.reducedMotion) timer = setInterval(draw, 1000);
  ctx.onRemove(function () { if (timer) clearInterval(timer); });
});
