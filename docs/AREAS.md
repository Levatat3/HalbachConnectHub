# Areas: let someone fill a page of your site without giving them any keys

An **area** is a page (or a few) on your site that one other person can send content to. They never log in to anything of yours: no Cloudflare, no GitHub, no Halbach. They send a small zip over HTTPS, it waits in a review queue on your PC, and **you** approve it. It goes live at your next Publish.

The full design is in [design/AREAS.md](design/AREAS.md). This page says what is built in 0.5 and how to use it.

## For you (the owner)

1. **Set up the drop address once.** In Settings, set the **live host** (the tunnel's host name, for example `live.example.com`) and start the tunnel. Areas are reached through the same tunnel your live modules use. Your PC and the tunnel must be on while someone sends.
2. Open **Areas → New area**. Give it a name and an address (`/funkworkshop/`). Halbach shows its **passphrase once** (26 random characters). It keeps only a scrypt fingerprint of it. Send the passphrase to the person privately.
3. When they send something, **Areas** shows a badge. Open the area, then **Review**: you see how many pages, blocks and files, and what changed from what is live. **Preview it** builds the site with their content in your local preview. **Approve** or **Reject** (with a note they can read).
4. **Publish** as usual. Approving does not put anything on the internet by itself — unless the area is on the **Auto** trust level (below), where approved content is published by itself, debounced to at most one automatic publish every five minutes.

Everything else is in the area's tabs: **Versions** (every approval is a saved snapshot; roll back in one click), **Settings** (name, on/off, **paused** to stop new submissions instantly, which blocks they may use, which sites they may embed, size limits, show in menu), **Activity** (every attempt, with a hashed address, never the passphrase).

A **new passphrase** makes the old one stop working immediately. **Revoke** stops everyone.

**Edit page** (owner only) lets you change an area's page from your side, for example to set its theme and colours, or as a backup if the person is away. It starts from the area's live version, and pictures carry over. Saving does **not** publish anything: the edit becomes a pending submission marked "EDITED BY YOU", checked by the same rules as anything the area sends, and it goes live only after you approve it and publish. (In a Trusted or Auto area it is approved on save, as their own sends are.) It is not reachable from a collaborator's window or through the drop door.

### Trust levels

- **Review** (default): every valid submission waits for you.
- **Trusted**: a valid submission is approved automatically (still checked, still a saved version, still needs your Publish).
- **Auto**: a valid submission is approved *and published automatically* - at most one automatic publish every five minutes, no matter how fast submissions arrive. It goes out through the same Publish pipeline, so the area's content can only ever land under its own address. A standing banner in the window shows which areas publish by themselves, and **Freeze it** there (or pause the area, or switch its trust back) cancels the waiting publish at once and returns it to review. If the live site does not confirm a new automatic build, everything that publish pushed is put back, the area is frozen, and the previous content is deployed once more.

**Code is the exception:** when an area is allowed its own pages (HTML and JavaScript, see below), any change to them waits for you to read it, even in a Trusted or Auto area.

**Auto-publish is experimental and off by default.** It is gated behind the `auto_publish_enabled` setting (off unless you turn it on). Leave it off unless you have reviewed the threat model in `docs/SECURITY-REVIEW.md`.

## For the collaborator (for example Funk)

Any Halbach install works; no site, keys or account are needed.

```
halbach area init my-page                      a folder with area.json and files/
halbach --area my-page                          edit and preview it in a window, then send it
halbach area connect --url https://live.example.com --area funkworkshop
halbach area send my-page --wait 60            zip, check, upload, wait up to 60 s for the decision
halbach area status                            what happened to earlier sends, with the owner's notes
halbach area validate my-page                   check a folder against the server's limits, no network needed
```

`halbach --area <folder>` opens the collaborator's window: edit your page, preview it, and send it, and watch what the owner decides (received, in review, approved or rejected with a note). The window can only do those things — it has no access to the owner's modules, publish, tunnel or settings.

`connect` asks for the passphrase without echoing it (or reads `HALBACH_AREA_KEY`) and keeps it in your Halbach state folder, never in the page folder and never in a URL. `send` checks your folder against the area's limits first, so most mistakes show up before anything is uploaded.

### The passphrase file is for your account only

The connection is a single file, `area-connection.json`, in your Halbach state folder, and it holds the passphrase, so it is narrowed to the account that wrote it:

- **Linux and macOS:** the file's mode is set to `0600` (you only).
- **Windows:** modes do not control who can read a file, so Halbach runs `icacls <file> /inheritance:r /grant:r "%USERNAME%:F"` — inheritance off, then full control granted to your account only. It is run as a plain argument list (no shell) with a short timeout.

If Windows refuses that step for any reason (`icacls` missing, access denied, no `USERNAME` in the environment), the save still succeeds and **one** warning line is printed; the connection works as before, the file simply keeps the permissions the folder gave it. Nothing in the page folder is ever affected either way.

### What goes in `area.json`

```json
{
  "format": 1,
  "title": "Funk's Workshop",
  "description": "Things I am building.",
  "pages": [
    { "name": "", "title": "Funk's Workshop", "blocks": [
      { "type": "hero",  "props": { "heading": "Funk's Workshop" } },
      { "type": "image", "props": { "url": "files/logo.png", "alt": "logo" } },
      { "type": "text",  "props": { "markdown": "Hello! ![me](files/me.png)" } },
      { "type": "links", "props": { "items": [ { "label": "My site", "url": "https://example.com" } ] } }
    ] }
  ]
}
```

- The first page has the empty name `""` and lives at the area's address; further pages need a name (`"name": "tools"` → `/funkworkshop/tools/`).
- Files are referred to as `files/<path>`. Allowed blocks by default: hero, text (Markdown), cards, links, image, divider, youtube, twitch. The owner can narrow this, or add **embed** (see below).

### An area is its own site (tier 1: menu, brand, look)
An area is a small site of its own under your domain, not a few blocks inside yours:
- **Its own menu.** The area's pages form their own menu (the first page is the area's home), with a "← your site" link back. None of your menu's tabs appear on it, and none of its pages appear in yours.
- **One "Sites" dropdown.** Your main menu (and your 404 page) gets a single **Sites** dropdown listing every area that has *Show in the Sites menu* switched on. It is a plain `<details>` element: no script, works from the keyboard.
- **Its own look, if you allow it.** In **Areas → Settings → Their own look**, tick the themes the area may choose from. With none ticked the area keeps your site's look and a `design` in its `area.json` is ignored with a note. With some ticked, its owner may add a `design` section:

```json
"design": {
  "brand":  { "name": "Funka's Workshop", "mark": "FW", "tagline": "Tools and mods", "footer": "made by Funka", "logo": "files/logo.png" },
  "theme":  "neutral-light",
  "vars":   { "--accent": "#ff8800", "--bg": "#101418" },
  "layout": { "nav": "side" }
}
```
`theme` must be one you ticked; `vars` take colours, sizes and font stacks only (no `url()`, `@import` or `expression()`); `layout.nav` is `top`, `side` or `none`; `logo` is a picture from the area's own `files/` folder (png, jpg, gif or webp, never SVG). The Funk theme also accepts a `funk` section, the same settings as the Funk theme maker. The collaborator editor has a **Your site's look** panel for all of this, and Preview shows it, including the logo picture. Choosing the Funk theme opens a background panel (colours, effect, up to two extra layers, how it moves), and **Page width** (narrow to full width) sets the `--maxw` variable for you.
- **What this tier cannot contain:** scripts, HTML or (without the next setting) free-form CSS. Text values are stored as plain text and escaped on output. The design is checked when it arrives **and again at every build**; if you later untick a theme, the design is left out with a warning at the next Publish. An area's colours go into its own stylesheet and never reach yours.
- **Their own stylesheet (tier 2), if you allow it.** In **Areas → Settings → Their own stylesheet (CSS)**, switch it on. The area may then send **one** CSS file, named in its `design`:

