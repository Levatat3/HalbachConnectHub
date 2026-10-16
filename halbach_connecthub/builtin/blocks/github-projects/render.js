// Block: github-projects. Public repositories read live from GitHub (cached for ten minutes per visitor).
HalbachSite.registerBlock("github-projects", function (box, props, ctx) {
  var grid = ctx.el("div", "hs-cards");
  box.appendChild(grid);
  if (!props.user) return ctx.note(grid, "Set a GitHub user for this block.");
  var key = "hs-gh:" + props.user, hide = (props.hide || []).map(function (n) { return n.toLowerCase(); });
  function show(repos) {
    grid.innerHTML = "";
    repos.filter(function (r) { return !r.archived && !r.disabled && (props.forks || !r.fork) && hide.indexOf(r.name.toLowerCase()) < 0; })
      .forEach(function (r) {
        var a = ctx.el("a", "hs-card"); a.href = r.html_url; a.target = "_blank"; a.rel = "noopener noreferrer";
        a.appendChild(ctx.el("b", null, r.name));
        a.appendChild(ctx.el("span", null, r.description || "No description yet."));
        var tags = [r.language ? String(r.language).toLowerCase() : "", r.stargazers_count ? "\u2605 " + r.stargazers_count : ""].filter(Boolean);
        if (tags.length) { a.appendChild(document.createElement("br")); tags.forEach(function (t) { a.appendChild(ctx.el("span", "hs-tag", t)); }); }
        grid.appendChild(a);
      });
    if (!grid.children.length) ctx.note(grid, "No public projects yet.");
  }
  var cached = null;
  try { cached = JSON.parse(sessionStorage.getItem(key) || "null"); } catch (e) {}
  if (cached && Date.now() - cached.t < 600000) return show(cached.d);
  ctx.note(grid, "Loading repositories from GitHub...");
  fetch("https://api.github.com/users/" + encodeURIComponent(props.user) + "/repos?per_page=100&sort=pushed")
    .then(function (r) { if (!r.ok) throw 0; return r.json(); })
    .then(function (d) { try { sessionStorage.setItem(key, JSON.stringify({ t: Date.now(), d: d })); } catch (e) {} show(d); })
    .catch(function () { ctx.note(grid, "Could not reach GitHub right now."); });
});
