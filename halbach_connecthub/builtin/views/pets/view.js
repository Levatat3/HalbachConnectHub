// View: pets. Each bot's active pet from a sprite sheet module "pet-sprites", animated (0,1,2,1), with abilities and a plan.
(function () {
  var RARITY = { Common: "rc", Uncommon: "ru", Rare: "rr", Legendary: "rl", Divine: "rd" }, sheets = {};
  function sheet(base, name) {
    var k = base + name;
    if (!sheets[k]) { var im = new Image(); im.src = base + encodeURIComponent(name) + ".png"; sheets[k] = im; }
    return sheets[k];
  }
  HalbachSite.registerView("pets", function (out, d, ctx) {
    var spr = ctx.moduleSync("pet-sprites"), base = spr && spr.entry ? ctx.url("/" + spr.entry.replace(/[^\/]*$/, "")) : null;
    var bots = (d && d.bots) || [];
    out.innerHTML = "";
    var grid = ctx.el("div", "hs-petgrid"); out.appendChild(grid);
    if (!bots.length) return grid.appendChild(ctx.el("p", "hs-empty", "No bots are reporting right now."));
    bots.forEach(function (b) {
      var card = ctx.el("div", "hs-petcard"), pets = b.pets || [];
      var a = pets.filter(function (p) { return p.id === b.active; })[0] || pets.filter(function (p) { return p.id === b.best; })[0];
      var best = pets.filter(function (p) { return p.id === b.best; })[0];
      var canvas = ctx.el("canvas", "hs-petsprite"); canvas.width = 8; canvas.height = 8; card.appendChild(canvas);
      var info = ctx.el("div"); card.appendChild(info);
      var head = ctx.el("div", "hs-pethead"); head.appendChild(ctx.el("b", null, String(b.name))); info.appendChild(head);
      if (!a) head.appendChild(ctx.el("small", null, "no pets yet"));
      else {
        head.appendChild(ctx.el("span", "hs-petname " + (RARITY[a.rarity] || "rc"), String(a.name)));
        head.appendChild(ctx.el("small", null, String(a.rarity)));
        var ab = ctx.el("div", "hs-petabil");
        (a.abilities || []).forEach(function (x) {
          var chip = ctx.el("span", x.works ? "ok" : "no", x.n + " " + x.lvl + "/" + a.max);
          if (!x.works) chip.title = "does nothing on this server";
          ab.appendChild(chip);
        });
        info.appendChild(ab);
        var plan = (b.active === b.best ? "plans to keep it" : "plans to switch to " + (best ? best.name + " (" + best.rarity + ")" : "a better pet")) +
          " \u00b7 " + Math.max(0, (b.total || pets.length) - 1) + " spare" + (a.gained ? " \u00b7 +" + a.gained + " levels" : "") +
          (b.fuse ? " \u00b7 fuse ready: 2 " + b.fuse.rarity : "");
        info.appendChild(ctx.el("div", "hs-petplan", plan));
        if (base && a.sprite) canvas._sp = a.sprite;
      }
      grid.appendChild(card);
    });
    if (out._anim) clearInterval(out._anim);
    out._anim = setInterval(function () {
      if (!document.body.contains(out)) { clearInterval(out._anim); return; }
      if (out.offsetParent === null || !base) return;
      var frame = Math.floor(Date.now() / 240);
      out.querySelectorAll("canvas.hs-petsprite").forEach(function (c) {
        var sp = c._sp; if (!sp) return;
        var im = sheet(base, sp.s); if (!im.complete || !im.naturalWidth) return;
        var cyc = sp.frames.length > 2 ? [sp.frames[0], sp.frames[1], sp.frames[2], sp.frames[1]] : sp.frames;
        var g = c.getContext("2d"); g.imageSmoothingEnabled = false; g.clearRect(0, 0, 8, 8);
        g.drawImage(im, (sp.x || 0) + cyc[frame % cyc.length] * sp.w, sp.y, sp.w, sp.h, 0, 0, 8, 8);
      });
    }, 120);
  });
})();
