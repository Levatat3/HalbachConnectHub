# Plugins: blocks, views, themes, templates

Everything the site can show or look like is a plugin; the built-ins are just plugins that ship in the package. A user adds one by dropping a folder into `<state>/plugins/<kind>/<id>/`, or by installing a zip from the window. **Halbach never downloads plugins by itself.**

## 1. Kinds

| Kind | What it is | Result |
|---|---|---|
| **block** | A kind of content on a page (a countdown, a guestbook, a map). Has a settings schema and a render script. | Appears in the "add block" catalog; its form is generated from the schema. |
| **view** | A look for live data (cards, pets, a leaderboard, a chart). Receives JSON from a live module. | Appears in the live module's "How the site shows it" list; may declare an auto-created nav tab. |
| **theme** | Colours, fonts, spacing, layout and optional effects as CSS plus variables. | Appears in the theme picker; variables become colour pickers. |
| **template** | A starting site model (pages and blocks, no code). | Appears in "New site from a template". |
| **importer** *(Phase 6)* | Reads another site format into a model (the legacy starter site, a folder of Markdown). | Appears in "Import". |
| **exporter** *(Phase 6)* | Writes extra outputs at build time (an extra feed, a print version). Build-time Python, so it is a higher trust level (below). | |

Blocks, views and themes run **in the visitor's browser** on the public site. Templates are data. Importers and exporters are Python that runs **on the owner's PC**, so they are the most sensitive kind and are off by default, trusted individually.

## 2. Folder layout and manifest

```
plugins/block/countdown/
  plugin.json        required
  render.js          the block's script (declared in "files")
  render.css         optional
  README.md          shown in the window
  icon.svg           optional
```

`plugin.json`:

```json
{
  "kind": "block",
  "id": "countdown",
  "name": "Countdown",
  "version": "1.2.0",
  "hostapi": 1,
  "author": "Name <contact>",
  "license": "MIT",
  "description": "Counts down to a date.",
  "category": "media",
  "files": ["render.js", "render.css"],
  "fields": [
    { "key": "to", "label": "Target date", "type": "text", "required": true, "max": 40 },
    { "key": "label", "label": "Label", "type": "text", "max": 80 }
  ],
  "csp": { "connect-src": [], "frame-src": [], "img-src": [], "script-src": [] },
  "needs": ["live"],
  "isolated": false
}
```

Rules enforced when a plugin is loaded: `id` is `[a-z0-9-]{1,40}` and unique within its kind; `files` exist, are inside the folder (no `..`, no symlinks), and are `.js`, `.css`, `.json`, `.svg`, `.png`, `.woff2`; total size is capped (default 2 MB); `hostapi` is an integer the runtime supports; `csp` hosts must be `https://` origins (no wildcards except a subdomain wildcard such as `https://*.example.com`, flagged in the UI).

## 3. Field types (the settings schema)

The same schema validates the data server-side **and** generates the form in the window, so a plugin author writes the schema once and gets both.

| Type | Value | Validation |
|---|---|---|
| `text` | string | `max` (default 200), no control characters |
| `longtext` | string | `max` (default 4000) |
| `markdown` | string | `max` (default 20000); rendered by the safe renderer only |
| `url` | string | `https`/`http`/`mailto`/`#`/site-relative only |
| `number` | number | `min`, `max`, integer option |
| `bool` | boolean | |
| `select` | one of `options` | must be in the list |
| `list-text` | list of strings | `max_items` (default 50), each `max` |
| `links` | list of `{label, url}` | as `url`, `max_items` |
| `cards` | list of `{title, text, url|page}` | `page` must be a real page id |
| `module` | a module id | must exist; `modules` limits the kind (`static`, `live`, `doc`, `gallery`, `image`, ...) |
| `area` | an area id | must exist |
| `page` | a page id | must exist |
| `color` | `#rrggbb` | pattern |
| `image` | module id, file in a module, or `https` URL | as above |

Unknown keys are dropped. Required props must be present. Strings are control-character-stripped. Nothing is ever interpreted as HTML.

## 4. Host API v1 (what a browser-side plugin may rely on)

A plugin script registers itself and receives a small, stable context. It must build DOM with `ctx.el` / `textContent`, never `innerHTML` with data.

```js
HalbachSite.registerBlock("countdown", function render(container, props, ctx) {
  const box = ctx.el("div", "hs-countdown");
  container.appendChild(box);
  const tick = () => { box.textContent = props.label + ": " + remaining(props.to); };
  tick(); const t = setInterval(tick, 1000);
  ctx.onRemove(() => clearInterval(t));
});

HalbachSite.registerView("leaderboard", function render(container, data, ctx) { /* data is the filtered JSON */ });
```