```json
"design": { "css": "files/style.css" }
```
  The file is put at the end of the area's own stylesheet (after its theme, so its rules win) and never reaches your site's stylesheet or any other area. You review it like everything else, and a review warning is added for things that can cover the page (`position: fixed`, a very high `z-index`).

  **What is refused, with a plain reason, when it arrives and again at every build:**
  - **Anything obfuscated or encrypted.** No backslash escapes outside quoted text (they hide what the CSS says), no quoted text over 300 characters or unbroken run of 120+ characters (that is how encoded data looks), no lines over 4,000 characters (packed files must be written out to be reviewed), no non-ASCII characters outside quoted text and comments, no hidden or control characters, no huge comments. A stylesheet is never rewritten into something safer: it passes as written or is refused.
  - **Anything that reaches outside.** No `@import`, no `@namespace`, no `data:` addresses, no outside addresses of any kind. `url()` may only name a picture (png, jpg, gif or webp, never SVG) that is in the area's own `files/` folder; those become the area's own addresses at build.
  - **Script-like features.** No `expression()`, `behavior`, `-moz-binding`, `javascript:`, `image-set()`, `image()`, `src()`, `attr()`, `paint()` or similar, and only a short list of `@` rules (`@media`, `@supports`, `@keyframes`, `@layer`, `@container`, `@font-face`, `@property`). `@font-face` may use fonts installed on the visitor's PC (`local()`), never download one.
  - Also: a size limit (100 KB), balanced brackets, at most 6 levels of nesting, UTF-8 text, and the file must be the one named in `design.css` (an area has one stylesheet, and the file is not published as a separate file).

  If you later switch the setting off, the stylesheet is left out with a warning at the next Publish. Visitors see none of this: they just see the page, with no prompts or warnings.
- **Their own pages: see the next section.** It is off unless you switch it on, area by area.

### Their own pages (tier 3: HTML, CSS and JavaScript), if you allow it
In **Areas → Settings → Their own pages**, switch it on. The area may then send a small site of its own in an `app/` folder next to `area.json`:

```
area.json        "app": { "entry": "app/index.html", "label": "Tool shelf" }
app/index.html   plain pages, plus about.html, tools/index.html ...
app/main.js      scripts (plain scripts, loaded with <script src="main.js">)
app/style.css    stylesheets (loaded with <link rel="stylesheet" href="style.css">)
app/data/*.json  data the scripts read with fetch("data/items.json")
app/img/*.png    pictures (png, jpg, gif, webp)
```
Everything is addressed relative to the page that uses it (`img/logo.png`, `../style.css`), and must stay inside `app/`. The area's menu gets one extra link (the `label`) that opens the `entry` page. The pages are published at `/<area>/app/` exactly as they were checked.

