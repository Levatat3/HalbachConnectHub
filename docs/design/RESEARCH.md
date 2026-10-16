# Research notes behind the modular design

What was checked, what it means for the design, and where it came from. Facts that can go stale (limits, plans) are marked with the date they were read: **2026-10-16**.

## 1. Hosting limits (Cloudflare Pages, free plan)

Source: Cloudflare Pages docs, "Limits" (page last updated 2026-09-05).

| Limit | Value | What it forces |
|---|---|---|
| Files per site | 20,000 | The builder counts every file it emits and refuses to publish over the cap, with a clear message. Content collections and media need a budget. |
| Size of one file | 25 MiB | Same check per file. Bigger files belong on R2 (a "large file" setting could write the link instead of uploading). |
| `_headers` rules | **100 per file**, 2,000 characters per header | **No per-page header rules.** Security headers are a handful of global wildcard rules; per-page things (`noindex`) go in the page's own `<meta>`. Each area gets at most one rule. |
| `_redirects` | 2,000 static + 100 dynamic, and a **100 KB file cap** (rules past the cap are silently dropped) | The builder writes redirects deliberately, measures the file, and fails loudly instead of silently truncating. |
| Custom domains | 100 per project | Not a constraint. |
| Builds | 500 per month, one at a time | Only applies to git-connected builds. Direct uploads (what Halbach uses) are not builds. |
| Functions | Count against the Workers plan quota | Halbach avoids Functions entirely, so the 100,000 requests/day cap never applies. |
| Preview deployments | Unlimited | "Test publish" is free to use as often as wanted. |

Also observed on a live Pages site: `/folder/` serves `folder/index.html`, a `404.html` at the root is used automatically, and `.html` is stripped from URLs. So **real-path pages are just folders with an `index.html`**, no server rules needed.

Observed bug-class (this project, 2026-10-15): Cloudflare's *Browser Cache TTL* zone setting overrides the site's own `Cache-Control` for `.js`/`.css`, so changed scripts were served stale for 4 hours. Content-hashed file names (already implemented in the publisher) are the fix and become mandatory for every generated asset.

Also observed: Pages injects Cloudflare Web Analytics (`static.cloudflareinsights.com` beacon). A strict Content-Security-Policy must allow it, or it is blocked and the console fills with errors.

## 2. Truly private pages: Cloudflare Access

Source: Cloudflare Zero Trust docs and blog (free plan: up to 50 users; applications can be protected per path or subdomain; sign-in by emailed one-time PIN or an identity provider).

Consequence: a static site cannot hide a page by itself. "Unlisted" means hard to find, nothing more. For a private area the design documents a three-step recipe (create an Access application on `/area-path/*`, allow named emails) and the hub shows a clear label: **unlisted is not private**. Halbach never draws a fake password box in the page.

## 3. Upload safety (OWASP File Upload Cheat Sheet)

Source: OWASP Cheat Sheet Series, "File Upload". Its core rules, and how the area/drop design answers each:

| OWASP rule | Design answer |
|---|---|
| List allowed extensions; validate type, never trust the `Content-Type` header | Per-area extension allow-list **plus** magic-number sniffing for the common types; both must agree. |
| Rename files; limit name length and characters | Files are stored under generated ids; the original name is kept only as sanitised metadata (ASCII-safe, 80 characters, no path parts). |
| Size limits; archive bombs (zip bombs) | Content-Length checked before reading; streamed to a temp file with a hard cap; archives checked for entry count, total uncompressed size and compression ratio before extraction. |
| Only authorised users may upload | Per-area passphrase (scrypt-hashed), constant-time compare, per-IP and per-key rate limits, lockout after repeated failures, instant revocation. |
| Store outside the web root; serve through a handler | Uploads land in the hub's private area folder, never in a served folder; nothing is served until a human approves it and the owner publishes. |
| Active content (XSS) in public files | Approved area pages are data, not code: blocks are drawn with `textContent`, Markdown is rendered without HTML, links are scheme-checked, images must be same-area or `https`. SVG is sanitised or rejected. Scripts are off by default and need an explicit per-area switch. |
| Antivirus / content disarm | Out of scope for the stdlib-only core; a documented hook lets a plugin or an external scanner veto a bundle before review. |
| CSRF | The drop endpoint takes a bearer-style key in a header, not cookies, so there is no ambient authority to forge. |
| Overwriting existing files | Submissions are written to fresh folders; approval creates a new numbered version; nothing is overwritten in place. |

Free-plan note: Cloudflare proxies cap one request body at about **100 MB**, so single uploads are capped below that (default 50 MB); bigger transfers need chunking (a later item) or another route.

## 4. What a site system normally contains (feature survey)

Surveyed by reading how Hugo, Jekyll, Eleventy, Astro, Decap CMS, Ghost and Grav describe themselves. The recurring building blocks, which the plan's feature inventory is built from:

- **Content model:** pages, collections of dated entries with front matter (title, date, tags, draft), taxonomies (tags/categories), pagination, per-entry URLs, RSS/Atom feeds.
- **Layout and theming:** templates/layouts, partials, themes that can be swapped, design tokens (CSS variables), dark/light, print styles.
- **Plugins:** hooks at build time (Jekyll/Eleventy/Hugo modules) and at run time (Astro "islands": only the interactive parts ship JavaScript).
- **Config-driven editing:** Decap CMS defines collections and field widgets (string, markdown, image, select, list, relation) in a config file and generates the editor forms from it. This is the model for Halbach's schema-generated block forms.
- **SEO and sharing:** title/description, canonical URL, Open Graph and Twitter card tags, sitemap.xml, robots.txt, structured data (JSON-LD), favicons and a web app manifest.
- **Performance:** asset hashing and long-cache headers, image resizing/modern formats, lazy loading, a size budget.
- **Quality gates:** link checking, accessibility linting, spell/format checks, build warnings.
- **Operations:** preview before publish, draft/published states, scheduled publishing, history and rollback, redirects, custom 404, analytics, search, forms.
- **Collaboration:** roles and approval flows (editorial workflow in Decap, Ghost staff roles).
- **Portability:** export/import of the whole site, headless/CI builds, content in plain files under version control.

Everything in that list is either in the plan, deliberately deferred (with the reason), or explicitly out of scope (with the reason). See the feature inventory in `MODULAR-PLAN.md`.

## 5. Things deliberately not researched into adoption

- A drag-and-drop page builder (the owner does not want one; forms and lists only).
- Server-side rendering or a database (static output only; dynamic parts ride the live hub through the tunnel).
- Third-party fonts or tag managers by default (privacy and CSP cost; allowed only through a declared plugin).
