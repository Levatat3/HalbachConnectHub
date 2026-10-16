# The site model and the build pipeline

The site stops being hand-written files and becomes **data owned by Halbach**: a model of pages and blocks, a theme, and some settings. The builder turns that model into a plain static folder and the existing publisher deploys it. This document specifies the model, what the builder emits, and the guarantees every generated site gets.

## 1. The model (`site.json`)

Stored in the state folder (`<state>/site/site.json`, written atomically, history kept). Versioned: `"version": 1`; migrations run on load and always back the file up first.

```json
{
  "version": 1,
  "brand": {
    "name": "EXAMPLE",
    "mark": "Ex",
    "tagline": "games, projects and streams",
    "handle": "example",
    "footer": "built by hand",
    "icon": { "mode": "generated", "file": null }
  },
  "theme": { "id": "neutral-dark", "vars": { "--accent": "#7cc4ff" }, "mode": "auto" },
  "settings": {
    "lang": "en",
    "base_url": "https://example.com",
    "csp": "enforce",
    "analytics": "cloudflare",
    "search": false,
    "nav_style": "top",
    "indexable": true
  },
  "pages": [
    {
      "id": "home", "slug": "", "label": "HOME",
      "title": "Example", "description": "Games, projects and streams.",
      "nav": true, "listed": true, "enabled": true, "draft": false,
      "og_image": null, "theme": null, "area": null,
      "blocks": [
        { "id": "b1", "type": "hero", "props": { "heading": "Hello", "typed": true } },
        { "id": "b2", "type": "cards", "props": { "items": [ { "title": "Play", "text": "A game", "page": "game" } ] } }
      ]
    }
  ],
  "collections": [],
  "redirects": [ { "from": "/old", "to": "/new/", "status": 301 } ]
}
```

### Pages

| Field | Meaning |
|---|---|
| `id` | Stable internal name (`[a-z0-9-]{1,40}`); never changes when the address or label does. |
| `slug` | The address: `""` is the home page; otherwise `[a-z0-9-]{1,40}` with optional `/` for nesting (`tools/forge`), max depth 3. Lowercase only (Pages is case-sensitive). |
| `label` | Text in the nav. |
| `title`, `description` | Used for `<title>`, meta description, Open Graph. Required for listed pages (the health check warns otherwise). |
| `nav` | Show in the nav bar. |
| `listed` | In the sitemap and indexable. `false` means **unlisted**: no nav, no sitemap, `<meta name="robots" content="noindex">`. Not private. |
| `enabled` | `false` means not built at all (a safe "remove the Forge tab" without losing its blocks). |
| `draft` | Built only in Preview, never in Publish. |
| `og_image` | Image module or file for link previews; falls back to a generated card. |
| `theme` | Optional per-page theme override. |
| `area` | Owned by an **area** (see AREAS.md): its blocks come from the area's approved version and are confined to its slug prefix. |
| `blocks` | Ordered list of `{id, type, props}`. Props are plain data validated against the block's schema. |

**Reserved slugs** (the builder refuses them): `modules`, `assets`, `plugins`, `_halbach`, `api`, `admin`, `404`, `robots`, `sitemap`, and any path that collides with a file the builder or an "extra files" folder emits.

**Nesting:** a slug may contain `/`; the nav can group by first segment (`nav_group`). Parent pages are not required to exist.

### Blocks

`{id, type, props}`. `type` must be a registered block (built-in or plugin). `props` are validated server-side against the block's `fields` schema (types in PLUGINS.md); unknown keys are dropped; every string is length-capped; URLs must be `https:`, `http:`, `mailto:`, `#`, or relative to the site (never `javascript:` or `data:`); module references must exist and be of an allowed kind. **Nothing a block receives is ever treated as HTML.**

### Collections (blog, changelog, news)

```json
{ "id": "posts", "label": "Posts", "slug": "blog", "source": "module:posts-folder",
  "sort": "date-desc", "per_page": 10, "feed": true, "tags": true }
```

A collection is a folder of Markdown files with front matter (`title`, `date`, `tags`, `draft`, `summary`). The builder emits an index (paginated), one page per entry, tag pages, and an Atom feed. Entries arrive as an ordinary static module (so they can also come from an area submission). Draft entries are skipped.

### Redirects

Written to `_redirects` (static rules only), counted and size-checked: fails the build if over 2,000 rules or 100 KB. Auto-generated redirects: renamed pages (old slug to new), legacy `#hash` addresses are handled in the runtime, optional case variants of slugs.

## 2. What the builder emits

For a model plus the registered plugins the builder writes a stage folder:

```
stage/
  index.html                      home page (real HTML file)
  <slug>/index.html               one per enabled page (nested slugs nest)
  404.html                        custom not-found page (themed)
  site.json                       the model, public subset (no area keys, no draft pages)
  manifest.json                   modules (unchanged format, see README)
  modules/<id>/...                static modules (as today)
  _halbach/runtime.<hash>.js      the generic runtime
  _halbach/theme.<hash>.css       theme CSS with the variable overrides baked in
  _halbach/blocks/<type>.<hash>.js  only the blocks (and views) actually used
  _halbach/icons/...              favicons and the web app manifest
  robots.txt  sitemap.xml  feed.xml (per collection)  _headers  _redirects
  <area-slug>/files/...           approved area files, confined to the area's folder
```

### The page template

Every page is generated from one template so behaviour is uniform:

- `<html lang>`, `<meta charset>`, viewport, `theme-color`, `color-scheme`.
- `<title>`, description, `canonical`, `robots` (from `listed`), Open Graph and Twitter card tags, optional JSON-LD (`WebSite`, `Person`, `BreadcrumbList`).
- Icons: SVG, 32 px PNG, `.ico`, Apple touch icon, web app manifest.
- Theme CSS and runtime as hashed external files (no inline script, ever).
- Skip link, landmarks (`header`, `nav`, `main`, `footer`), one `h1`, visible focus styles.
- The page's blocks as inert JSON in `<script type="application/json" id="hs-page">`, drawn by the runtime.
- A `<noscript>` message, and for text-only blocks (hero, text, links) a server-rendered fallback so crawlers and no-JS readers see real content. (Phase 5; Phase 1 ships the JSON approach.)

### Headers within the 100-rule cap

Only global wildcard rules, built from what the used blocks *declare* they need:

```
/*
  Content-Security-Policy: default-src 'self'; script-src 'self' <declared>; style-src 'self' 'unsafe-inline'; img-src 'self' data: <declared>;
      connect-src 'self' <live host> <declared>; frame-src <declared>; object-src 'none'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'
  X-Content-Type-Options: nosniff
  Referrer-Policy: strict-origin-when-cross-origin
  Permissions-Policy: camera=(), microphone=(), geolocation=()
  Strict-Transport-Security: max-age=31536000
/_halbach/*
  Cache-Control: public, max-age=31536000, immutable
/modules/*  (and each area's /<slug>/*)
  Content-Security-Policy: <stricter: no script at all>
```

CSP is assembled automatically: a block declares `csp` hosts in its manifest (the Twitch block declares the Twitch player frame, the GitHub block declares the GitHub API), so **the policy always equals "what the used blocks need"**, and the hub's Site screen shows the final policy and which block asked for each host. Cloudflare's analytics beacon hosts are added when analytics is on. `settings.csp` is `enforce`, `report-only` or `off`.

### Quality gates (run on every build, shown in the window)

Errors block publishing; warnings do not.

| Check | Level |
|---|---|
| File count over 20,000; any file over 25 MiB; `_headers` over 100 rules; `_redirects` over limits | error |
| Page without title/description (listed pages); duplicate slugs; slug collision with an emitted file | error / warning |
| Block with a missing required prop; reference to a module that does not exist or has the wrong kind | error |
| Internal link to a page that is disabled or missing; broken anchor | warning |
| Image without alt text; skipped heading level; two `h1`s | warning |
| Theme colour contrast below WCAG AA for text/background and accent/background pairs | warning |
| Image over 1 MB; page whose JSON exceeds 100 KB | warning |
| Plugin file changed since it was trusted | error (plugin disabled until re-approved) |
| Unlisted page linked from a listed page's nav | warning |

## 3. Preview, publish, history

- **Preview:** the hub serves the freshly built stage at `http://127.0.0.1:<port>/preview/` (loopback only, read-only), including drafts. Live blocks work against the local hub.
- **Test publish:** uploads to the `preview` branch address (already implemented).
- **Diff before publish:** a list of pages and files added, changed and removed since the last publish.
- **Publish:** unchanged mechanics (stage, deploy, verify the build id), now with the quality gates in front.
- **History:** every save of `site.json` is a numbered snapshot (the last 100 kept). Restore any snapshot; "undo" is the previous one. Deployments also stay in Cloudflare, so a bad publish can be rolled back by redeploying the previous snapshot.
- **Scheduling:** "publish at a time" is a hub timer (it only fires while the hub runs, and says so). Drafts plus a manual Publish remain the main flow.
- **Export/import:** the whole site (model, plugin list, module sources) as one zip, and import from a zip, so a site can move between machines or be shared as a template.
- **Headless:** `halbach build --out DIR` and `halbach publish` run without the window, for scheduled tasks or CI.

## 4. Site-level features

| Feature | Notes |
|---|---|
| Brand and icons | Name, mark, tagline, footer; the favicon set (SVG, PNG sizes, ICO, Apple touch, web manifest) is **generated from the mark** by a small stdlib renderer, or an uploaded image is used. |
| Theme picker and overrides | Pick a theme; override its CSS variables from colour pickers; `mode: light/dark/auto`. |
| Navigation | Order, labels, hide per page, grouping by first slug segment, external links as nav items. |
| 404 page | Themed; lists nothing private. |
| Search | Optional: a client-side index (`search.json`: titles, descriptions, headings, first paragraph) and a search block. No external service. |
| Analytics | `none`, `cloudflare` (cookieless beacon; hosts added to CSP). Other providers only via a declared plugin. |
| Contact/forms | A form block that posts to an area's inbox (review before anything happens) instead of a third-party form service. |
| Embeds | Twitch, YouTube (privacy-friendly host), generic sandboxed embed with a host allow-list. |
| Media | Image blocks warn on large images; optional thumbnail generation if the optional `halbach[images]` extra (Pillow) is installed; otherwise none, with a warning. |
| Multi-language | `lang` per site and per page; `hreflang` links when pages declare translations (Phase 6). |
| Legacy compatibility | The runtime redirects old `#hash` addresses to their real page. |
| Accessibility | Skip link, landmarks, focus rings, reduced-motion respected by every built-in effect, contrast checks on themes. |
| Privacy | No cookies, no third-party requests unless a block declared them (and the CSP shows them). |