| Member | Purpose |
|---|---|
| `ctx.el(tag, className, text)` | Create an element; text goes in with `textContent`. |
| `ctx.markdown(text)` | Safe Markdown to a DOM node. |
| `ctx.module(id)` | The module's manifest entry (type, entry path, files). |
| `ctx.url(path)` | Resolve a path relative to the site root (works from nested pages). |
| `ctx.live.run(box, moduleId, render, opts)` | Poll a live module with the standard online/offline/retry handling. |
| `ctx.theme` | `{id, vars}` of the active theme. |
| `ctx.page` | `{id, slug, title}`. |
| `ctx.onVisible(fn)` | Call when the block scrolls into view (lazy work). |
| `ctx.onRemove(fn)` | Clean-up hook (timers, listeners). |
| `ctx.reducedMotion` | `true` if the visitor asked for less motion; animations must honour it. |

Versioning: `hostapi` is an integer. A runtime that supports 1..N loads plugins declaring any of them; a plugin declaring a higher number than the runtime knows is disabled with a clear message. Breaking changes bump the number; additions do not.

## 5. Themes

```
plugins/theme/paper/
  plugin.json   {"kind":"theme", "id":"paper", "vars": {"--bg":"#fafafa","--text":"#1a1a1a","--accent":"#0b57d0"}, "effects": []}
  theme.css
```

- The runtime outputs **semantic markup with stable class names** (`hs-header`, `hs-nav`, `hs-page`, `hs-block`, `hs-card`, `hs-footer`, ...); a theme styles those. The class list is part of the host API.
- Colours, fonts and spacing are CSS variables declared in `vars`; the window turns each into a picker and stores overrides in the model, so changing a colour never means editing CSS.
- `effects` are opt-in modules a theme declares (CRT scanlines, glyph rain, typed headings). Every effect respects `prefers-reduced-motion`.
- Fonts: system stacks by default. A theme may ship `.woff2` files (self-hosted, content-hashed). External font hosts require a declared `csp` entry and are flagged.
- Layout options a theme may expose: `nav_style` (`top`, `side`, `none`), container width, density.
- Print styles and a high-contrast variant are part of the built-in themes' acceptance list.

## 6. Trust model

Plugin code runs with the full privileges of the public site: it can read any public data and call any origin the CSP allows. It **cannot** reach the hub, the state, the tunnel or the owner's PC. So:

1. **Installing is explicit.** A plugin shows up as *Untrusted* with its manifest, file list, sizes and a SHA-256 of its files. It does nothing until the owner presses **Enable**.
2. **Hash pinning.** Enabling records the hash. If any file changes later (an update, or tampering), the plugin is disabled and a build error names it until it is re-approved.
3. **What it asks for is visible.** The Enable dialog lists the `csp` origins it needs and the block types it adds. The final site policy shows which plugin asked for which host.
4. **Least privilege by default.** The CSP is built only from the plugins actually used on pages.
5. **Optional isolation** (`"isolated": true`): the block is drawn inside a sandboxed iframe (opaque origin, no cookies, no storage, no same-origin access) and talks to the page through a tiny `postMessage` API. Slower and less capable, intended for third-party blocks the owner does not fully trust. Area-authored pages can only use built-in blocks or isolated plugins.
6. **Python-side kinds** (importer, exporter) are never enabled by default, run with the hub's privileges, and are shown with a stronger warning and a separate switch.
7. **No auto-update, no auto-download, no remote code loading.** A plugin zip is read from disk; the window verifies its layout and size before copying it in.

## 7. The developer kit

- `halbach plugin new <kind> <id>` scaffolds a plugin from a template (manifest, render file, README, a fixture page).
- `halbach plugin check <folder>` validates the manifest, schema, file rules, size, `hostapi`, forbidden constructs in browser scripts (`eval`, `new Function`, `document.write`, string `setTimeout`, writes of data into `innerHTML`/`outerHTML`/`insertAdjacentHTML`, inline event attributes), and reports in plain language. It is a lint, not a sandbox, and says so.
- `halbach plugin pack <folder>` makes the zip with a manifest hash.
- **Dev preview:** a hub route renders any plugin against sample props in isolation, with live reload on file change.
- `docs/PLUGINS.md` ships a complete worked example for each kind, and the test suite runs every example through `check` and a build.

## 8. Distribution (later)

A plain-text **index file** format (JSON list of name, version, URL, SHA-256) that a user may add by hand lets the window show "available plugins" with the hash pre-filled; install still downloads only on an explicit click and verifies the hash. This is deferred: the folder-and-zip route is enough for the first releases.
