# The modular site (Halbach 0.4)

Until 0.3 the site was a folder you edited by hand. In 0.4 you can let Halbach **build the site for you**: pages, menu, theme, security headers, sitemap and all. You change things in a window; nothing needs a code edit or an assistant. The old way keeps working, and it is still the default.

## Two modes

| | Classic (default) | Modular |
|---|---|---|
| The site is | the folder you set in Settings, uploaded as it is | built from the **Site** screens |
| Pages | one `index.html` with tabs | a real page per address (`/play/`, `/about/`, ...) |
| Publishing | unchanged | the same button; it builds first |
| Switch | Site → "Use the modular site" | Site → "Switch back to classic" |

Switching never deletes anything. Your site folder is only ever *read* (for extra files, such as images or a game), and your modular pages stay saved when you switch back.

## First run

1. Open **Site**.
2. Press **My current site** to import what visitors see today, or pick a template (Blank, Personal, Streamer).
3. Open **Preview**. It builds the site and shows it in the window with the same security headers Cloudflare will send. Use Phone / Tablet / Desktop to check sizes.
4. Read the checks on **Overview**. Errors block publishing; notes are advice.
5. Press **Use the modular site**, then **Publish**.

## Pages

A page has a name (shown in the menu), an address, a title, a description and a list of **blocks**. Pages can be switched off (taken down, kept), hidden from the menu, unlisted (kept out of search and the sitemap) or marked as a draft (visible in Preview, never published). The home page is the one with the empty address and cannot be deleted.

Adding a page such as `levmorium.com/funkworkshop` is **Site → Pages → Add page**.

## Blocks

Every block's settings form is generated from its manifest, so a block someone else writes gets a form for free.

| Block | Category | What it does |
|---|---|---|
| Cards | layout | A grid of cards that link to pages or addresses. |
| Divider | layout | A horizontal rule with an optional label. |
| Embed a page | media | Shows one of your pages or an address inside a sandboxed frame. |
| File downloads | media | A list of downloadable files from a module. |
| Gallery | media | A grid of images from a gallery module. |
| GitHub projects | modules | Your public repositories, read live from GitHub. |
| Hero | text | A big heading with an optional typed effect, a prompt line and a tagline. |
| Image | media | One image. |
| Links | layout | A row or list of links. |
| Live data | modules | Live data from your PC with a chosen look (cards, or a plugin's view such as Pets). |
| Module list | modules | A grid of your modules (docs, pages, galleries, live data). |
| Show a module | modules | Shows one module in place: a doc, page, gallery, image, file or live data. |
| Flash game (Ruffle) | media | Plays a Flash file in the browser using the Ruffle emulator. |
| Text | text | Markdown text. Raw HTML is shown as text, never rendered. |
| Twitch player | media | An embedded Twitch stream. |
| YouTube | media | A video or a channel's uploads (privacy-friendly embed). |

The *Drop box* block is reserved for the areas update and is not offered yet.

## Themes and design

- **Neutral dark**: A calm dark theme with no effects. The suggested starting point.
- **Neutral light**: A clean light theme with no effects.
- **Retro terminal**: Dark CRT green and amber: the look of the original starter site.

A theme is a set of CSS variables plus a stylesheet. **Design** lets you pick a theme and override its colours (`--bg`, `--text`, `--accent` and so on) without editing any file. The brand mark and name feed the menu and a generated icon; if your site folder has `favicon.svg`, `favicon.ico`, `favicon-32.png` or `apple-touch-icon.png`, those are used instead.

## What gets published

For every page Halbach writes a real `index.html` containing the page shell, a menu and the page's data as inert JSON. A small shared runtime reads that JSON and draws the blocks, loading only the scripts the page needs. Alongside that it writes:

- `_headers`: a **content-security-policy** built from what the blocks you actually use declared (Twitch's player host appears only if you use a Twitch block), plus no-sniff, referrer and permissions headers, and long-lived caching for hashed assets.
- `robots.txt` and `sitemap.xml` (the sitemap needs your site address in **Site settings**).
- `404.html`, `_redirects` from your redirect list, and `manifest.json` for modules and live data.

Scripts and stylesheets get content-hashed names, so a changed file can never be served stale.

### The security policy: report-only first

The policy has three modes in **Site settings**: *Report only* (browsers log problems but block nothing), *Enforce*, and *Off*. Start in report-only, open every page in Preview, then switch to Enforce. Pages in your site folder and your static modules (which may use inline scripts) get a more relaxed policy under `/modules/*`, and they run in a sandboxed frame without access to your site's storage. Your own pages embedded from a path on the same site (the Forge viewer, for example) run as normal.

## Redirects and the menu

**Menu & redirects** holds extra menu links to other sites and a redirect list (old address to new address), so links you shared keep working after you rename or remove a page. Redirects are limited to what Cloudflare Pages allows (2,000 and 100 KB).

## History, export and import

Every change is saved as a snapshot (the last 100 are kept). **History** restores any of them, and a restore is itself saved. **Download the site file** saves pages, design and settings as one JSON file; **Load a site file** replaces the site with one (the old one stays in History). Uploaded files and modules are not inside the file.

## Command line

```
halbach site check
halbach site build --out out
halbach site export my-site.json
halbach site import my-site.json        (close the window first)
halbach site import-classic             (close the window first)
```

`check` exits 1 when something blocks publishing, which makes it usable in a script. `import` refuses a file from a newer Halbach, a malformed file or an oversized one, and leaves the site alone.

## Checks

Blocking: a page that points at a page that does not exist, a block whose required setting is empty, an unknown module, two pages with the same address, a missing home page, a theme that is not available, files that collide with a page address or a reserved name, and Cloudflare's limits (20,000 files, 25 MB per file, header and redirect sizes).

Advice: template placeholder text left in a page, an image without a description, low text contrast in the theme, a link to a path that is not in the site, large files, the security policy still in report-only, and no site address for the sitemap.

## What is not here yet

Planned, not built: user-installed block and theme plugins, **areas** (a page a collaborator can send content to, which you review before it goes live), collections, search, and the release templates. The design for them is in [docs/design](design/); the files that exist for them (the Drop box block, plugin manifests for user plugins) are placeholders.

## Known limits

- The builder renders pages in the visitor's browser from JSON, so a page without JavaScript shows a short notice instead of its blocks. Pre-rendering is planned.
- Cloudflare's Browser Cache TTL can override headers on assets; hashed names avoid the problem for Halbach's own files.
- Previews use `/preview/` addresses and the hub's own port; they are for you only and are never uploaded.
- Text blocks use a safe Markdown subset (headings, lists, links, code, tables, quotes); raw HTML is shown as text.
