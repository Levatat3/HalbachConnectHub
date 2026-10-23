/* FunkTheme: shared theme engine + theme maker for Funk's apps (Claude Watch first; FunkDrop, Game Mode later).
 *
 * A theme is a small JSON object (saved as *.funktheme in %LOCALAPPDATA%\FunkThemes so every app sees the
 * same library):
 *   { funkTheme: 1, name, style: "solid"|"gradient"|"rainbow"|"image", colors: [hex, ...], angle,
 *     strength: 0..1 (how much the colour shows through), accent: null | [hex, hex],
 *     image: dataURL, blur: px, tint: hex (average colour of the image),
 *     fx: null | "ascii"|"matrix"|"synthwave"|"stars"|"rain" (animated background), fxSpeed: 0.2..3, fxAmount: 0..1,
 *     fxColor: hex (effect colour; default = the accent) }
 * FunkTheme.apply(theme) turns it into the design tokens every Funk app already uses
 * (--bg --s1 --s2 --s3 --line --line2 --text --dim --faint --brand --grad --ok --bad --gold --blue)
 * plus --page (the background layer). Surfaces are translucent, so gradients and pictures show through
 * like Discord's app themes.
 *
 * FunkTheme.openEditor({ current, library, onChange, onSave, onDelete, onExport, onImport, onDone }) shows the maker.
 *
 * FunkThemes 2 (same engine, still reads every v1 theme):
 *  - A theme may be a zip (theme.json + files). The app's themes.py unpacks it and serves the files; the theme then has
 *    assets: "<folder>", the app sets FunkTheme.assetBase, and file names in the theme ("image": "asset:bg.jpg",
 *    "preview": "preview.jpg") resolve through FunkTheme.asset(theme, name).
 *  - Effects register themselves with FunkTheme.registerFx(name, { label, controls, draw(g, P) }); the editor builds
 *    each effect's panel from its controls (slider / color / toggle / choice, showIf hides one until a toggle is on).
 *    speed, amount and color are shared by every effect and still save as fxSpeed / fxAmount / fxColor; an effect's
 *    own controls save in fxValues. P.v holds all the values.
 *  - A theme only gets funkTheme: 2 when it needs it (asset files), so older apps keep reading the rest.
 */
