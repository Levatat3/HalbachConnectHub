// Retro terminal effects: falling glyphs behind the page and CRT scanlines over it.
HalbachSite.registerEffect("crt-scanlines", function () {
  var d = document.createElement("div"); d.className = "hs-fx hs-fx-scan"; d.setAttribute("aria-hidden", "true");
  document.body.appendChild(d);
});
HalbachSite.registerEffect("glyph-rain", function (ctx) {
  if (ctx.reducedMotion) return;
  var c = document.createElement("canvas"); c.className = "hs-fx hs-fx-rain"; c.setAttribute("aria-hidden", "true");
  document.body.insertBefore(c, document.body.firstChild);
  var g = c.getContext("2d"), cols = [], glyphs = "01<>/{}[]=+-*#$%&LvHBC", size = 16;
  function fit() {
    c.width = window.innerWidth; c.height = window.innerHeight;
    cols = []; for (var i = 0; i < Math.floor(c.width / size); i++) cols.push(Math.random() * c.height / size);
  }
  fit(); window.addEventListener("resize", fit);
  var color = getComputedStyle(document.documentElement).getPropertyValue("--accent").trim() || "#39ff7a";
  setInterval(function () {
    if (document.hidden) return;
    g.fillStyle = "rgba(5,8,5,.12)"; g.fillRect(0, 0, c.width, c.height);
    g.fillStyle = color; g.font = size + "px monospace";
    cols.forEach(function (y, i) {
      g.fillText(glyphs.charAt(Math.floor(Math.random() * glyphs.length)), i * size, y * size);
      cols[i] = y * size > c.height && Math.random() > .975 ? 0 : y + 1;
    });
  }, 70);
});
