// Halbach glue for Funk's theme engine (funktheme.js, loaded just before this file in the same script).
// Applies the site's Funk theme (PAGE.theme.funk, or the "Funk" preset) and maps the engine's design tokens
// onto the variables Halbach's styles read. The engine itself stays exactly as Funk wrote it.
HalbachSite.registerEffect("funktheme", function (ctx) {
  var FT = window.FunkTheme;
  if (!FT) return;
  var DEFAULT = { funkTheme: 1, name: "Funk", style: "gradient", colors: ["#462a50", "#2f0a66"], angle: 135, strength: 0.45,
                  accent: null, blur: 0, fx: "ascii", fxColor: "#c604fb", fxSpeed: 2.6, fxAmount: 0.88 };
  var theme = (ctx.theme && ctx.theme.funk) || DEFAULT;
  var vars = FT.apply(theme);
  var root = document.documentElement.style;
  var MAP = { "--bg": "--bg", "--panel": "--s1", "--text": "--text", "--muted": "--dim", "--line": "--line", "--accent": "--brand", "--accent-2": "--brand2" };
  Object.keys(MAP).forEach(function (k) { if (vars[MAP[k]]) root.setProperty(k, vars[MAP[k]]); });
  document.body.classList.add("hs-funk");
});