- **What visitors' browsers do with them.** Every file under `/<area>/app/` is sent with a security header that puts the pages in a **browser sandbox**: they get no access to your site's storage or cookies (`localStorage` and cookies throw an error), cannot submit forms, cannot open pop-ups, can load **only files from your own site** and can send nothing anywhere else (no outside scripts, pictures, fonts or network calls). Links to other sites work as plain links in the same tab. The header is always enforced, whatever mode your site's own policy is in, and your site's policy is detached for these paths so the two cannot combine into something neither meant. (Shown working on Cloudflare Pages and in Chrome: the page's scripts and data files load; an outside fetch and an outside picture are blocked; storage and cookies are blocked.)
- **What the owner reads.** A change to an area's own pages (any file in `app/`, or the `app` section) **never goes live automatically, even in a trusted or auto area**: it waits in Review. The review card says so, lists the files, and **Read their code…** shows the text of each file exactly as it will be published (as text, never run). Approving asks you to confirm you have read it.
- **What is checked, as written (and again at every build).** The same rule as for stylesheets: anything the owner cannot read is refused.
  - *Pages:* only a fixed list of plain tags; no forms, frames, objects, `<base>`, SVG, audio or video; no inline event handlers (`onclick`...): add listeners in a script; nothing may load from outside (`src` and `href` are the area's own files, except a plain link `<a href="https://...">`); no password or file inputs; links may not open a new tab (a sandbox blocks it).
  - *Scripts:* no packed or one-line files, long encoded strings, strings of character codes or `_0x1234` names; no `eval`, no building code from text, no `atob`, no modules (plain scripts only); no WebSocket, XMLHttpRequest, workers or beacons; no outside address written into a script; no code that sends the visitor elsewhere (`location = ...`, `window.open`). Reading the address (`location.search`) is fine.
  - *Stylesheets and styles:* the stylesheet rules above, with pictures taken from the area's own `app/` files.
  - *Files:* html, js, css, json, txt, png, jpg, gif, webp only; at most 200 files, 300 KB per text file; names are checked like everything else. Pictures have their camera data removed as usual.
- **If you switch it off later**, or a file stops passing the checks, the pages are left out of the next build with a warning, and the menu link goes with them.
- **The honest limits.** The checks keep code readable and refuse the obvious tricks; they cannot prove that arbitrary JavaScript is harmless. What contains it is the sandbox and the header. A page that follows the rules can still **look like your site** (it is on your domain), show misleading text, link visitors elsewhere, or use their processor. It can **read the public files of your site** (static files are readable across origins), so never put anything private on the site. Switch this on only for someone you trust, and read what they send.
- **Preview.** The collaborator's window has a **Your own pages** section and a **Preview my pages** button, served with the same header; your own review preview does too.

### Embedding another site (owner-gated)
The `embed` block shows another site inside the page in a frame. It is **off by default** and needs two things from the owner, in the area's **Settings**: tick `embed` under *Blocks they may use*, and list the exact hosts under *Sites they may embed* (for example `funkstudio-share.example.workers.dev`).

```json
{ "type": "embed", "props": { "url": "https://player.example.com/app", "height": 560 } }
```
- The address must be a plain `https://host/...` whose host is on the owner's list. Wildcards, other sub-domains, `user@host`, ports, IP addresses and look-alike hosts are all refused; the owner's list takes no wildcards either.
- An embed cannot show the owner's modules, and a bundle cannot set the frame's trust: only the builder does, and only for a listed host.
- Only that area's own page gets the extra `frame-src`; the rest of the site's security policy is unchanged. The list is checked again at every build, so removing a host drops its embeds at the next Publish.
- The frame runs in a sandbox (scripts, popups and forms allowed, no top-level navigation). Because a listed host is a different origin from the site, the frame also gets its own storage (`allow-same-origin`), which it needs for things like saved settings, without any access to the site's own storage.
- `halbach area validate` checks against the connected area's own rules (including its embed hosts); without a connection it uses the default rules and says so.
- **Trust note:** a listed host's content appears inside your page. It cannot run on your origin, but if that site were ever compromised it could show misleading content, so list only sites you trust.
- Allowed files: `.md .txt .png .jpg .jpeg .gif .webp .pdf` (no HTML, scripts, SVG, programs or archives). Names may use letters, digits, spaces and `. _ - ( ) + , @ ~`.
- Links must be `https://` or `mailto:` or inside the area. Raw HTML in Markdown is shown as text.

## What is checked (and refused with a plain reason)

Every bundle is treated as hostile. Nothing is extracted until each entry's name and metadata pass, and extraction is streamed with a hard size cap into a temporary folder that is removed on any failure.

- **Archive:** path tricks (`..`, absolute paths, drive letters, hidden or control characters, bidi and zero-width characters, Windows reserved names, trailing dots and spaces), symlinks and special files, passwords, unusual compression, case-colliding names, too many files, names too long or too deep, anything outside `area.json` and `files/`, zip bombs (ratio and size, before reading), headers that lie about sizes.
- **Files:** extension allow-list, program or script types hidden in double extensions, and the **bytes must match the extension** (PNG, JPEG, GIF, WebP, PDF signatures; UTF-8 text). Picture sizes are read from the header and capped (default 8000 px a side, 40 megapixels). A JPEG that still has camera data (EXIF) is flagged; Halbach does not strip it.
- **Content:** blocks must be on the area's allow-list and valid; no modules, no pages of yours, no scripts; `slug`, menu, theme, redirects and settings in `area.json` are ignored and reported. An area can only ever write under its own address.
- **Quota:** the area's total (live plus new) stays under its limit.

On your site an area's page gets its own, stricter security policy (only what its own blocks need), and everything under `/<area>/files/` is served with `default-src 'none'; sandbox`, so nothing in it can ever run.

## The one public door

Areas add `POST /drop/<area>/bundle`, `GET /drop/<area>/status` and `GET /drop/<area>/limits` to the **live hub**, which the tunnel already exposes.

- **With no usable area the live hub is exactly as read-only as before:** those routes answer as if they did not exist.
- Authentication comes first: `Authorization: Bearer <passphrase>`, checked (constant time, scrypt) **before any body is read**. A wrong key, an unknown area and a switched-off area all answer the same 401. No cookies.
- Wrong keys: 10 failures from one address lock that address out for 15 minutes.
- Size is checked from `Content-Length` before reading, enforced again while streaming, with a read timeout for slow senders and a whole-upload deadline. Two uploads at a time, 12 per address per 10 minutes, and a free-disk floor.
- Errors never include paths or internals. Every attempt is logged (hashed address, never the key).

## Private areas

Unlisted is not private. Anyone who knows the address can open it. For a page
only certain people can open, put **Cloudflare Access** in front of that
address: Access asks each visitor to prove who they are with an emailed code
before they reach the page. Halbach cannot do this for you and does not fake a
password box.

This recipe protects the **page's address on your site's domain** (for example
`https://example.com/funkworkshop/`). It does **not** go in front of the drop
address the collaborator uploads to (the tunnel's live host, for example
`https://live.example.com/drop/funkworkshop/...`) — putting Access there would
stop `halbach area send` from working. They are different host names.

The steps follow Cloudflare's own documentation for a **self-hosted** Access
application and for **one-time PIN** login. Cloudflare's dashboard wording
changes from time to time — if a label has moved, look for the same words.

1. Sign in at [dash.cloudflare.com](https://dash.cloudflare.com/) and choose
   **Zero Trust**.
2. Go to **Access controls → Applications**, then select **Create new
   application**.
3. Choose **Self-hosted and private**, then select **Add public hostname**.
4. In **Domain**, pick your site's domain (for example `example.com`). In the
   path box beside it, enter the area followed by `/*` — for `funkworkshop`
   that is `funkworkshop/*`. This one rule covers the area page **and**
   everything under its `/files/` folder, so the page and its pictures are
   protected together.
5. Under **Access policies**, create a policy:
   - **Action:** Allow.
   - **Include:** **Emails**, then add your collaborator's address (add each
     person who should get in). Access is deny by default: nobody passes without
     an Allow rule that matches them.
6. For authentication, make sure the **One-time PIN** provider is enabled (see
   below) — it is what lets your collaborator sign in with an emailed code and
   no account.
7. Select **Create**.

### Turn on the one-time PIN

New Zero Trust accounts use Cloudflare's own identity provider by default and
**do not** add one-time PIN automatically. Check this on Cloudflare's current
dashboard:

1. Go to **Zero Trust → Integrations → Identity providers**.
2. Under **Your identity providers**, select **Add new identity provider**.
3. Choose **One-time PIN**.

Your collaborator can now enter their email on the Access page and select
**Send login code**. The code arrives from `notify.cloudflare.com` and expires
after 10 minutes.

### Check it works

- Open `https://example.com/funkworkshop/` in a **private / incognito window**.
  You should get Cloudflare's sign-in page, not the area. Enter an allowed
  email, paste the code, and you reach the page.
- Open the same address and enter an email that is **not** on the policy: you
  should see *That account does not have access.*
- A direct link to a file (`.../funkworkshop/files/picture.png`) is stopped too,
  because the same path rule covers it.

If your email provider scans or blocks the login mail, allowlist the sender
domain `notify.cloudflare.com` and the sender address
`noreply@notify.cloudflare.com` (Cloudflare's one-time PIN page lists the exact
IP addresses as well). Cloudflare's Zero Trust free plan covers a small team;
check the current user limit on Cloudflare's plans if you add many people.

## Not built yet

- Single-file uploads (`/drop/<area>/file`) and a browser upload form for senders; today the collaborator sends a whole zip (from the window or `halbach area send`).
- Notifications by chat.

Camera data is stripped from JPEG and PNG pictures on the way in (see the area rules below), and the collaborator can edit, preview and send from a window (`halbach --area <folder>`).

## Risks to weigh
A stolen passphrase can send *valid* content, but it still waits for your review (or, in a Trusted area, goes live at your next Publish, limited to the area's own address and blocks). On an **Auto** area it is published by itself, debounced to one automatic publish every five minutes — with the standing banner, the freeze button and the automatic rollback as the ways back. Revoke or replace the passphrase in the window at any time.
