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
 * FunkTheme.openEditor({ current, library, onChange, onSave, onDelete, onExport, onDone }) shows the maker.
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
    const tint = t.style === "image" ? (t.tint || "#20242c") : avg(cols);
    const s = clamp(t.strength == null ? 0.5 : Number(t.strength), 0, 1);
    const angle = (t.angle == null ? 135 : t.angle) + "deg";

    // Accent: explicit, else the most colourful stop, paired with the next one (or a hue step away).
    let acc = t.accent && t.accent.length ? t.accent : null;
    if (!acc) {
      const src = t.style === "image" ? [t.tint || "#e8915b"] : cols;
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
    const layer = t.style === "image" && t.image ? `url("${t.image}") center / cover no-repeat`
      : t.style === "solid" ? cols[0]
      : `linear-gradient(${angle}, ${cols.join(", ")})`;
    const page = `linear-gradient(${rgba(BASE, veil)}, ${rgba(BASE, veil)}), ${layer}`;

    // Surfaces: dark, nudged toward the theme colour, translucent so the background reads through.
    const k = 0.08 + 0.12 * s;
    const glass = s > 0 || t.style === "image";
    const surf = (base, a) => glass ? rgba(mix(base, tint, k), a) : mix(base, tint, k);
    return {
      "--bg": mix("#0e0f12", tint, 0.06 * s),
      "--page": page,
      "--page-blur": (t.style === "image" ? Number(t.blur) || 0 : 0) + "px",
      "--s1": surf("#15171c", 0.78), "--s2": surf("#1c1f26", 0.84), "--s3": surf("#252932", 0.9),
      "--solid": mix("#15171c", tint, k),
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
    if (!root && document.body) fxRun(t || PRESETS[0], vars);      // animated background (none for older themes)
    return vars;
  }
  /** CSS background for a small swatch of a theme. */
  function swatch(t) {
    const cols = stops(t);
    if (t.style === "image" && t.image) return `url("${t.image}") center / cover`;
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
            <div class="ft-seg">${["solid", "gradient", "rainbow", "image"].map((s) => `<button data-style="${s}" class="${t.style === s ? "on" : ""}">${{ solid: "Solid", gradient: "Gradient", rainbow: "Rainbow", image: "Picture" }[s]}</button>`).join("")}</div>
            ${t.style === "solid" ? `<div class="ft-cols" style="margin-top:12px">${colorBox(0, t.colors[0] || "#1d2b53")}</div>` : ""}
            ${t.style === "gradient" ? `<div class="ft-cols" style="margin-top:12px">${t.colors.map((c, i) => colorBox(i, c)).join("")}
              ${t.colors.length < 3 ? `<button class="ft-mini" data-a="addc" title="Add a third colour">+</button>` : `<button class="ft-mini" data-a="rmc" title="Back to two colours">−</button>`}</div>` : ""}
            ${t.style === "image" ? `<div class="ft-row"><button class="ft-btn soft" data-a="pick">${t.image ? "Change picture…" : "Choose a picture…"}</button>
              <input type="file" id="ft-file" accept="image/png,image/jpeg,image/gif,image/webp" hidden></div>
              <div class="ft-note">PNG, JPG, GIF or WebP. Big pictures are shrunk to 1920 px wide so themes stay small.</div>
              ${slider("blur", "Blur", 0, 30, 1, t.blur || 0, "px")}` : ""}
            ${t.style === "gradient" || t.style === "rainbow" ? slider("angle", "Angle", 0, 360, 5, t.angle ?? 135, "°") : ""}
            ${slider("strength", "Strength", 0, 100, 1, Math.round((t.strength ?? 0.5) * 100), "%")}
          </div>

          <div class="ft-sec"><label>Effect</label>
            <div class="ft-seg">${Object.entries(FX).map(([k, n]) => `<button data-fx="${k}" class="${(t.fx || "none") === k ? "on" : ""}">${n}</button>`).join("")}</div>
            ${t.fx && t.fx !== "none" ? slider("fxSpeed", "Speed", 20, 300, 5, Math.round((t.fxSpeed ?? 1) * 100), "%") + slider("fxAmount", "Amount", 0, 100, 1, Math.round((t.fxAmount ?? 0.7) * 100), "%")
              + `<div class="ft-row"><span class="ft-name">Colour</span><div class="ft-cols">${colorBox("fx", t.fxColor || derive(t)["--brand"])}</div></div>` : ""}
          </div>

          <div class="ft-sec"><label>Accent</label>
            <div><label class="ft-toggle"><input type="checkbox" data-a="autoacc" ${t.accent ? "" : "checked"}> Match the background automatically</label></div>
            ${t.accent ? `<div class="ft-cols" style="margin-top:10px">${colorBox("a0", t.accent[0])}${colorBox("a1", t.accent[1] || t.accent[0])}</div>` : ""}
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

    bg.addEventListener("input", (e) => {
      const el = e.target;
      if (el.dataset.c !== undefined) {
        const i = el.dataset.c;
        if (i === "a0" || i === "a1") t.accent[i === "a0" ? 0 : 1] = el.value; else if (i === "fx") t.fxColor = el.value; else t.colors[Number(i)] = el.value;
        el.parentElement.style.background = el.value;
        t.name = ""; apply(t); opt.onChange && opt.onChange(t);
        const nm = bg.querySelector("#ft-name"); if (nm) nm.value = "";
        return;                                       // don't redraw mid-drag (it would close the picker)
      }
      if (el.dataset.k) {
        const v = Number(el.value);
        t[el.dataset.k] = ["strength", "fxSpeed", "fxAmount"].includes(el.dataset.k) ? v / 100 : v;
        el.nextElementSibling.textContent = v + (el.dataset.k === "angle" ? "°" : el.dataset.k === "blur" ? "px" : "%");
        apply(t); opt.onChange && opt.onChange(t);
      }
    });
    bg.addEventListener("change", async (e) => {
      const el = e.target;
      if (el.dataset.c !== undefined) draw();
      if (el.dataset.a === "autoacc") { t.accent = el.checked ? null : [...derive({ ...t, accent: null })["--grad"].match(/#[0-9a-f]{6}/gi).slice(0, 2)]; change(); }
      if (el.id === "ft-file" && el.files[0]) {
        try { Object.assign(t, await loadImage(el.files[0]), { style: "image", name: "", accent: null }); if (t.strength == null || t.strength < 0.3) t.strength = 0.6; change(); }
        catch (err) { opt.onError && opt.onError(err.message); }
      }
      if (el.id === "ft-import" && el.files[0]) {
        try {
          const j = JSON.parse(await el.files[0].text());
          if (!j || j.funkTheme !== 1) throw new Error("That isn't a .funktheme file.");
          t = { ...PRESETS[0], ...j }; change();
        } catch (err) { opt.onError && opt.onError(err.message || "Couldn't read that theme."); }
      }
    });
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
      if (b.dataset.fx) { t.fx = b.dataset.fx === "none" ? null : b.dataset.fx; t.name = ""; if (t.fx && !t.strength) t.strength = 0.5; return change(); }
      if (b.dataset.style) {
        const s = b.dataset.style;
        if (s === t.style) return;
        if (s === "solid") t.colors = [t.colors[0] || "#1d2b53"];
        if (s === "gradient") t.colors = t.colors.length >= 2 ? t.colors.slice(0, 3) : [t.colors[0] && t.colors[0] !== "#0e0f12" ? t.colors[0] : "#ff7a3d", "#7c3aed"];
        if ((s === "gradient" || s === "rainbow" || s === "solid") && !t.strength) t.strength = 0.5;
        t.style = s; t.name = ""; t.accent = null;   // new background -> accent follows it
        if (s === "image" && !t.image) { change(); return bg.querySelector("#ft-file").click(); }
        return change();
      }
      const a = b.dataset.a;
      if (a === "addc") { t.colors.push(mix(t.colors[0], t.colors[1], 0.5)); t.name = ""; return change(); }
      if (a === "rmc") { t.colors = t.colors.slice(0, 2); t.name = ""; return change(); }
      if (a === "pick") return bg.querySelector("#ft-file").click();
      if (a === "import") return bg.querySelector("#ft-import").click();
      if (a === "save" || a === "export") {
        const name = bg.querySelector("#ft-name").value.trim();
        if (!name) { opt.onError && opt.onError("Give the theme a name first."); bg.querySelector("#ft-name").focus(); return; }
        t.name = name; t.funkTheme = 1;
        if (a === "save") { library = (await (opt.onSave && opt.onSave(t))) || library; draw(); }
        else opt.onExport && opt.onExport(t);
        return;
      }
      if (a === "close") return finish(false);
      if (a === "cancel") return finish(true);
    });
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
  const FX = { none: "None", ascii: "ASCII smoke", matrix: "Matrix", synthwave: "Synthwave", stars: "Starfield", rain: "Rain" };
  const GLYPHS = "ｱｲｳｴｵｶｷｸｹｺｻｼｽｾｿﾀﾁﾂﾃﾄﾅﾆﾇﾈﾉﾊﾋﾌﾍﾎﾏﾐﾑﾒﾓﾔﾕﾖﾗﾘﾙﾚﾛﾜﾝ0123456789ABCDEFZ+-*=<>";
  const fxState = { kind: null, cv: null, raf: 0, last: 0, st: {}, vars: null, t: null };
  function fxStop() {
    cancelAnimationFrame(fxState.raf); fxState.raf = 0;
    if (fxState.cv) fxState.cv.remove();
    fxState.cv = null; fxState.kind = null; fxState.st = {};
  }
  function fxRun(t, vars) {
    const kind = t && t.fx && FX[t.fx] && t.fx !== "none" ? t.fx : null;
    if (!kind) return fxStop();
    fxState.vars = vars; fxState.t = t;
    if (fxState.kind !== kind) { fxStop(); fxState.kind = kind; }
    if (!fxState.cv) {
      const cv = document.createElement("canvas"); cv.id = "ft-fx";
      cv.style.cssText = "position:fixed;inset:0;width:100vw;height:100vh;z-index:-1;pointer-events:none";
      document.body.prepend(cv); fxState.cv = cv;
    }
    if (!fxState.raf) fxState.raf = requestAnimationFrame(fxFrame);
  }
  function fxFrame(now) {
    const reduce = window.matchMedia && matchMedia("(prefers-reduced-motion: reduce)").matches;
    fxState.raf = reduce && fxState.last ? 0 : requestAnimationFrame(fxFrame);
    if (document.hidden || !fxState.cv) return;
    if (now - fxState.last < (fxState.kind === "ascii" ? 50 : 33)) return;   // 20-30 fps is plenty for a background
    const dt = Math.min(0.1, (now - (fxState.last || now)) / 1000) || 0.033; fxState.last = now;
    const cv = fxState.cv, dpr = Math.min(1.5, window.devicePixelRatio || 1);
    const w = Math.ceil(innerWidth * dpr), h = Math.ceil(innerHeight * dpr);
    if (cv.width !== w || cv.height !== h) { cv.width = w; cv.height = h; fxState.st = {}; }
    const g = cv.getContext("2d"), t = fxState.t, v = fxState.vars;
    const P = { w, h, dpr, dt: dt * (t.fxSpeed == null ? 1 : t.fxSpeed), speed: t.fxSpeed == null ? 1 : t.fxSpeed, au: audio(),
                amt: t.fxAmount == null ? 0.7 : t.fxAmount, a: v["--brand"], b: v["--brand2"], bg: v["--bg"], st: fxState.st, now: now / 1000,
                c: t.fxColor || v["--brand"] };
    ({ ascii: fxAscii, matrix: fxMatrix, synthwave: fxSynth, stars: fxStars, rain: fxRain })[fxState.kind](g, P);
  }
  // ---- audio: apps set FunkTheme.audio = {bass, mid, high, beat} (0..1; beat jumps to 1 on a kick and decays). Effects use it
  // so they move with the music; FunkTheme.level (older apps) is treated as bass.
  function audio() {
    const A = (window.FunkTheme && window.FunkTheme.audio) || {};
    const lv = clamp(Number(window.FunkTheme && window.FunkTheme.level) || 0, 0, 1);
    return { bass: clamp(A.bass ?? lv, 0, 1), mid: clamp(A.mid ?? lv * 0.7, 0, 1), high: clamp(A.high ?? lv * 0.5, 0, 1), beat: clamp(A.beat ?? 0, 0, 1) };
  }

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
        if (dd < 140) { const s = Math.exp(-dd / 30) * 14; vx += -ddy * s * 0.3; vy += ddx * s * 0.3; }
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
    if (!warm && au.beat > 0.85 && performance.now() - st.beatSeen > 180) {    // a beat throws a puff somewhere
      st.beatSeen = performance.now();
      puff(Math.random() * C, Math.random() * R, 5 + 6 * au.bass, 0.55 + 0.4 * au.bass);
    }
    if (moving) puff(st.mx, st.my, 2.5, 0.35);
  }
  function fxMatrix(g, P) {
    const { w, h, st } = P, au = P.au, fs = Math.round(15 * P.dpr), n = Math.ceil(w / fs);
    if (!st.y) { st.y = Array.from({ length: n }, () => Math.random() * -h / fs); st.v = Array.from({ length: n }, () => 8 + Math.random() * 14); g.fillStyle = P.bg; g.fillRect(0, 0, w, h); }
    g.fillStyle = rgba(P.bg, 0.16 - 0.06 * au.bass); g.fillRect(0, 0, w, h);    // longer trails on loud bass
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
    const glow = g.createRadialGradient(cx, y0, r * 0.6, cx, y0, r * (1.9 + au.beat));
    glow.addColorStop(0, rgba(P.a, 0.10 + 0.25 * au.beat)); glow.addColorStop(1, rgba(P.a, 0));
    g.fillStyle = glow; g.fillRect(0, 0, w, y0);
    const sun = g.createLinearGradient(0, y0 - r, 0, y0); sun.addColorStop(0, P.b); sun.addColorStop(1, P.a);
    g.save(); g.globalAlpha = 0.35 + 0.45 * P.amt; g.fillStyle = sun;
    g.beginPath(); g.arc(cx, y0, r, Math.PI, 0); g.fill();
    g.globalCompositeOperation = "destination-out";
    for (let k = 0; k < 7; k++) { const yy = y0 - r * (k / 7) ** 0.8 * 0.9, th = (1 + k) * P.dpr * 1.6; g.fillRect(cx - r, yy - th, 2 * r, th); }
    g.restore();
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
    const warp = 0.12 + 0.9 * au.bass * au.bass + 0.4 * au.beat;               // the bass punches the warp
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
    if (au.beat > 0.95 && au.bass > 0.75 && Math.random() < 0.25) st.flash = 1;       // lightning on the big hits
    if (st.flash > 0) { g.fillStyle = rgba("#dfe8ff", 0.10 * st.flash * st.flash); g.fillRect(0, 0, w, h); }
    g.strokeStyle = P.c; g.lineWidth = P.dpr;
    const heavy = 0.7 + 1.2 * au.mid, live = Math.round(n * (0.5 + 0.5 * Math.min(1, heavy)));
    for (let i = 0; i < live; i++) {
      const d = st.d[i], len = 14 * d[2] * P.dpr * heavy; d[1] += 900 * d[2] * P.dt * P.dpr * heavy; d[0] -= 120 * d[2] * P.dt * P.dpr;
      if (d[1] > h) { d[1] = -len; d[0] = Math.random() * w * 1.1; }
      g.globalAlpha = 0.12 + 0.3 * d[2] * (0.4 + 0.6 * P.amt);
      g.beginPath(); g.moveTo(d[0], d[1]); g.lineTo(d[0] + len * 0.13, d[1] - len); g.stroke();
    }
    g.globalAlpha = 1;
  }

  window.FunkTheme = { PRESETS, RAINBOW, FX, apply, derive, swatch, openEditor, loadImage, level: 0, audio: null };
})();