(function () {
  const RAINBOW = ["#ff5f6d", "#ffb45e", "#f9f871", "#4ade80", "#38bdf8", "#8b5cf6", "#f472b6"];
  const BASE = "#0d0e11";

  const PRESETS = [
    { name: "Claude Watch", style: "solid", colors: ["#0e0f12"], strength: 0, accent: ["#f5a25d", "#e8615b"] },
    { name: "FunkDrop", style: "solid", colors: ["#0e0f12"], strength: 0, accent: ["#ff7a3d", "#ff3d7f"] },
    { name: "Game Mode", style: "solid", colors: ["#0e0f12"], strength: 0, accent: ["#3ee07f", "#1fc8e0"] },
    { name: "Midnight", style: "solid", colors: ["#1d2b53"], strength: 0.6 },
    { name: "Sunset", style: "gradient", colors: ["#ff7a3d", "#ff3d7f", "#7c3aed"], angle: 135, strength: 0.55 },
    { name: "Aurora", style: "gradient", colors: ["#10b981", "#06b6d4", "#8b5cf6"], angle: 160, strength: 0.5 },
    { name: "Ocean", style: "gradient", colors: ["#0ea5e9", "#1e3a8a"], angle: 180, strength: 0.55 },
    { name: "Cherry", style: "gradient", colors: ["#fb7185", "#831843"], angle: 150, strength: 0.5 },
    { name: "Forest", style: "gradient", colors: ["#4ade80", "#14532d"], angle: 170, strength: 0.5 },
    { name: "Ember", style: "gradient", colors: ["#fb923c", "#7f1d1d"], angle: 160, strength: 0.55 },
    { name: "Lavender", style: "gradient", colors: ["#c4b5fd", "#4c1d95"], angle: 145, strength: 0.5 },
    { name: "Rainbow", style: "rainbow", colors: [], angle: 120, strength: 0.45 },
    { name: "Funk Studio", style: "solid", colors: ["#07090c"], strength: 0, accent: ["#3dff8a", "#4de1ff"] },
    { name: "Terminal Mist", style: "solid", colors: ["#1a1a1a"], strength: 0.35, accent: ["#d97757", "#e8a27c"], fx: "ascii", fxColor: "#9a9a9a", fxSpeed: 1, fxAmount: 0.6 },
    { name: "Green Code", style: "solid", colors: ["#030a06"], strength: 0.45, accent: ["#3dff8a", "#1fd6a0"], fx: "ascii", fxColor: "#3dff8a", fxSpeed: 1, fxAmount: 0.55 },
    { name: "Amber Terminal", style: "solid", colors: ["#120c05"], strength: 0.45, accent: ["#ffb347", "#ff8c42"], fx: "ascii", fxColor: "#ffb347", fxSpeed: 0.9, fxAmount: 0.55 },
    { name: "Matrix", style: "solid", colors: ["#010a04"], strength: 0.55, accent: ["#3dff8a", "#00c853"], fx: "matrix", fxSpeed: 1, fxAmount: 0.75 },
    { name: "Synthwave", style: "gradient", colors: ["#1a0b3d", "#45115c", "#0b0820"], angle: 180, strength: 0.6, accent: ["#ff2e97", "#2de2e6"], fx: "synthwave", fxSpeed: 1, fxAmount: 0.7 },
    { name: "Starfield", style: "solid", colors: ["#04050f"], strength: 0.45, accent: ["#8ab4ff", "#c084fc"], fx: "stars", fxSpeed: 1, fxAmount: 0.6 },
    { name: "Rainy Night", style: "gradient", colors: ["#0c1b30", "#05070d"], angle: 170, strength: 0.55, accent: ["#5eb8ff", "#7dd3fc"], fx: "rain", fxSpeed: 1, fxAmount: 0.6 },
  ].map((p) => ({ funkTheme: 1, angle: 135, accent: null, blur: 0, fx: null, ...p }));

  // ---------------------------------------------------------------- colour maths
  const clamp = (x, a, b) => Math.max(a, Math.min(b, x));
  function rgb(hex) {
    let h = String(hex || "#000").replace("#", "");
    if (h.length === 3) h = h.split("").map((c) => c + c).join("");
    const n = parseInt(h.slice(0, 6), 16) || 0;
    return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
  }
  const hex = ([r, g, b]) => "#" + [r, g, b].map((v) => clamp(Math.round(v), 0, 255).toString(16).padStart(2, "0")).join("");
  const mix = (a, b, t) => { const x = rgb(a), y = rgb(b); return hex(x.map((v, i) => v + (y[i] - v) * t)); };
  const rgba = (h, a) => { const [r, g, b] = rgb(h); return `rgba(${r},${g},${b},${a})`; };
  function toHsl(h) {
    let [r, g, b] = rgb(h).map((v) => v / 255);
    const mx = Math.max(r, g, b), mn = Math.min(r, g, b), l = (mx + mn) / 2;
    if (mx === mn) return [0, 0, l];
    const d = mx - mn, s = l > 0.5 ? d / (2 - mx - mn) : d / (mx + mn);
    const hh = mx === r ? (g - b) / d + (g < b ? 6 : 0) : mx === g ? (b - r) / d + 2 : (r - g) / d + 4;
    return [hh * 60, s, l];
  }
  function fromHsl(hh, s, l) {
    hh = ((hh % 360) + 360) % 360 / 360;
    if (s === 0) return hex([l * 255, l * 255, l * 255]);
    const q = l < 0.5 ? l * (1 + s) : l + s - l * s, p = 2 * l - q;
    const f = (t) => { t = (t + 1) % 1; return t < 1 / 6 ? p + (q - p) * 6 * t : t < 1 / 2 ? q : t < 2 / 3 ? p + (q - p) * (2 / 3 - t) * 6 : p; };
    return hex([f(hh + 1 / 3) * 255, f(hh) * 255, f(hh - 1 / 3) * 255]);
  }
  const avg = (list) => hex([0, 1, 2].map((i) => list.reduce((s, c) => s + rgb(c)[i], 0) / list.length));
  /** Make a colour bright and saturated enough to use as an accent on a dark background. */
  function vivid(h) {
    const [hh, s, l] = toHsl(h);
    return fromHsl(hh, Math.max(s, 0.55), clamp(l, 0.58, 0.72));
  }

  // ---------------------------------------------------------------- theme -> tokens
  function stops(t) {
    if (t.style === "rainbow") return RAINBOW;
    const c = (t.colors || []).filter(Boolean);
    return c.length ? c : ["#0e0f12"];
  }
  function derive(t) {
    const cols = stops(t);
    const pic = t.style === "image" || t.style === "video" || t.style === "web";
    const tint = pic ? (t.tint || "#20242c") : avg(cols);
    const s = clamp(t.strength == null ? 0.5 : Number(t.strength), 0, 1);
    const angle = (t.angle == null ? 135 : t.angle) + "deg";

    // Accent: explicit, else the most colourful stop, paired with the next one (or a hue step away).
    let acc = t.accent && t.accent.length ? t.accent : null;
    if (!acc) {
      const src = pic ? [t.tint || "#e8915b"] : cols;
      const ranked = [...src].sort((a, b) => { const A = toHsl(a), B = toHsl(b); return B[1] * (1 - Math.abs(B[2] - 0.55)) - A[1] * (1 - Math.abs(A[2] - 0.55)); });
      const a0 = vivid(ranked[0]);
      const a1 = ranked[1] && toHsl(ranked[1])[1] > 0.25 ? vivid(ranked[1]) : fromHsl(toHsl(a0)[0] + 35, toHsl(a0)[1], toHsl(a0)[2]);
      acc = [a0, a1];
    }
    const brand = vivid(acc[0]);
    const grad = t.style === "rainbow" && !t.accent
      ? `linear-gradient(135deg, ${RAINBOW.join(", ")})`
      : `linear-gradient(135deg, ${acc[0]} 0%, ${acc[1] || acc[0]} 100%)`;

    // Background layer: the colour/picture under a dark veil. strength 0 = plain dark, 1 = loud.
    const veil = t.style === "image" ? 1 - 0.7 * s : 1 - 0.62 * s;
    const img = imageUrl(t);
    const layer = t.style === "image" && img ? `url("${img}") center / cover no-repeat`
      : t.style === "video" || t.style === "web" ? BASE   // the video / page itself is a layer above #page-bg
      : t.style === "solid" ? cols[0]
      : `linear-gradient(${angle}, ${cols.join(", ")})`;
    const page = `linear-gradient(${rgba(BASE, veil)}, ${rgba(BASE, veil)}), ${layer}`;

    // Surfaces: dark, nudged toward the theme colour, translucent so the background reads through.
    const k = 0.08 + 0.12 * s;
    const glass = s > 0 || pic;
    const surf = (base, a) => glass ? rgba(mix(base, tint, k), a) : mix(base, tint, k);
    return {
      "--bg": mix("#0e0f12", tint, 0.06 * s),
      "--page": page,
      "--page-blur": (t.style === "image" ? Number(t.blur) || 0 : 0) + "px",
      "--s1": surf("#15171c", 0.78), "--s2": surf("#1c1f26", 0.84), "--s3": surf("#252932", 0.9),
      "--solid": mix("#15171c", tint, k),
      "--deep": mix("#050506", tint, 0.06 + 0.14 * s),   // darkest backdrop, for full-bleed canvases (the Brain's sky)
      "--line": glass ? "rgba(255,255,255,.08)" : "#2a2e37", "--line2": glass ? "rgba(255,255,255,.14)" : "#353a45",
      "--text": "#eef0f4", "--dim": mix("#8b92a0", tint, 0.12), "--faint": mix("#5d6472", tint, 0.12),
      "--brand": brand, "--brand2": vivid(acc[1] || acc[0]), "--grad": grad,
      "--brand-soft": rgba(brand, 0.16), "--brand-line": rgba(brand, 0.45),
      "--ok": "#34d399", "--bad": "#f87171", "--gold": "#fbbf24", "--blue": "#60a5fa",
    };
  }
  function apply(t, root) {
    const vars = derive(t || PRESETS[0]);
    const el = root || document.documentElement;
    for (const [k, v] of Object.entries(vars)) el.style.setProperty(k, v);
    if (!root && document.body) layersRun(t || PRESETS[0], vars);   // video + animated effects (none for older themes)
    return vars;
  }
  // ---- FunkThemes 2 asset files: "asset:bg.jpg" (or a bare file name in preview) -> the app's local asset server
  function asset(t, name) {
    const base = window.FunkTheme && window.FunkTheme.assetBase;
    const dir = t && /^we:\d+$/.test(t.link || "") ? "we-" + t.link.slice(3) : t && t.assets;   // imported: plays from the Workshop folder
    if (!dir || !base || !name) return null;
    return base + encodeURIComponent(dir) + "/" + String(name).split("/").map(encodeURIComponent).join("/");
  }
  function imageUrl(t) {
    if (!t || !t.image) return null;
    return String(t.image).startsWith("asset:") ? asset(t, t.image.slice(6)) : t.image;
  }
  /** CSS background for a small swatch of a theme. */
  function swatch(t) {
    const cols = stops(t);
    const prev = t.preview && asset(t, t.preview);
    if (prev) return `url("${prev}") center / cover`;
    if (t.style === "image" && imageUrl(t)) return `url("${imageUrl(t)}") center / cover`;
    if (t.fx && t.fx !== "none" && t.accent) return `repeating-linear-gradient(${t.fx === "rain" ? 100 : 90}deg, ${t.accent[0]}55 0 2px, transparent 2px 7px), ${t.style === "gradient" ? `linear-gradient(${t.angle || 135}deg, ${cols.join(", ")})` : cols[0]}`;
    if (t.style === "solid" && (!t.strength || t.strength === 0) && t.accent)
      return `linear-gradient(135deg, #15171c 0 55%, ${t.accent[0]} 55% 78%, ${t.accent[1] || t.accent[0]} 78%)`;
    if (t.style === "solid") return cols[0];
    return `linear-gradient(${t.angle || 135}deg, ${cols.join(", ")})`;
  }

  // ---------------------------------------------------------------- pictures
  /** Shrink a picked image to <= 1920 px wide (keeps themes small) and find its average colour. */
  function loadImage(file) {
    return new Promise((resolve, reject) => {
      const fr = new FileReader();
      fr.onerror = () => reject(new Error("Couldn't read that file."));
      fr.onload = () => {
        const img = new Image();
        img.onerror = () => reject(new Error("That file isn't a picture this app can read (use PNG, JPG, GIF or WebP)."));
        img.onload = () => {
          const w = Math.min(1920, img.naturalWidth), h = Math.round(img.naturalHeight * (w / img.naturalWidth));
          const c = document.createElement("canvas"); c.width = w; c.height = h;
          c.getContext("2d").drawImage(img, 0, 0, w, h);
          const small = document.createElement("canvas"); small.width = 12; small.height = 12;
          const sx = small.getContext("2d"); sx.drawImage(img, 0, 0, 12, 12);
          const d = sx.getImageData(0, 0, 12, 12).data;
          let r = 0, g = 0, b = 0;
          for (let i = 0; i < d.length; i += 4) { r += d[i]; g += d[i + 1]; b += d[i + 2]; }
          const n = d.length / 4;
          const isPng = /png$/i.test(file.type);
          resolve({ image: c.toDataURL(isPng ? "image/png" : "image/jpeg", 0.86), tint: hex([r / n, g / n, b / n]) });
        };
        img.src = fr.result;
      };
      fr.readAsDataURL(file);
    });
  }

  // ---------------------------------------------------------------- the theme maker
  const CSS = `
  .ft-bg{position:fixed;inset:0;background:rgba(0,0,0,.5);display:flex;justify-content:flex-end;z-index:50}
  .ft{width:min(430px,100vw);height:100%;background:var(--solid);border-left:1px solid var(--line2);display:flex;flex-direction:column;
      box-shadow:-20px 0 60px -20px rgba(0,0,0,.6);color:var(--text);font-size:13px}
  .ft-head{display:flex;align-items:center;gap:10px;padding:16px 18px;border-bottom:1px solid var(--line)}
  .ft-head h3{margin:0;font-size:16px;flex:1}
  .ft-body{flex:1;overflow-y:auto;padding:4px 18px 18px}
  .ft-sec{margin-top:16px}
  .ft-sec>label,.ft-lab{display:block;font-size:11px;font-weight:600;letter-spacing:1px;text-transform:uppercase;color:var(--dim);margin-bottom:8px}
  .ft-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(58px,1fr));gap:24px 9px;margin-bottom:14px}
  .ft-sw{position:relative;aspect-ratio:1;border-radius:14px;border:2px solid transparent;cursor:pointer;box-shadow:inset 0 0 0 1px rgba(255,255,255,.12)}
  .ft-sw.on{border-color:var(--text)}
  .ft-sw span{position:absolute;left:0;right:0;bottom:-17px;font-size:10.5px;color:var(--dim);text-align:center;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
  .ft-sw .x{position:absolute;top:-6px;right:-6px;width:18px;height:18px;border-radius:50%;background:#000c;color:#fff;border:1px solid var(--line2);
      font-size:11px;line-height:15px;text-align:center;display:none;bottom:auto;left:auto}
  .ft-sw:hover .x{display:block}
  .ft-seg{display:flex;background:var(--s2);border:1px solid var(--line);border-radius:11px;padding:3px;gap:2px}
  .ft-seg button{flex:1;border:0;background:none;color:var(--dim);padding:7px 0;border-radius:8px;cursor:pointer;font:inherit;font-size:12.5px}
  .ft-seg button.on{background:var(--s3);color:var(--text);box-shadow:0 1px 0 rgba(255,255,255,.06) inset}
  .ft-cols{display:flex;gap:10px;align-items:center;flex-wrap:wrap}
  .ft-col{position:relative;width:46px;height:46px;border-radius:12px;overflow:hidden;box-shadow:inset 0 0 0 1px rgba(255,255,255,.15)}
  .ft-col input{position:absolute;inset:-8px;width:62px;height:62px;border:0;padding:0;cursor:pointer;background:none}
  .ft-mini{border:1px dashed var(--line2);background:none;color:var(--dim);border-radius:12px;width:46px;height:46px;cursor:pointer;font-size:18px}
  .ft-row{display:flex;align-items:center;gap:10px;margin-top:10px}
  .ft-row .ft-name{width:72px;color:var(--dim);flex:none}
  .ft-row input[type=range]{flex:1;accent-color:var(--brand)}
  .ft-row .ft-val{width:40px;text-align:right;color:var(--faint);font-variant-numeric:tabular-nums}
  .ft-preview{margin-top:14px;border-radius:14px;overflow:hidden;border:1px solid var(--line);position:relative;height:118px}
  .ft-preview .pg{position:absolute;inset:0;background:var(--page);filter:blur(var(--page-blur));transform:scale(1.08)}
  .ft-preview .ft-pc{position:absolute;left:14px;right:14px;top:14px;background:var(--s1);border:1px solid var(--line);border-radius:12px;padding:11px 12px}
  .ft-preview .t{font-weight:700;font-size:14px}.ft-preview .t b{background:var(--grad);-webkit-background-clip:text;background-clip:text;color:transparent}
  .ft-preview .d{color:var(--dim);font-size:12px;margin:3px 0 9px}
  .ft-preview .b{display:inline-block;background:var(--grad);color:#fff;font-weight:600;font-size:12px;border-radius:9px;padding:5px 12px}
  .ft-foot{display:flex;gap:8px;padding:14px 18px;border-top:1px solid var(--line);flex-wrap:wrap}
  .ft-foot input{flex:1;min-width:120px;background:var(--s2);color:var(--text);border:1px solid var(--line);border-radius:10px;padding:8px 10px;font:inherit}
  .ft-btn{border:0;border-radius:10px;padding:8px 13px;font:inherit;font-weight:600;font-size:12.5px;cursor:pointer;color:#fff;background:var(--grad)}
  .ft-btn.soft{background:var(--s3);color:var(--text)}
  .ft-btn.ghost{background:none;color:var(--dim);border:1px solid var(--line2)}
  .ft-note{color:var(--faint);font-size:11.5px;margin-top:8px}
  .ft{position:relative}
  .ft-we{position:absolute;inset:0;background:var(--solid);z-index:3;display:flex;flex-direction:column}
  .ft-we-top{display:flex;gap:8px;align-items:center;padding:14px 18px 10px;border-bottom:1px solid var(--line);flex-wrap:wrap}
  .ft-we-top h3{margin:0;font-size:15px;flex:1}
  .ft-we-grid{flex:1;overflow-y:auto;display:grid;grid-template-columns:1fr 1fr;gap:10px;padding:12px 18px;align-content:start}
  .ft-we-card{cursor:pointer;border-radius:12px;overflow:hidden;background:var(--s2);border:1px solid var(--line)}
  .ft-we-card:hover{border-color:var(--brand-line)}
  .ft-we-card img{width:100%;aspect-ratio:16/9;object-fit:cover;display:block;background:#000}
  .ft-we-card div{padding:6px 8px;font-size:12px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
  .ft-we-card b{font-weight:600;font-size:10px;letter-spacing:.6px;text-transform:uppercase;color:var(--dim);margin-right:6px}
  .ft-txt{flex:1;background:var(--s2);color:var(--text);border:1px solid var(--line);border-radius:9px;padding:6px 8px;font:inherit}
  .ft-sec summary{list-style:none}.ft-sec summary::-webkit-details-marker{display:none}
  .ft-layer{margin-top:14px;padding:4px 12px 12px;border:1px solid var(--line);border-radius:12px;background:var(--s1)}
  .ft-mini.sm{width:30px;height:30px;font-size:12px;flex:none}
  .ft-sel{flex:1;background:var(--s2);color:var(--text);border:1px solid var(--line);border-radius:9px;padding:6px 8px;font:inherit}
  .ft-toggle{display:flex;align-items:center;gap:8px;color:var(--dim);cursor:pointer;margin-top:10px}`;

  function openEditor(opt) {
    if (!document.getElementById("ft-css")) {
      const st = document.createElement("style"); st.id = "ft-css"; st.textContent = CSS; document.head.appendChild(st);
    }
    const original = JSON.parse(JSON.stringify(opt.current || PRESETS[0]));
    let t = JSON.parse(JSON.stringify(original));
    let library = opt.library || [];
    const bg = document.createElement("div");
    bg.className = "ft-bg";
    document.body.appendChild(bg);
    const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
    const same = (a, b) => a.name === b.name;
    const change = () => { apply(t); opt.onChange && opt.onChange(t); draw(); };

    function draw() {
      const cols = t.style === "gradient" ? (t.colors.length >= 2 ? t.colors : [...t.colors, "#7c3aed"].slice(0, 2)) : t.colors;
      if (t.style === "gradient") t.colors = cols;
      bg.innerHTML = `
      <div class="ft">
        <div class="ft-head"><h3>Theme</h3><button class="ft-btn ghost" data-a="close">Done</button></div>
        <div class="ft-body">
          <div class="ft-preview"><div class="pg"></div><div class="ft-pc">
            <div class="t">${esc((opt.brand || ["Claude", "Watch"])[0])} <b>${esc((opt.brand || ["Claude", "Watch"])[1])}</b></div><div class="d">This is how cards and buttons will look.</div><span class="b">Button</span></div></div>

          <div class="ft-sec"><label>Presets</label><div class="ft-grid">
            ${PRESETS.map((p, i) => `<div class="ft-sw ${same(p, t) ? "on" : ""}" data-p="${i}" style="background:${swatch(p)}"><span>${esc(p.name)}</span></div>`).join("")}
          </div></div>
          ${library.length ? `<div class="ft-sec" style="margin-top:26px"><label>Your themes</label><div class="ft-grid">
            ${library.map((p, i) => `<div class="ft-sw ${same(p, t) ? "on" : ""}" data-l="${i}" style="background:${swatch(p)}"><span>${esc(p.name)}</span><b class="x" data-del="${i}" title="Delete">✕</b></div>`).join("")}
          </div></div>` : ""}

          <div class="ft-sec" style="margin-top:28px"><label>Background</label>
            <div class="ft-seg">${["solid", "gradient", "rainbow", "image", ...(opt.onPickVideo || t.style === "video" ? ["video"] : []), ...(t.style === "web" ? ["web"] : [])].map((s) => `<button data-style="${s}" class="${t.style === s ? "on" : ""}">${{ solid: "Solid", gradient: "Gradient", rainbow: "Rainbow", image: "Picture", video: "Video", web: "Web page" }[s]}</button>`).join("")}</div>
            ${t.style === "solid" ? `<div class="ft-cols" style="margin-top:12px">${colorBox(0, t.colors[0] || "#1d2b53")}</div>` : ""}
            ${t.style === "gradient" ? `<div class="ft-cols" style="margin-top:12px">${t.colors.map((c, i) => colorBox(i, c)).join("")}
              ${t.colors.length < 3 ? `<button class="ft-mini" data-a="addc" title="Add a third colour">+</button>` : `<button class="ft-mini" data-a="rmc" title="Back to two colours">−</button>`}</div>` : ""}
            ${t.style === "image" ? `<div class="ft-row"><button class="ft-btn soft" data-a="pick">${t.image ? "Change picture…" : "Choose a picture…"}</button>
              <input type="file" id="ft-file" accept="image/png,image/jpeg,image/gif,image/webp" hidden></div>
              <div class="ft-note">PNG, JPG, GIF or WebP. Big pictures are shrunk to 1920 px wide so themes stay small.</div>
              ${slider("blur", "Blur", 0, 30, 1, t.blur || 0, "px")}` : ""}
            ${t.style === "video" ? `<div class="ft-row"><button class="ft-btn soft" data-a="pickvid">${t.video ? "Change video…" : "Choose a video…"}</button></div>
              <div class="ft-note">MP4 or WebM, played muted on a loop. It stops while the window is hidden or a fullscreen game is in front.</div>
              ${slider("blur", "Blur", 0, 30, 1, t.blur || 0, "px")}` : ""}
            ${t.style === "web" ? slider("blur", "Blur", 0, 30, 1, t.blur || 0, "px") : ""}
            ${t.style === "video" || t.style === "web" ? slider("sound", "Sound", 0, 100, 1, Math.round((t.sound ?? 0) * 100), "%") : ""}
            ${t.imported ? `<div class="ft-note">From Wallpaper Engine: “${esc(t.imported.title)}”. It plays from your Workshop folder and belongs to its author, so it stays on this PC (it can't be exported).</div>` : ""}
            ${opt.onWeList ? `<button class="ft-btn soft" data-a="weimport" style="margin-top:12px">Import from Wallpaper Engine…</button>` : ""}
            ${t.style === "gradient" || t.style === "rainbow" ? slider("angle", "Angle", 0, 360, 5, t.angle ?? 135, "°") : ""}
            ${slider("strength", "Strength", 0, 100, 1, Math.round((t.strength ?? 0.5) * 100), "%")}
          </div>

          ${t.style === "web" && t.weProps && Object.keys(t.weProps).length ? `<div class="ft-sec"><details ${weOpen ? "open" : ""} data-wedet="1">
            <summary class="ft-lab" style="cursor:pointer">Wallpaper settings (${Object.keys(t.weProps).length})</summary>${weControls(t)}</details></div>` : ""}

          <div class="ft-sec"><label>Effect</label>
            <div class="ft-seg">${Object.entries(FX).map(([k, n]) => `<button data-fx="${k}" class="${(t.fx || "none") === k ? "on" : ""}">${n}</button>`).join("")}</div>
            ${t.fx && FXREG[t.fx] ? fxControls(t, -1) + (t.fxLayers || []).map(layerBlock).join("")
              + ((t.fxLayers || []).length < 2 ? `<button class="ft-btn soft" data-a="addlayer" style="margin-top:12px">+ Add an effect layer</button>` : "") : ""}
          </div>

          <div class="ft-sec"><label>Accent</label>
            <div><label class="ft-toggle"><input type="checkbox" data-a="autoacc" ${t.accent ? "" : "checked"}> Match the background automatically</label></div>
            ${t.accent ? `<div class="ft-cols" style="margin-top:10px">${colorBox("a0", t.accent[0])}${colorBox("a1", t.accent[1] || t.accent[0])}</div>` : ""}
          </div>

          <div class="ft-sec"><label>Motion</label>
            <div class="ft-seg">${[15, 30, 60].map((f) => `<button data-fps="${f}" class="${motion(t).fps === f ? "on" : ""}">${f} fps</button>`).join("")}</div>
            ${opt.onSetMuteAll ? `<div><label class="ft-toggle"><input type="checkbox" data-muteall="1" ${window.FunkTheme.muteAll ? "checked" : ""}> Mute all wallpaper sound (every theme)</label></div>` : ""}
            <div><label class="ft-toggle"><input type="checkbox" data-mo="pauseFullscreen" ${motion(t).pauseFullscreen ? "checked" : ""}> Pause while a fullscreen game or video is in front</label></div>
            <div><label class="ft-toggle"><input type="checkbox" data-mo="half" ${motion(t).half ? "checked" : ""}> Draw effects at half resolution (lighter on the GPU)</label></div>
            <div><label class="ft-toggle"><input type="checkbox" data-mo="drift" ${motion(t).drift ? "checked" : ""}> Background drifts with the mouse</label></div>
            ${opt.audio && opt.audio.available ? `<div><label class="ft-toggle"><input type="checkbox" data-mo="listen" ${motion(t).listen ? "checked" : ""}> React to music playing on this PC</label></div>
              ${motion(t).listen ? `<div class="ft-row"><span class="ft-name">Listen to</span><select class="ft-sel" data-adev="1"><option value="">Default output</option>${(opt.audio.devices || []).map((d) => `<option value="${esc(d)}" ${opt.audio.device === d ? "selected" : ""}>${esc(d)}</option>`).join("")}</select></div>
              <div class="ft-note">Not moving with your music? Pick the headphones or speakers you actually hear it on.</div>` : ""}` : ""}
          </div>
        </div>
        <div class="ft-foot">
          <input id="ft-name" value="${esc(t.name || "")}" placeholder="Theme name">
          <button class="ft-btn" data-a="save">Save</button>
          <button class="ft-btn soft" data-a="export" title="Save a .funktheme file you can send to someone">Export</button>
          <button class="ft-btn soft" data-a="import" title="Load a .funktheme file">Import</button>
          <input type="file" id="ft-import" accept=".funktheme,.json,application/json" hidden>
          <button class="ft-btn ghost" data-a="cancel">Cancel</button>
        </div>
      </div>`;
    }
    const colorBox = (i, c) => `<div class="ft-col" style="background:${c}"><input type="color" data-c="${i}" value="${c}"></div>`;
    const slider = (key, label, min, max, step, val, unit) =>
      `<div class="ft-row"><span class="ft-name">${label}</span><input type="range" data-k="${key}" min="${min}" max="${max}" step="${step}" value="${val}"><span class="ft-val">${val}${unit}</span></div>`;
    // An effect's panel, built from the controls it registered (shared speed / amount / colour first).
    const shown = (c) => c.type === "slider" && c.pct ? Math.round(c.v * 100) + "%" : c.type === "slider" ? (+c.v).toFixed(c.step < 1 ? (c.step < 0.1 ? 2 : 1) : 0) + (c.unit || "") : "";
    const BLENDS = [["normal", "Normal"], ["screen", "Screen (glows)"], ["lighten", "Lighten"], ["overlay", "Overlay"], ["multiply", "Multiply (darkens)"]];
    const layerOf = (li) => (li < 0 ? t : (t.fxLayers || [])[li]);
    function layerBlock(l, i) {
      const op = l.opacity ?? 0.8;
      return `<div class="ft-layer">
        <div class="ft-row"><span class="ft-name">Layer ${i + 2}</span><select class="ft-sel" data-lfx="${i}">${Object.entries(FX).filter(([k]) => k !== "none").map(([k, n]) => `<option value="${k}" ${l.fx === k ? "selected" : ""}>${esc(n)}</option>`).join("")}</select>
          <button class="ft-mini sm" data-rmlayer="${i}" title="Remove this layer">✕</button></div>
        <div class="ft-row"><span class="ft-name">Opacity</span><input type="range" data-lop="${i}" min="0.1" max="1" step="0.01" value="${op}"><span class="ft-val">${Math.round(op * 100)}%</span></div>
        <div class="ft-row"><span class="ft-name">Blend</span><select class="ft-sel" data-lbl="${i}">${BLENDS.map(([k, n]) => `<option value="${k}" ${(l.blend || "normal") === k ? "selected" : ""}>${n}</option>`).join("")}</select></div>
        ${fxControls(l, i)}</div>`;
    }
    function fxControls(o, li) {
      const vals = fxValues(o), ctl = fxControlList(o.fx);
      return Object.entries(ctl).map(([k, c]) => {
        if (c.showIf && (c.showIf.includes(":") ? String(vals[c.showIf.split(":")[0]]) !== c.showIf.split(":")[1] : !vals[c.showIf])) return "";
        const v = vals[k], lab = esc(c.label || k);
        if (c.type === "slider") return `<div class="ft-row"><span class="ft-name">${lab}</span><input type="range" data-fxl="${li}" data-fxk="${k}" min="${c.min}" max="${c.max}" step="${c.step || 0.01}" value="${v}"><span class="ft-val">${shown({ ...c, v })}</span></div>`;
        if (c.type === "color") return `<div class="ft-row"><span class="ft-name">${lab}</span><div class="ft-cols">${colorBox("fx:" + li + ":" + k, v || derive(t)["--brand"])}</div></div>`;
        if (c.type === "toggle") return `<div><label class="ft-toggle"><input type="checkbox" data-fxl="${li}" data-fxk="${k}" ${v ? "checked" : ""}> ${lab}</label></div>`;
        if (c.type === "choice") return `<div class="ft-row"><span class="ft-name">${lab}</span><select data-fxl="${li}" data-fxk="${k}" class="ft-sel">${(c.options || []).map((o) => `<option value="${esc(o.value)}" ${String(o.value) === String(v) ? "selected" : ""}>${esc(o.label)}</option>`).join("")}</select></div>`;
        return "";
      }).join("");
    }
    let weOpen = false;
    // Wallpaper Engine conditions ("a.value", "!a.value", "a.value == 2 && b.value") checked by a tiny parser:
    // they come from someone else's project.json, so they are never run as code. Unreadable -> show the control.
    function weCond(expr, vals) {
      const toks = String(expr).match(/\s*(===|!==|==|!=|<=|>=|&&|\|\||[!<>()]|-?\d+(?:\.\d+)?|'[^']*'|"[^"]*"|[A-Za-z_][\w.]*)/g);
      if (!toks || toks.join("").replace(/\s/g, "") !== String(expr).replace(/\s/g, "")) return true;
      let i = 0;
      const peek = () => (toks[i] || "").trim(), next = () => (toks[i++] || "").trim();
      function atom() {
        const tk = next();
        if (tk === "!") return !atom();
        if (tk === "(") { const v = or(); next(); return v; }
        if (/^-?\d/.test(tk)) return Number(tk);
        if (/^['"]/.test(tk)) return tk.slice(1, -1);
        if (tk === "true" || tk === "false") return tk === "true";
        const m = tk.match(/^(\w+)\.value$/);
        if (m) return vals[m[1]];
        throw new Error("?");
      }
      function cmp() {
        let a = atom();
        while (/^(===|!==|==|!=|<=|>=|<|>)$/.test(peek())) {
          const op = next(), b = atom();
          a = op === "==" || op === "===" ? a == b : op === "!=" || op === "!==" ? a != b : op === "<" ? a < b : op === ">" ? a > b : op === "<=" ? a <= b : a >= b;
        }
        return a;
      }
      function and() { let a = cmp(); while (peek() === "&&") { next(); const b = cmp(); a = a && b; } return a; }
      function or() { let a = and(); while (peek() === "||") { next(); const b = and(); a = a || b; } return a; }
      try { const v = or(); return i >= toks.length ? !!v : true; } catch (e) { return true; }
    }
    function weVals(t) { const out = {}, own = t.weValues || {}; for (const [k, c] of Object.entries(t.weProps || {})) out[k] = own[k] ?? c.value; return out; }
    function weControls(t) {
      const vals = weVals(t);
      return Object.entries(t.weProps).map(([k, c]) => {
        if (c.cond && !weCond(c.cond, vals)) return "";
        const v = vals[k], lab = esc(c.label || k);
        if (c.type === "slider") return `<div class="ft-row"><span class="ft-name" title="${lab}">${lab}</span><input type="range" data-wek="${esc(k)}" min="${c.min}" max="${c.max}" step="${c.step || 1}" value="${v}"><span class="ft-val">${+(+v).toFixed(2)}</span></div>`;
        if (c.type === "color") return `<div class="ft-row"><span class="ft-name" title="${lab}">${lab}</span><div class="ft-cols">${colorBox("we:" + k, v)}</div></div>`;
        if (c.type === "toggle") return `<div><label class="ft-toggle"><input type="checkbox" data-wek="${esc(k)}" ${v ? "checked" : ""}> ${lab}</label></div>`;
        if (c.type === "choice") return `<div class="ft-row"><span class="ft-name" title="${lab}">${lab}</span><select class="ft-sel" data-wek="${esc(k)}">${(c.options || []).map((o) => `<option value="${esc(o.value)}" ${String(o.value) === String(v) ? "selected" : ""}>${esc(o.label)}</option>`).join("")}</select></div>`;
        if (c.type === "text") return `<div class="ft-row"><span class="ft-name" title="${lab}">${lab}</span><input class="ft-txt" data-wek="${esc(k)}" value="${esc(v)}"></div>`;
        return "";
      }).join("");
    }
    function setWe(k, v) { t.weValues = { ...(t.weValues || {}), [k]: v }; t.name = t.name || ""; }
    // The picker: every video and web wallpaper on this PC, newest look first by title; click one to try it.
    async function weImport() {
      const items = await opt.onWeList();
      if (!items || !items.length) { opt.onError && opt.onError("No Wallpaper Engine wallpapers found on this PC."); return; }
      const ok = items.filter((x) => x.ok), skipped = items.length - ok.length;
      const pane = document.createElement("div"); pane.className = "ft-we";
      pane.innerHTML = `<div class="ft-we-top"><h3>Wallpaper Engine</h3><button class="ft-btn ghost" data-we="close">Back</button>
        <input class="ft-txt" data-we="q" placeholder="Search ${ok.length} wallpapers" style="flex-basis:100%">
        <div class="ft-seg" style="flex-basis:100%">${["all", "video", "web"].map((f) => `<button data-wf="${f}" class="${f === "all" ? "on" : ""}">${{ all: "All", video: "Video", web: "Web page" }[f]}</button>`).join("")}</div></div>
        <div class="ft-we-grid"></div><div class="ft-note" style="padding:0 18px 12px">${skipped} more (scene wallpapers and presets) can't be used yet.</div>`;
      bg.querySelector(".ft").appendChild(pane);
      let filt = "all", q = "";
      const grid = pane.querySelector(".ft-we-grid");
      const fill = () => {
        grid.innerHTML = ok.filter((x) => (filt === "all" || x.type === filt) && (!q || x.title.toLowerCase().includes(q))).map((x) =>
          `<div class="ft-we-card" data-weid="${esc(x.id)}"><img loading="lazy" alt="" src="${esc(asset({ link: "we:" + x.id }, x.preview) || "")}"><div><b>${x.type === "web" ? "web" : "video"}</b>${esc(x.title)}</div></div>`).join("");
      };
      fill();
      pane.addEventListener("input", (e) => { if (e.target.dataset.we === "q") { q = e.target.value.trim().toLowerCase(); fill(); } });
      pane.addEventListener("click", async (e) => {
        e.stopPropagation();
        const el = e.target;
        if (el.dataset.we === "close") return pane.remove();
        if (el.dataset.wf) { filt = el.dataset.wf; pane.querySelectorAll("[data-wf]").forEach((b) => b.classList.toggle("on", b === el)); return fill(); }
        const card = el.closest("[data-weid]");
        if (!card) return;
        const r = await opt.onWeImport(card.dataset.weid);
        if (!r || !r.ok) { opt.onError && opt.onError((r && r.error) || "Couldn't use that wallpaper."); return; }
        pane.remove();
        t = { ...PRESETS[0], fx: null, fxLayers: [], ...r.theme };
        change();
        // colours from its preview picture, so the panels and accent match the wallpaper
        const prev = asset(t, t.preview);
        if (prev) {
          const img = new Image(); img.crossOrigin = "anonymous";
          img.onload = () => {
            try {
              const c = document.createElement("canvas"); c.width = c.height = 12; const g = c.getContext("2d"); g.drawImage(img, 0, 0, 12, 12);
              const d = g.getImageData(0, 0, 12, 12).data; let rr = 0, gg = 0, bb = 0;
              for (let i = 0; i < d.length; i += 4) { rr += d[i]; gg += d[i + 1]; bb += d[i + 2]; }
              const n = d.length / 4; t.tint = hex([rr / n, gg / n, bb / n]); change();
            } catch (err) {}
          };
          img.src = prev;
        }
      });
    }
    function setFx(k, v, li) {
      const o = layerOf(Number(li ?? -1)); if (!o) return;
      t.name = "";
      if (k === "speed") o.fxSpeed = v; else if (k === "amount") o.fxAmount = v; else if (k === "color") o.fxColor = v;
      else o.fxValues = { ...(o.fxValues || {}), [k]: v };
    }

    bg.addEventListener("input", (e) => {
      const el = e.target;
      if (el.dataset.c !== undefined) {
        const i = el.dataset.c;
        if (i === "a0" || i === "a1") t.accent[i === "a0" ? 0 : 1] = el.value; else if (i.startsWith("we:")) setWe(i.slice(3), el.value); else if (i.startsWith("fx:")) { const [, li, k] = i.split(":"); setFx(k, el.value, li); } else t.colors[Number(i)] = el.value;
        el.parentElement.style.background = el.value;
        t.name = ""; apply(t); opt.onChange && opt.onChange(t);
        const nm = bg.querySelector("#ft-name"); if (nm) nm.value = "";
        return;                                       // don't redraw mid-drag (it would close the picker)
      }
      if (el.dataset.k) {
        const v = Number(el.value);
        t[el.dataset.k] = ["strength", "fxSpeed", "fxAmount", "sound"].includes(el.dataset.k) ? v / 100 : v;
        el.nextElementSibling.textContent = v + (el.dataset.k === "angle" ? "°" : el.dataset.k === "blur" ? "px" : "%");
        apply(t); opt.onChange && opt.onChange(t);
      }
      if (el.dataset.wek && el.type === "range") {
        const v = Number(el.value); setWe(el.dataset.wek, v); el.nextElementSibling.textContent = +v.toFixed(2); weSendProps(); return;
      }
      if (el.dataset.lop !== undefined) {
        const v = Number(el.value); t.fxLayers[Number(el.dataset.lop)].opacity = v; t.name = "";
        el.nextElementSibling.textContent = Math.round(v * 100) + "%"; apply(t); opt.onChange && opt.onChange(t);
      }
      if (el.dataset.fxk && el.type === "range") {
        const k = el.dataset.fxk, li = Number(el.dataset.fxl), c = fxControlList((layerOf(li) || t).fx)[k] || {}, v = Number(el.value);
        setFx(k, v, li); el.nextElementSibling.textContent = shown({ ...c, v });
        apply(t); opt.onChange && opt.onChange(t);
      }
    });
    bg.addEventListener("change", async (e) => {
      const el = e.target;
      if (el.dataset.c !== undefined) draw();
      if (el.dataset.fxk && el.type !== "range") { setFx(el.dataset.fxk, el.type === "checkbox" ? el.checked : el.value, el.dataset.fxl); return change(); }
      if (el.dataset.lfx !== undefined) { const l = t.fxLayers[Number(el.dataset.lfx)]; l.fx = el.value; l.fxValues = {}; t.name = ""; return change(); }
      if (el.dataset.lbl !== undefined) { t.fxLayers[Number(el.dataset.lbl)].blend = el.value; t.name = ""; return change(); }
      if (el.dataset.wek && el.type !== "range") {
        const c = (t.weProps || {})[el.dataset.wek] || {};
        let v = el.type === "checkbox" ? el.checked : el.value;
        if (c.type === "choice") { const o = (c.options || []).find((x) => String(x.value) === v); if (o) v = o.value; }
        setWe(el.dataset.wek, v); weSendProps(); if (c.type !== "text") draw(); return;   // conditions may show or hide others
      }
      if (el.dataset.adev) { opt.audio.device = el.value || null; opt.onSetAudioDevice && opt.onSetAudioDevice(el.value || null); return; }
      if (el.dataset.muteall) { window.FunkTheme.muteAll = el.checked; opt.onSetMuteAll(el.checked); applySound(); return; }
      if (el.dataset.mo) { t.motion = { ...motion(t), [el.dataset.mo]: el.checked }; t.name = ""; return change(); }
      if (el.dataset.a === "autoacc") { t.accent = el.checked ? null : [...derive({ ...t, accent: null })["--grad"].match(/#[0-9a-f]{6}/gi).slice(0, 2)]; change(); }
      if (el.id === "ft-file" && el.files[0]) {
        try { Object.assign(t, await loadImage(el.files[0]), { style: "image", name: "", accent: null }); if (t.strength == null || t.strength < 0.3) t.strength = 0.6; change(); }
        catch (err) { opt.onError && opt.onError(err.message); }
      }
      if (el.id === "ft-import" && el.files[0]) {
        try {
          const j = JSON.parse(await el.files[0].text());
          if (!j || (j.funkTheme !== 1 && j.funkTheme !== 2)) throw new Error("That isn't a .funktheme file.");
          t = { ...PRESETS[0], ...j }; change();
        } catch (err) { opt.onError && opt.onError(err.message || "Couldn't read that theme."); }
      }
    });
    bg.addEventListener("toggle", (e) => { if (e.target.dataset && e.target.dataset.wedet) weOpen = e.target.open; }, true);
    bg.addEventListener("click", async (e) => {
      const el = e.target;
      if (el === bg) return finish(true);
      if (el.dataset.del !== undefined) {
        e.stopPropagation();
        const victim = library[Number(el.dataset.del)];
        library = (await (opt.onDelete && opt.onDelete(victim))) || library.filter((x) => x !== victim);
        return draw();
      }
      const sw = el.closest("[data-p],[data-l]");
      if (sw) { t = JSON.parse(JSON.stringify(sw.dataset.p !== undefined ? PRESETS[Number(sw.dataset.p)] : library[Number(sw.dataset.l)])); return change(); }
      const b = el.closest("button");
      if (!b) return;
      if (b.dataset.rmlayer !== undefined) { t.fxLayers.splice(Number(b.dataset.rmlayer), 1); t.name = ""; return change(); }
      if (b.dataset.fps) { t.motion = { ...motion(t), fps: Number(b.dataset.fps) }; t.name = ""; return change(); }
      if (b.dataset.fx && b.dataset.fx === "none") t.fxLayers = [];                  // no main effect = no extra layers either
      if (b.dataset.fx) { t.fx = b.dataset.fx === "none" ? null : b.dataset.fx; t.name = ""; if (t.fx && !t.strength) t.strength = 0.5; return change(); }
      if (b.dataset.style) {
        const s = b.dataset.style;
        if (s === t.style) return;
        if (s === "solid") t.colors = [t.colors[0] || "#1d2b53"];
        if (s === "gradient") t.colors = t.colors.length >= 2 ? t.colors.slice(0, 3) : [t.colors[0] && t.colors[0] !== "#0e0f12" ? t.colors[0] : "#ff7a3d", "#7c3aed"];
        if ((s === "gradient" || s === "rainbow" || s === "solid") && !t.strength) t.strength = 0.5;
        t.style = s; t.name = ""; t.accent = null;   // new background -> accent follows it
        if (s === "image" && !t.image) { change(); return bg.querySelector("#ft-file").click(); }
        if (s === "video" && !t.video) { change(); return pickVideo(); }
        return change();
      }
      const a = b.dataset.a;
      if (a === "pickvid") return pickVideo();
      if (a === "weimport") return weImport();
      if (a === "addlayer") {
        const next = ["rain", "stars", "ascii", "matrix", "synthwave"].find((k) => FXREG[k] && k !== t.fx && !(t.fxLayers || []).some((l) => l.fx === k)) || "rain";
        t.fxLayers = [...(t.fxLayers || []), { fx: next, opacity: 0.8, blend: "screen" }]; t.name = ""; return change();
      }
      if (a === "addc") { t.colors.push(mix(t.colors[0], t.colors[1], 0.5)); t.name = ""; return change(); }
      if (a === "rmc") { t.colors = t.colors.slice(0, 2); t.name = ""; return change(); }
      if (a === "pick") return bg.querySelector("#ft-file").click();
      if (a === "import") {
        if (!opt.onImport) return bg.querySelector("#ft-import").click();   // apps without a Python side: JSON themes only
        const r = await opt.onImport();                                      // the app's file dialog (reads zip themes too)
        if (r && r.ok && r.theme) { library = r.library || library; t = { ...PRESETS[0], ...r.theme }; return change(); }
        if (r && r.error) opt.onError && opt.onError(r.error);
        return;
      }
      if (a === "export" && t.link) { opt.onError && opt.onError("This one plays straight from Wallpaper Engine's Workshop folder and belongs to its author, so it can't be sent. Pick your own video to make one you can share."); return; }
      if (a === "save" || a === "export") {
        const name = bg.querySelector("#ft-name").value.trim();
        if (!name) { opt.onError && opt.onError("Give the theme a name first."); bg.querySelector("#ft-name").focus(); return; }
        t.name = name; t.funkTheme = t.assets ? 2 : 1;      // v1 apps can still read anything without asset files
        if (t.style === "video") { const c = videoFrame(480); if (c) t.previewData = c.toDataURL("image/jpeg", 0.82); }
        if (a === "save") {
          if (L.video && String(t.assets || "").startsWith("_draft-")) { L.video.removeAttribute("src"); L.video.load(); delete L.video.dataset.src; }   // let the draft folder move
          library = (await (opt.onSave && opt.onSave(t))) || library;
          const fresh = library.find((x) => x.name === t.name);           // files may have moved to the theme's own folder
          if (fresh) { t = JSON.parse(JSON.stringify(fresh)); apply(t); opt.onChange && opt.onChange(t); }
          draw();
        }
        else opt.onExport && opt.onExport(t);
        return;
      }
      if (a === "close") return finish(false);
      if (a === "cancel") return finish(true);
    });
    async function pickVideo() {
      const r = opt.onPickVideo && await opt.onPickVideo(t.assets || null);
      if (!r || !r.ok) { if (r && r.error) opt.onError && opt.onError(r.error); return; }
      Object.assign(t, { style: "video", video: "asset:" + r.file, assets: r.assets, name: "", accent: null, tint: null, link: null, imported: null, preview: null });
      if (t.strength == null || t.strength < 0.3) t.strength = 0.6;
      change();
      // once the first frame is in, the accent and surfaces take their colour from the video
      const v = L.video;
      if (v) v.addEventListener("loadeddata", () => {
        const c = videoFrame(12); if (!c) return;
        const d = c.getContext("2d").getImageData(0, 0, c.width, c.height).data; let r = 0, g = 0, b = 0;
        for (let i = 0; i < d.length; i += 4) { r += d[i]; g += d[i + 1]; b += d[i + 2]; }
        const n = d.length / 4; t.tint = hex([r / n, g / n, b / n]); change();
      }, { once: true });
    }
    function finish(revert) {
      if (revert) { apply(original); opt.onChange && opt.onChange(original); }
      bg.remove();
      opt.onDone && opt.onDone(revert ? original : t);
    }
    draw();
  }

  // ---------------------------------------------------------------- animated effects (theme.fx)
  // A canvas behind the whole app (position:fixed, z-index:-1, so it paints over the page background but under
  // everything else; surfaces are glass, so it shows through). 30 fps, paused while the window is hidden, one still
  // frame for "reduce motion". FunkTheme.level (0..1) lets an app make it move with its music.
  const FX = { none: "None" };          // name -> label, in the order effects registered
  const FXREG = {};                     // name -> { label, controls, draw(g, P) }
  // Every effect gets these three; they save as fxSpeed / fxAmount / fxColor so v1 apps still read them.
  const FX_SHARED = {
    speed: { type: "slider", label: "Speed", min: 0.2, max: 3, step: 0.05, value: 1, pct: true },
    amount: { type: "slider", label: "Amount", min: 0, max: 1, step: 0.01, value: 0.7, pct: true },
    color: { type: "color", label: "Colour", value: null },              // null = the theme's accent
  };
  function registerFx(name, def) {
    if (!name || !def || typeof def.draw !== "function") throw new Error("registerFx needs a name and a draw(g, P) function");
    FXREG[name] = def; FX[name] = def.label || name;
  }
  const fxControlList = (name) => ({ ...FX_SHARED, ...((FXREG[name] && FXREG[name].controls) || {}) });
  /** Every control's value for the theme's effect: the theme's own, else the control's default. */
  function fxValues(t) {
    const out = {}, own = (t && t.fxValues) || {};
    for (const [k, c] of Object.entries(fxControlList(t && t.fx))) out[k] = own[k] ?? c.value;
    if (t && t.fxSpeed != null) out.speed = t.fxSpeed;
    if (t && t.fxAmount != null) out.amount = t.fxAmount;
    if (t && t.fxColor) out.color = t.fxColor;
    return out;
  }
  const GLYPHS = "ｱｲｳｴｵｶｷｸｹｺｻｼｽｾｿﾀﾁﾂﾃﾄﾅﾆﾇﾈﾉﾊﾋﾌﾍﾎﾏﾐﾑﾒﾓﾔﾕﾖﾗﾘﾙﾚﾛﾜﾝ0123456789ABCDEFZ+-*=<>";
  // ---------------------------------------------------------------- layers (FunkThemes 2)
  // #ft-layers sits right above the app's #page-bg (or behind everything in apps without one) and holds, bottom to top:
  // the background video (style "video"), then up to 3 effect canvases: the theme's own fx, then t.fxLayers
  // [{ fx, fxSpeed, fxAmount, fxColor, fxValues, opacity, blend }]. One loop draws them all.
  // t.motion = { fps: 15|30|60, half: bool (draw at half resolution), drift: bool (background follows the mouse a little),
  //              pauseFullscreen: bool (default on) }. Everything stops while the window is hidden, and while
  // FunkTheme.paused is true: apps set FunkTheme.checkPause = async () => bool (e.g. "a fullscreen game is in front"),
  // polled every 2 s. Other animations in an app (the Brain) can ask FunkTheme.isPaused() and FunkTheme.motion().
  const L = { box: null, video: null, fx: [], raf: 0, last: 0, t: null, vars: null, dx: 0, dy: 0, drawn: false, polling: false };
  const MOTION = { fps: 30, half: false, drift: false, pauseFullscreen: true, listen: false };
  const motion = (t) => ({ ...MOTION, ...((t && t.motion) || {}) });
  const isPaused = () => document.hidden || !!(window.FunkTheme && window.FunkTheme.paused);
  const webUrl = (t) => (t && t.style === "web" && t.web ? asset(t, String(t.web).replace(/^asset:/, "")) : null);
  // An imported Wallpaper Engine page's settings in its own format: { key: { value } }, colours as "r g b" (0..1).
  function weProps(t) {
    const out = {}, own = (t && t.weValues) || {};
    for (const [k, c] of Object.entries((t && t.weProps) || {})) {
      let v = own[k] ?? c.value;
      if (c.type === "color") v = rgb(v).map((x) => (x / 255).toFixed(4)).join(" ");
      out[k] = { value: v };
    }
    return out;
  }
  function weSend(msg) { try { if (L.web && L.web.contentWindow) L.web.contentWindow.postMessage(msg, "*"); } catch (e) {} }
  function weSendProps() { if (L.web && L.webReady) weSend({ funkWe: "props", props: weProps(L.t) }); }
  addEventListener("message", (e) => {
    if (!L.web || e.source !== L.web.contentWindow) return;               // only our own frame
    const m = e.data || {};
    if (m.funkWe === "ready") { L.webReady = true; L.vol = -1; weSendProps(); applySound(); }
    if (m.funkWe === "audio-on") L.weAudio = true;
  });
  // Wallpaper sound, like Wallpaper Engine: t.sound 0..1 per theme (new themes are silent), FunkTheme.muteAll for every
  // theme, and nothing while paused (hidden, or a fullscreen game in front).
  function applySound() {
    const FT = window.FunkTheme || {}, t = L.t;
    const v = FT.muteAll || isPaused() ? 0 : clamp(Number(t && t.sound) || 0, 0, 1);
    if (L.video) { L.video.muted = v === 0; L.video.volume = v; }
    if (L.web && L.webReady && v !== L.vol) { weSend({ funkWe: "volume", v }); L.vol = v; }
  }
  // The mouse: a web wallpaper sits behind the app, so it never sees the pointer. Moves always go through; presses and
  // clicks only when they land on empty background (never on the app's own buttons, cards or text).
  const INTERACTIVE = "button,a,input,select,textarea,label,summary,[role=button],[contenteditable],.ft-bg";
  function onBackground(el) {
    if (!el || el.closest && el.closest(INTERACTIVE)) return false;
    if (el === document.body || el === document.documentElement || el.id === "page-bg") return true;
    const bg = getComputedStyle(el).backgroundColor;
    return bg === "transparent" || /rgba\(.*,\s*0\)$/.test(bg);
  }
  // Sound follows pausing at once, not on the next frame: a hidden or minimised window runs no frames at all.
  document.addEventListener("visibilitychange", () => applySound());
  let mouseAt = 0;
  function weMouse(e, kind) {
    if (!L.web || !L.webReady || isPaused() || L.web.style.display === "none") return;
    if (kind === "move") { const now = performance.now(); if (now - mouseAt < 16) return; mouseAt = now; }
    else if (!onBackground(e.target)) return;
    const r = L.web.getBoundingClientRect(), k = r.width / (parseFloat(L.web.style.width) || r.width);
    weSend({ funkWe: "mouse", kind, x: (e.clientX - r.left) / k, y: (e.clientY - r.top) / k });
  }
  addEventListener("pointermove", (e) => weMouse(e, "move"), { passive: true, capture: true });
  addEventListener("pointerdown", (e) => weMouse(e, "down"), { passive: true, capture: true });
  addEventListener("pointerup", (e) => weMouse(e, "up"), { passive: true, capture: true });
  addEventListener("click", (e) => weMouse(e, "click"), { passive: true, capture: true });
  const videoUrl = (t) => (t && t.style === "video" && t.video ? asset(t, String(t.video).replace(/^asset:/, "")) : null);
  function fxLayersOf(t) {
    const out = [];
    if (t && t.fx && FXREG[t.fx]) out.push({ ...t, opacity: 1, blend: "normal" });
    for (const l of (t && t.fxLayers) || []) if (l && FXREG[l.fx]) out.push(l);
    return out.slice(0, 3);
  }
  function layerBox() {
    if (L.box && L.box.isConnected) return L.box;
    const box = document.createElement("div"), pg = document.getElementById("page-bg");
    box.id = "ft-layers";
    box.style.cssText = `position:fixed;inset:0;pointer-events:none;overflow:hidden;z-index:${pg ? 0 : -1}`;
    if (pg) pg.after(box); else document.body.prepend(box);
    return (L.box = box);
  }
  function layersRun(t, vars) {
    L.t = t; L.vars = vars;
    const m = motion(t), want = fxLayersOf(t), vid = videoUrl(t);
    const web = webUrl(t);
    if (!want.length && !vid && !web && !m.drift) return layersStop();
    const box = layerBox();
    if (web) {
      if (!L.web) {
        const f = document.createElement("iframe");
        // Its own origin (the asset server's) so 3D textures, storage and loaders work. It still can't reach the app:
        // the app page is served from a different port, so it is cross-origin, and pop-ups / taking over the window stay blocked.
        f.setAttribute("sandbox", "allow-scripts allow-same-origin");
        f.setAttribute("allow", "autoplay"); f.tabIndex = -1; f.setAttribute("aria-hidden", "true");
        f.addEventListener("load", () => { L.webReady = true; weSendProps(); });
        box.prepend(f); L.web = f;
      }
      const s = clamp(t.strength == null ? 0.85 : Number(t.strength), 0, 1);
      // Wallpaper Engine pages are made for the whole desktop: render at the monitor's size, scaled to cover the window
      L.web.style.cssText = `position:absolute;left:50%;top:50%;border:0;pointer-events:none;transform-origin:50% 50%;` +
        `opacity:${(0.3 + 0.7 * s).toFixed(3)};filter:blur(${Number(t.blur) || 0}px)`;
      fitWeb();
      if (L.web.dataset.src !== web) { L.web.dataset.src = web; L.webReady = false; L.weAudio = false; L.web.src = web; }
      else weSendProps();
    } else if (L.web) { L.web.remove(); L.web = null; }
    if (vid) {
      if (!L.video) {
        const v = document.createElement("video");
        v.muted = true; v.loop = true; v.playsInline = true; v.crossOrigin = "anonymous"; v.setAttribute("muted", "");
        box.prepend(v); L.video = v;
      }
      const v = L.video, s = clamp(t.strength == null ? 0.6 : Number(t.strength), 0, 1);
      v.style.cssText = `position:absolute;inset:-24px;width:calc(100% + 48px);height:calc(100% + 48px);object-fit:cover;` +
        `opacity:${(0.7 * s).toFixed(3)};filter:blur(${Number(t.blur) || 0}px)`;
      if (v.dataset.src !== vid) { v.dataset.src = vid; v.src = vid; }
      if (!isPaused()) v.play().catch(() => {});
    } else if (L.video) { L.video.remove(); L.video = null; }
    while (L.fx.length > want.length) L.fx.pop().cv.remove();
    want.forEach((lt, i) => {
      let f = L.fx[i];
      if (!f) { const cv = document.createElement("canvas"); box.appendChild(cv); f = L.fx[i] = { cv, st: {}, kind: null, last: 0 }; }
      if (f.kind !== lt.fx) { f.kind = lt.fx; f.st = {}; }
      f.t = lt;
      f.cv.style.cssText = `position:absolute;inset:0;width:100%;height:100%;opacity:${lt.opacity ?? 1};mix-blend-mode:${lt.blend || "normal"}`;
    });
    L.drawn = false;
    applySound();
    if (!L.raf) L.raf = requestAnimationFrame(layersFrame);
  }
  function layersStop() {
    cancelAnimationFrame(L.raf); L.raf = 0;
    L.fx.forEach((f) => f.cv.remove()); L.fx = [];
    if (L.video) { L.video.remove(); L.video = null; }
    if (L.web) { L.web.remove(); L.web = null; }
    setDrift(0, 0);
  }
  function fitWeb() {
    if (!L.web) return;
    const sw = Math.max(800, screen.width || 1920), sh = Math.max(450, screen.height || 1080);
    const k = Math.max((innerWidth + 48) / sw, (innerHeight + 48) / sh);
    L.web.style.width = sw + "px"; L.web.style.height = sh + "px";
    L.web.style.transform = `translate(calc(-50% + ${L.dx.toFixed(1)}px), calc(-50% + ${L.dy.toFixed(1)}px)) scale(${k.toFixed(4)})`;
  }
  addEventListener("resize", fitWeb);
  function setDrift(x, y) {
    L.dx = x; L.dy = y;
    const tr = x || y ? `translate(${x.toFixed(1)}px,${y.toFixed(1)}px)` : "";
    const pg = document.getElementById("page-bg");
    if (pg) pg.style.transform = tr;
    if (L.video) L.video.style.transform = tr;
    fitWeb();
  }
  function layersFrame(now) {
    L.raf = requestAnimationFrame(layersFrame);
    const t = L.t, m = motion(t);
    const reduce = window.matchMedia && matchMedia("(prefers-reduced-motion: reduce)").matches;
    applySound();
    if (isPaused() || (reduce && L.drawn)) {
      if (L.video && !L.video.paused) L.video.pause();
      if (L.web && L.web.style.display !== "none") { weSend({ funkWe: "paused", paused: true }); L.web.style.display = "none"; }   // a hidden frame stops drawing
      return;
    }
    if (L.video && L.video.paused) L.video.play().catch(() => {});
    if (L.web && L.web.style.display === "none") { L.web.style.display = ""; weSend({ funkWe: "paused", paused: false }); }
    if (now - L.last < 1000 / m.fps - 4) return;                         // the FPS cap (4 ms slack for frame jitter)
    const dt = Math.min(0.1, (now - (L.last || now)) / 1000) || 1 / m.fps; L.last = now;
    if (m.drift) {                                                        // ease toward the mouse, up to 12 px each way
      const on = FX_MOUSE.x > -1000, tx = on ? (0.5 - FX_MOUSE.x / innerWidth) * 24 : 0, ty = on ? (0.5 - FX_MOUSE.y / innerHeight) * 24 : 0;
      const k = Math.min(1, dt * 4); setDrift(L.dx + (tx - L.dx) * k, L.dy + (ty - L.dy) * k);
    } else if (L.dx || L.dy) setDrift(0, 0);
    const dpr = Math.min(1.5, window.devicePixelRatio || 1) * (m.half ? 0.5 : 1), v = L.vars, au = audio();
    const w = Math.ceil(innerWidth * dpr), h = Math.ceil(innerHeight * dpr);
    for (const f of L.fx) {
      const def = FXREG[f.kind];
      if (!def || now - f.last < 1000 / Math.min(m.fps, def.fps || 60) - 4) continue;
      const fdt = Math.min(0.1, (now - (f.last || now)) / 1000) || dt; f.last = now;
      if (f.cv.width !== w || f.cv.height !== h) { f.cv.width = w; f.cv.height = h; f.st = {}; }
      const val = fxValues(f.t);
      def.draw(f.cv.getContext("2d"), { w, h, dpr, dt: fdt * val.speed, speed: val.speed, au, amt: val.amount, v: val,
        a: v["--brand"], b: v["--brand2"], bg: v["--bg"], st: f.st, now: now / 1000, c: val.color || v["--brand"] });
    }
    if (L.web && L.weAudio) {                                             // Wallpaper Engine's audio feed: 128 bands, ~fps times a second
      const b = window.FunkTheme && window.FunkTheme.audio && window.FunkTheme.audio.bands;
      weSend({ funkWe: "audio", bands: b && b.length >= 128 ? Array.from(b).slice(0, 128) : new Array(128).fill(0) });
    }
    L.drawn = true;
  }
  setInterval(() => {                                                     // ask the app whether to pause (fullscreen game)
    const FT = window.FunkTheme;
    if (!FT || typeof FT.checkPause !== "function" || L.polling || document.hidden) return;
    if (motion(L.t).pauseFullscreen === false) { FT.paused = false; applySound(); return; }
    L.polling = true;
    Promise.resolve(FT.checkPause()).then((p) => { FT.paused = !!p; applySound(); }).catch(() => {}).finally(() => { L.polling = false; });
  }, 2000);
  /** The current background video's frame as a small canvas (for a theme preview and its tint), or null. */
  function videoFrame(width) {
    const v = L.video;
    if (!v || v.readyState < 2 || !v.videoWidth) return null;
    const c = document.createElement("canvas"); c.width = width; c.height = Math.round(width * v.videoHeight / v.videoWidth);
    try { c.getContext("2d").drawImage(v, 0, 0, c.width, c.height); c.toDataURL(); } catch (e) { return null; }   // cross-origin guard
    return c;
  }
  // ---- audio. Apps set FunkTheme.audio = { bass, mid, high, beat } (0..1) and/or bands: 128 values 0..1, 64 left then
  // 64 right, low to high on a log scale (Wallpaper Engine's layout). Whatever is missing is worked out from the bands;
  // beat = the bass jumping over its recent average, then decaying. With the theme's motion.listen on, the engine
  // fetches the bands itself from FunkTheme.audioSource() (the app's "what this PC is playing"), ~30 times a second.
  // FunkTheme.level (older apps) is treated as bass. audio() runs once per frame and is shared (FunkTheme.audioNow).
  const AUD = { avg: 0, beat: 0, at: 0, mono: new Float32Array(64), frameAt: -1, out: null, polling: false, mine: false };
  function audio() {
    const now = performance.now();
    if (AUD.out && now - AUD.frameAt < 8) return AUD.out;
    const FT = window.FunkTheme || {}, A = FT.audio || {}, b = A.bands;
    const lv = clamp(Number(FT.level) || 0, 0, 1);
    let bass = A.bass, mid = A.mid, high = A.high, beat = A.beat, live = false;
    if (b && b.length >= 64) {
      const two = b.length >= 128, m = AUD.mono;
      let peak = 0;
      for (let i = 0; i < 64; i++) { m[i] = two ? (b[i] + b[64 + i]) / 2 : b[i]; if (m[i] > peak) peak = m[i]; }
      const avgOf = (a, z) => { let t = 0; for (let i = a; i < z; i++) t += m[i]; return t / (z - a); };
      bass ??= avgOf(0, 14); mid ??= avgOf(14, 44); high ??= avgOf(44, 64);       // ~30-120 Hz, to ~2 kHz, above
      live = peak > 0.02;
      if (beat == null && A.beats != null) {               // the app counted the kicks where the sound comes in (no delay)
        const dt = Math.min(0.1, (now - (AUD.at || now)) / 1000); AUD.at = now;
        AUD.beat = A.beats !== AUD.seenBeats && AUD.seenBeats != null ? 1 : Math.max(0, AUD.beat - dt * 4);
        AUD.seenBeats = A.beats;
        beat = AUD.beat;
      }
      if (beat == null) {
        const dt = Math.min(0.1, (now - (AUD.at || now)) / 1000); AUD.at = now;
        AUD.avg += (bass - AUD.avg) * Math.min(1, dt * 2.5);
        AUD.beat = bass > AUD.avg * 1.18 + 0.06 && AUD.beat < 0.5 ? 1 : Math.max(0, AUD.beat - dt * 4);
        beat = AUD.beat;
      }
    } else AUD.mono.fill(0);
    AUD.out = { bass: clamp(bass ?? lv, 0, 1), mid: clamp(mid ?? lv * 0.7, 0, 1), high: clamp(high ?? lv * 0.5, 0, 1),
                beat: clamp(beat ?? 0, 0, 1), bands: AUD.mono, live: live || (bass ?? lv) > 0.02 };
    AUD.frameAt = now;
    return AUD.out;
  }
  setInterval(() => {                                     // motion.listen: pull the bands from the app
    const FT = window.FunkTheme;
    if (!FT) return;
    const want = motion(L.t).listen && typeof FT.audioSource === "function" && !isPaused();
    if (!want) { if (AUD.mine) { FT.audio = null; AUD.mine = false; } return; }
    if (AUD.polling) return;
    AUD.polling = true;
    Promise.resolve(FT.audioSource()).then((r) => {
      const a = Array.isArray(r) ? { bands: r } : r;                 // older apps: just the bands
      if (motion(L.t).listen && a && a.bands && a.bands.length) { FT.audio = a; AUD.mine = true; }
    }).catch(() => {}).finally(() => { AUD.polling = false; });
  }, 16);

  // ASCII smoke (the Claude app's new-chat background, made into a fluid): a density field on the character grid is
  // carried by a swirling divergence-free flow (curl of a moving stream function) and fades slowly, so it stretches into
  // wisps. Wandering emitters breathe smoke in, every beat throws a puff, the mouse stirs a vortex and leaves a trail.
  // Bass = flow speed, treble = character shimmer. Glyphs are pre-drawn once and blitted with alpha (fast).
  const ASCII = "abcdefghijklmnopqrstuvwxyz0123456789";
  const FX_MOUSE = { x: -9999, y: -9999, t: 0 };
  addEventListener("pointermove", (e) => { FX_MOUSE.x = e.clientX; FX_MOUSE.y = e.clientY; FX_MOUSE.t = performance.now(); }, { passive: true });
  function fxAscii(g, P) {
    const { w, h, st } = P, cw = Math.round(9 * P.dpr), ch = Math.round(15 * P.dpr);
    const C = Math.ceil(w / cw), R = Math.ceil(h / ch), n = ASCII.length, au = P.au;
    if (!st.atlas || st.C !== C || st.R !== R || st.color !== P.c) {
      const A = document.createElement("canvas"); A.width = cw * n; A.height = ch;
      const ax = A.getContext("2d"); ax.fillStyle = P.c; ax.textBaseline = "top";
      ax.font = `${Math.round(11.5 * P.dpr)}px "Cascadia Mono", Consolas, monospace`;
      for (let i = 0; i < n; i++) ax.fillText(ASCII[i], i * cw + P.dpr, 1.5 * P.dpr);
      st.atlas = A; st.C = C; st.R = R; st.color = P.c; st.t = st.t || Math.random() * 100;
      st.d = new Float32Array(C * R); st.d2 = new Float32Array(C * R);
      st.g = new Uint8Array(C * R); for (let i = 0; i < st.g.length; i++) st.g[i] = (Math.random() * n) | 0;
      st.fx = [new Float32Array(C), new Float32Array(C), new Float32Array(C)]; st.dfx = [new Float32Array(C), new Float32Array(C), new Float32Array(C)];
      st.gy = [new Float32Array(R), new Float32Array(R), new Float32Array(R)]; st.dgy = [new Float32Array(R), new Float32Array(R), new Float32Array(R)];
      st.em = Array.from({ length: 4 }, (_, i) => ({ x: Math.random() * C, y: Math.random() * R, a: i * 1.7 }));
      st.mx = FX_MOUSE.x / cw * P.dpr; st.my = FX_MOUSE.y / ch * P.dpr; st.beatSeen = 0;
      for (let k = 0; k < 90; k++) fxAsciiStep(st, C, R, 0.05, P, { bass: 0.3, mid: 0, high: 0, beat: 0 }, true);   // start with smoke already there
    }
    fxAsciiStep(st, C, R, Math.min(P.dt, 0.08), P, au, false);
    g.clearRect(0, 0, w, h);
    const base = (0.55 + 0.9 * P.amt), shimmer = 0.006 + 0.05 * au.high, d = st.d;
    for (let r = 0, i = 0; r < R; r++) {
      const y = r * ch;
      for (let c = 0; c < C; c++, i++) {
        let a = d[i] * base; if (a < 0.03) continue;
        if (Math.random() < shimmer) st.g[i] = (Math.random() * n) | 0;
        g.globalAlpha = a > 0.85 ? 0.85 : a;
        g.drawImage(st.atlas, st.g[i] * cw, 0, cw, ch, c * cw, y, cw, ch);
      }
    }
    g.globalAlpha = 1;
  }
  function fxAsciiStep(st, C, R, dt, P, au, warm) {
    st.t += dt * (0.5 + 1.6 * au.bass);
    const t = st.t, sp = (3.2 + 9 * au.bass) * (P.speed || 1);           // cells per second
    // stream function psi = sum a_i f_i(x) g_i(y); flow = (d psi/dy, -d psi/dx): swirly and divergence-free
    const K = [[0.11, 0.19, 0.6, 0.45, 1.0], [0.23, 0.13, -0.5, 0.7, 0.6], [0.05, 0.31, 0.35, -0.4, 0.8]];
    for (let k = 0; k < 3; k++) {
      const [kx, ky, wx, wy, a] = K[k];
      for (let c = 0; c < C; c++) { const p = kx * c + wx * t + k; st.fx[k][c] = a * Math.sin(p); st.dfx[k][c] = a * kx * Math.cos(p); }
      for (let r = 0; r < R; r++) { const p = ky * r + wy * t + 2 * k; st.gy[k][r] = Math.cos(p); st.dgy[k][r] = -ky * Math.sin(p); }
    }
    // the mouse (a vortex + smoke trail while it moves)
    const mxT = FX_MOUSE.x * P.dpr / (9 * P.dpr), myT = FX_MOUSE.y * P.dpr / (15 * P.dpr);
    const mvx = mxT - st.mx, mvy = myT - st.my; st.mx = mxT; st.my = myT;
    const moving = !warm && performance.now() - FX_MOUSE.t < 120 && Math.abs(mvx) + Math.abs(mvy) > 0.05;
    const d = st.d, d2 = st.d2, keep = Math.pow(0.5, dt / (2.2 + 2 * P.amt));   // smoke half-life in seconds
    for (let r = 0, i = 0; r < R; r++) {
      for (let c = 0; c < C; c++, i++) {
        let vx = 0, vy = 0;
        for (let k = 0; k < 3; k++) { vx += st.fx[k][c] * st.dgy[k][r]; vy -= st.dfx[k][c] * st.gy[k][r]; }
        vx *= sp * 5; vy *= sp * 5;                                             // same scale both ways keeps it divergence-free (no piling up)
        const ddx = c - st.mx, ddy = r - st.my, dd = ddx * ddx + ddy * ddy;
        if (P.v.mouse !== false && dd < 140) { const s = Math.exp(-dd / 30) * 14; vx += -ddy * s * 0.3; vy += ddx * s * 0.3; }
        let x = c - vx * dt, y = r - vy * dt;                                   // semi-Lagrangian: where did this cell's smoke come from
        x = x < 0 ? 0 : x > C - 1.001 ? C - 1.001 : x; y = y < 0 ? 0 : y > R - 1.001 ? R - 1.001 : y;
        const x0 = x | 0, y0 = y | 0, fx = x - x0, fy = y - y0, j = y0 * C + x0;
        d2[i] = keep * ((d[j] * (1 - fx) + d[j + 1] * fx) * (1 - fy) + (d[j + C] * (1 - fx) + d[j + C + 1] * fx) * fy);
      }
    }
    st.d = d2; st.d2 = d;
    const puff = (cx, cy, rad, amt) => {
      const r0 = Math.max(0, (cy - rad) | 0), r1 = Math.min(R - 1, (cy + rad) | 0), c0 = Math.max(0, (cx - rad * 1.8) | 0), c1 = Math.min(C - 1, (cx + rad * 1.8) | 0);
      for (let r = r0; r <= r1; r++) for (let c = c0; c <= c1; c++) {
        const q = ((c - cx) / 1.8) ** 2 + (r - cy) ** 2; if (q < rad * rad) { const k = r * C + c; st.d[k] = Math.min(1.2, st.d[k] + amt * (1 - q / (rad * rad))); }
      }
    };
    for (const e of st.em) {                                                   // wandering emitters breathe smoke in
      e.a += dt * 0.15; e.x = (C * (0.5 + 0.42 * Math.sin(e.a * 0.9 + e.x * 0.0001))); e.y = R * (0.5 + 0.4 * Math.cos(e.a * 0.7 + 1.3 * st.em.indexOf(e)));
      puff(e.x, e.y, 3 + 2 * au.mid, dt * (0.6 + 1.6 * au.mid) * (0.6 + P.amt));
    }
    if (!warm && P.v.beats !== false && au.beat > 0.85 && performance.now() - st.beatSeen > 180) {    // a beat throws a puff somewhere
      st.beatSeen = performance.now();
      puff(Math.random() * C, Math.random() * R, 5 + 6 * au.bass, 0.55 + 0.4 * au.bass);
    }
    if (moving && P.v.mouse !== false) puff(st.mx, st.my, 2.5, 0.35);
  }
  function fxMatrix(g, P) {
    const { w, h, st } = P, au = P.au, fs = Math.round(15 * P.dpr), n = Math.ceil(w / fs);
    if (!st.y) { st.y = Array.from({ length: n }, () => Math.random() * -h / fs); st.v = Array.from({ length: n }, () => 8 + Math.random() * 14); g.fillStyle = P.bg; g.fillRect(0, 0, w, h); }
    g.fillStyle = rgba(P.bg, Math.max(0.03, 0.26 - 0.2 * (P.v.trail ?? 0.5) - 0.06 * au.bass)); g.fillRect(0, 0, w, h);    // longer trails on loud bass
    g.font = `${fs}px "MS Gothic", "Cascadia Mono", monospace`;
    const sp = 1 + 2.2 * au.bass, flash = au.beat * 0.25;
    for (let i = 0; i < n; i++) {
      if (Math.random() > 0.35 + 0.65 * P.amt + 0.3 * au.mid && st.y[i] < 0) continue;
      const y = st.y[i] * fs, ch = GLYPHS[(Math.random() * GLYPHS.length) | 0];
      g.fillStyle = Math.random() < 0.08 + flash ? "#e9fff1" : P.c; g.globalAlpha = Math.min(1, 0.55 + 0.45 * P.amt + flash);
      g.fillText(ch, i * fs, y);
      st.y[i] += st.v[i] * P.dt * sp;
      if (y > h && Math.random() > 0.97 - 0.05 * au.beat) st.y[i] = Math.random() * -20;
    }
    g.globalAlpha = 1;
  }
  function fxSynth(g, P) {
    const { w, h, st } = P, au = P.au; g.clearRect(0, 0, w, h);
    st.ph = ((st.ph || 0) + P.dt * (0.35 + 1.4 * au.bass)) % 1;                 // the grid rushes with the bass
    const y0 = h * 0.62, cx = w / 2, r = Math.min(w, h) * 0.2 * (1 + 0.12 * au.bass + 0.06 * au.beat);   // the sun breathes
    if (P.v.sun !== false) {
    const glow = g.createRadialGradient(cx, y0, r * 0.6, cx, y0, r * (1.9 + au.beat));
    glow.addColorStop(0, rgba(P.a, 0.10 + 0.25 * au.beat)); glow.addColorStop(1, rgba(P.a, 0));
    g.fillStyle = glow; g.fillRect(0, 0, w, y0);
    const sun = g.createLinearGradient(0, y0 - r, 0, y0); sun.addColorStop(0, P.b); sun.addColorStop(1, P.a);
    g.save(); g.globalAlpha = 0.35 + 0.45 * P.amt; g.fillStyle = sun;
    g.beginPath(); g.arc(cx, y0, r, Math.PI, 0); g.fill();
    g.globalCompositeOperation = "destination-out";
    for (let k = 0; k < 7; k++) { const yy = y0 - r * (k / 7) ** 0.8 * 0.9, th = (1 + k) * P.dpr * 1.6; g.fillRect(cx - r, yy - th, 2 * r, th); }
    g.restore();
    }
    g.strokeStyle = P.a; g.lineWidth = (1.2 + 1.2 * au.beat) * P.dpr; g.globalAlpha = 0.25 + 0.5 * P.amt;
    for (let k = -14; k <= 14; k++) { g.beginPath(); g.moveTo(cx + k * w * 0.012, y0); g.lineTo(cx + k * w * 0.16, h); g.stroke(); }
    for (let k = 0; k < 14; k++) { const d = (k + st.ph) / 14, y = y0 + (h - y0) * d * d; g.globalAlpha = (0.15 + 0.6 * P.amt + 0.3 * au.mid) * d; g.beginPath(); g.moveTo(0, y); g.lineTo(w, y); g.stroke(); }
    const fog = g.createLinearGradient(0, y0 - 2, 0, y0 + h * 0.08); fog.addColorStop(0, rgba(P.bg, 0.9)); fog.addColorStop(1, rgba(P.bg, 0));
    g.globalAlpha = 1; g.fillStyle = fog; g.fillRect(0, y0 - 2, w, h * 0.08);
  }
  function fxStars(g, P) {
    const { w, h, st } = P, au = P.au, n = Math.round(120 + 500 * P.amt);
    if (!st.s || st.s.length !== n) st.s = Array.from({ length: n }, () => [Math.random() * 2 - 1, Math.random() * 2 - 1, Math.random()]);
    g.clearRect(0, 0, w, h);
    const warp = 0.12 + (0.9 * au.bass * au.bass + 0.4 * au.beat) * (P.v.warp ?? 1);               // the bass punches the warp
    for (const s of st.s) {
      const z0 = s[2]; s[2] -= P.dt * warp; if (s[2] <= 0.02) { s[0] = Math.random() * 2 - 1; s[1] = Math.random() * 2 - 1; s[2] = 1; continue; }
      const x = w / 2 + s[0] / s[2] * w * 0.5, y = h / 2 + s[1] / s[2] * h * 0.5;
      if (x < 0 || x > w || y < 0 || y > h) { s[2] = 0; continue; }
      const px = w / 2 + s[0] / z0 * w * 0.5, py = h / 2 + s[1] / z0 * h * 0.5;     // streak = where it was last frame
      g.globalAlpha = Math.min(1, (1 - s[2]) * (1.2 + au.high)); g.strokeStyle = s[0] > 0.6 ? P.b : s[1] > 0.6 ? P.a : "#ffffff";
      g.lineWidth = (1 - s[2]) * 2.4 * P.dpr; g.beginPath(); g.moveTo(px, py); g.lineTo(x + 0.5, y + 0.5); g.stroke();
    }
    g.globalAlpha = 1;
  }
  function fxRain(g, P) {
    const { w, h, st } = P, au = P.au, n = Math.round(80 + 420 * P.amt);
    if (!st.d || st.d.length !== n) st.d = Array.from({ length: n }, () => [Math.random() * w, Math.random() * h, 0.5 + Math.random()]);
    g.clearRect(0, 0, w, h);
    st.flash = Math.max(0, (st.flash || 0) - P.dt * 3);
    if (P.v.lightning !== false && au.beat > 0.95 && au.bass > 0.75 && Math.random() < 0.25) st.flash = 1;       // lightning on the big hits
    if (st.flash > 0) { g.fillStyle = rgba("#dfe8ff", 0.10 * st.flash * st.flash); g.fillRect(0, 0, w, h); }
    g.strokeStyle = P.c; g.lineWidth = P.dpr;
    const heavy = 0.7 + 1.2 * au.mid, live = Math.round(n * (0.5 + 0.5 * Math.min(1, heavy)));
    for (let i = 0; i < live; i++) {
      const d = st.d[i], len = 14 * d[2] * P.dpr * heavy; d[1] += 900 * d[2] * P.dt * P.dpr * heavy; d[0] -= 400 * (P.v.wind ?? 0.3) * d[2] * P.dt * P.dpr;
      if (d[1] > h) { d[1] = -len; d[0] = Math.random() * w * 1.1; }
      g.globalAlpha = 0.12 + 0.3 * d[2] * (0.4 + 0.6 * P.amt);
      g.beginPath(); g.moveTo(d[0], d[1]); g.lineTo(d[0] + len * 0.433 * (P.v.wind ?? 0.3), d[1] - len); g.stroke();
    }
    g.globalAlpha = 1;
  }

  // Spectrum: the music's 64 bands as bars along the bottom, a ring round the middle, or a filled wave. Without music it
  // breathes slowly so it's never blank. Colour runs from the effect colour to the theme's second accent.
  function fxSpectrum(g, P) {
    const { w, h, st } = P, au = P.au, n = 64, style = P.v.style || "bars";
    st.s = st.s || new Float32Array(n); st.pk = st.pk || new Float32Array(n);
    const quiet = !au.live, k = Math.min(1, P.dt / (P.speed || 1) * 18);
    for (let i = 0; i < n; i++) {
      const tgt = quiet ? 0.07 + 0.05 * Math.sin(P.now * 1.3 + i * 0.22) : au.bands[i];
      st.s[i] += (tgt - st.s[i]) * k;
      st.pk[i] = Math.max(st.s[i], st.pk[i] - P.dt / (P.speed || 1) * 0.35);
    }
    g.clearRect(0, 0, w, h);
    const tall = h * (0.12 + 0.4 * P.amt) * (P.v.height ?? 1);
    const grad = (y0, y1) => { const gr = g.createLinearGradient(0, y0, 0, y1); gr.addColorStop(0, P.b); gr.addColorStop(1, P.c); return gr; };
    g.globalAlpha = 0.35 + 0.45 * P.amt;
    if (style === "ring") {
      const cx = w / 2, cy = h / 2, r0 = Math.min(w, h) * 0.17 * (1 + 0.12 * au.bass + 0.05 * au.beat), len = Math.min(w, h) * 0.22 * (P.v.height ?? 1);
      g.strokeStyle = P.c; g.lineCap = "round"; g.lineWidth = Math.max(2, r0 * 0.045);
      for (let j = 0; j < n * 2; j++) {                   // mirrored: bass at the top and bottom, highs at the sides
        const i = j < n ? j : 2 * n - 1 - j, a = (j / (2 * n)) * Math.PI * 2 - Math.PI / 2, l = 2 + st.s[i] * len;
        g.beginPath(); g.moveTo(cx + Math.cos(a) * r0, cy + Math.sin(a) * r0); g.lineTo(cx + Math.cos(a) * (r0 + l), cy + Math.sin(a) * (r0 + l)); g.stroke();
      }
    } else if (style === "wave") {
      g.fillStyle = grad(h - tall, h); g.beginPath(); g.moveTo(0, h);
      for (let x = 0; x <= 96; x++) { const u = x / 96, i = Math.min(n - 1, Math.floor(u * n)); g.lineTo(u * w, h - st.s[i] * tall); }
      g.lineTo(w, h); g.closePath(); g.fill();
    } else {
      const mirror = P.v.mirror !== false, cols = mirror ? n * 2 : n, bw = w / cols, gap = Math.max(1, bw * 0.22);
      g.fillStyle = grad(h - tall, h);
      for (let j = 0; j < cols; j++) {
        const i = mirror ? (j < n ? n - 1 - j : j - n) : j, bh = Math.max(2 * P.dpr, st.s[i] * tall);
        g.fillRect(j * bw + gap / 2, h - bh, bw - gap, bh);
        if (P.v.peaks !== false) g.fillRect(j * bw + gap / 2, h - st.pk[i] * tall - 4 * P.dpr, bw - gap, 2 * P.dpr);
      }
    }
    g.globalAlpha = 1;
  }

  registerFx("ascii", { label: "ASCII smoke", fps: 20, draw: fxAscii, controls: {
    mouse: { type: "toggle", label: "Stir the smoke with the mouse", value: true },
    beats: { type: "toggle", label: "Puff on the beat", value: true },
  } });
  registerFx("matrix", { label: "Matrix", draw: fxMatrix, controls: {
    trail: { type: "slider", label: "Trails", min: 0, max: 1, step: 0.01, value: 0.5, pct: true },
  } });
  registerFx("synthwave", { label: "Synthwave", draw: fxSynth, controls: {
    sun: { type: "toggle", label: "Sun", value: true },
  } });
  registerFx("stars", { label: "Starfield", draw: fxStars, controls: {
    warp: { type: "slider", label: "Bass warp", min: 0, max: 2, step: 0.05, value: 1, pct: true },
  } });
  registerFx("rain", { label: "Rain", draw: fxRain, controls: {
    wind: { type: "slider", label: "Wind", min: 0, max: 1, step: 0.01, value: 0.3, pct: true },
    lightning: { type: "toggle", label: "Lightning on big hits", value: true },
  } });

  registerFx("spectrum", { label: "Spectrum", draw: fxSpectrum, controls: {
    style: { type: "choice", label: "Shape", value: "bars", options: [{ value: "bars", label: "Bars" }, { value: "ring", label: "Ring" }, { value: "wave", label: "Wave" }] },
    height: { type: "slider", label: "Height", min: 0.3, max: 2, step: 0.05, value: 1, pct: true },
    mirror: { type: "toggle", label: "Bass in the middle", value: true, showIf: "style:bars" },
    peaks: { type: "toggle", label: "Peak caps", value: true, showIf: "style:bars" },
  } });

  // ---------------------------------------------------------------- wiring an app (FunkThemes 2)
  // call(name, ...args) -> Promise runs one of themes.ThemeApi's methods on the app's Python side.
  /** Point the engine at the app: its asset server, pausing behind games, the music feed and mute-all. */
  async function connect(call) {
    const FT = window.FunkTheme;
    try { FT.assetBase = await call("theme_asset_base"); } catch (e) {}
    FT.checkPause = () => call("theme_should_pause");
    FT.audioSource = () => call("theme_audio");
    try { FT.muteAll = !!(await call("theme_get_mute_all")); } catch (e) {}
  }
  /** Everything openEditor needs from the app; add current, brand and onDone. toast(message, isError) shows messages. */
  async function editorHooks(call, toast) {
    const say = toast || (() => {});
    let audio = null, lib = [];
    try { audio = await call("theme_audio_info"); } catch (e) {}
    try { lib = await call("theme_library"); } catch (e) {}
    return {
      library: lib, audio,
      onSave: async (t) => { const l = await call("theme_save", t); say(`Saved “${t.name}”. Your other Funk apps can use it too.`); return l; },
      onDelete: (t) => call("theme_delete", t.name),
      onExport: async (t) => { const r = await call("theme_export", t); if (r && r.ok) say("Exported to " + r.path); else if (r && r.error) say(r.error, true); },
      onImport: () => call("theme_import"),
      onPickVideo: (assets) => call("theme_pick_video", assets),
      onWeList: () => call("theme_we_list"),
      onWeImport: (id) => call("theme_we_import", id),
      onSetMuteAll: (on) => call("theme_set_mute_all", on),
      onSetAudioDevice: (name) => call("theme_set_audio_device", name),
      onError: (m) => say(m, true),
    };
  }

  window.FunkTheme = { PRESETS, RAINBOW, FX, apply, derive, swatch, openEditor, loadImage, registerFx, fxValues, asset,
                       isPaused, motion: () => motion(L.t), muteAll: false, connect, editorHooks, audioNow: audio, audioSource: null, version: 2, assetBase: null, paused: false, checkPause: null,
                       level: 0, audio: null };
})();
