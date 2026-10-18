# Architecture

## Pieces

```
   your PC                                              the internet
  ┌──────────────────────────────┐
  │ Halbach ConnectHub window (browser)  │
  │        │  token + Host check │
  │ local server  127.0.0.1:8780 │
  │   store.py  (state, rules)   │
  │        │                     │       static ──► site host (Pages)  ──► visitors
  │  publisher  ─────────────────┼──────────────┘
  │                              │
  │  live hub ───────────────────┼── tunnel ──► visitors   (only while the PC is on)
  └──────────────────────────────┘
```

- **`store.py`** owns the state file and every rule: validation, never-publish list, locked fields, change tracking. The server and UI never bypass it.
- **`server.py`** is a thin HTTP layer over the store. Loopback only, token on every API call, `Host` and `Origin` checks.
- **`ui/`** is plain HTML, CSS and JS. No build step, no framework, no inline scripts (the server sends a strict Content-Security-Policy).

## One hub per state folder

The hub keeps its state in memory and rewrites the whole file on every change, so two hubs on one folder overwrite each other (this once switched off modules that had been published). `single.py` holds an OS-level lock on `hub.lock` for as long as the hub runs; the OS drops it if the hub crashes or is killed, so a stale lock cannot block a start. A second launch prints where the running hub is (`hub.json`), opens its window and exits. The port fallback only applies when something that is *not* a hub is using the port.

## Pending-change tracking

A static module has `on` (what you chose) and `published_fp` (a fingerprint of what the host currently has, or `null`). The fingerprint is a hash of `(relative path, size, mtime)` for the files that would be uploaded. So:

| `on` | `published_fp` | fingerprint now | shown as |
|---|---|---|---|
| yes | none | any | will publish (add) |
| yes | same | same | on the site |
| yes | set | different | changed (update) |
| no | set | any | will remove |

Editing a file inside a watched folder therefore makes the change appear by itself.

## Static publish

1. Copy the site folder to a staging directory under the hub's data folder, skipping the never-publish list and the site's own `README.md`.
2. Give every script and stylesheet the site's pages load a content hash in its file name (`modules.3fa9c1d2e0.js`) and rewrite the page to match. Hosts and browsers keep `.js` and `.css` for hours (Cloudflare's Browser Cache TTL even overrides the site's own headers), so a changed file at the same address can be served stale to anyone who visited recently; a changed file with a new name cannot. Only the site's own pages are rewritten, never module pages.
3. Copy each switched-on static module into `modules/<id>/`, and classify it: a lone `.swf` is a game, a folder with `index.html` is a page, mostly images is a gallery, one image is an image, anything else is a file.
4. Write `manifest.json` with a random `build` id.
5. Run `wrangler pages deploy <stage> --project-name <project>` from the data folder (so wrangler's cache never lands in a project).
6. On success, record fingerprints (`mark_published`), then poll `https://<domain>/manifest.json` until its `build` matches, so "published" means "visible".

A test publish does steps 1 to 5 on a `preview` branch: private address, live site untouched, nothing marked published.

The site folder is fingerprinted like a module, so editing it shows up as a pending "Site files" change.

## Live modules

```
visitor ──► Cloudflare ──► tunnel ──► live hub (127.0.0.1:8782) ──► your service (127.0.0.1:<port>)
```

The live hub is a separate server from the control panel and is the only thing the tunnel points at. For `GET /m/<id>` it:

1. checks the `Host` header (the live host or loopback) and that the module exists and is switched on;
2. admits the viewer if under the module's cap (viewers are identified by a hash of `CF-Connecting-IP` and the user agent, counted for 12 seconds);
3. returns the cached payload if under a second old, otherwise calls the service (4 s timeout, 256 KB cap, JSON only, no redirects);
4. keeps only allowed top-level fields and strips private-looking keys at every depth (for a service that returns a list of objects, the same rule applies to every item, at most 100 of them);
5. answers with CORS only for your own domain, `www.`, and any extra origins you list.

`GET /status` lists which live modules are on, which is how the site decides between showing data and showing "offline". Everything else is a 404, and any method other than `GET` is a 405.

The tunnel manager only starts and stops an already-configured `cloudflared` named tunnel (`tunnel run --url http://127.0.0.1:<hub port> <name>`). It never downloads cloudflared and never handles a login. On Windows the connector is placed in a job object with kill-on-close, so it dies with the hub even if the hub is force-killed (otherwise the live data would stay public with no hub in charge of it).

## Drop-off inbox

```
chat or tool ──► halbach submit ──► <state>/inbox/<slug>/ ──► scanner (every 3 s) ──► switched-off module "from a chat"
                                                                                          │ you review, switch on, Publish
```

- `submit.py` is the only door other programs use. It copies into a hidden temporary folder (dot-folders are ignored by the scanner) and renames it into place, so a submission appears all at once and is never half written. It applies the never-publish list while copying, strips a leading byte-order mark from Markdown, refuses a drive root or the user folder, and caps a submission at 100 MB.
- `Store.scan_inbox()` imports a sub-folder only if it resolves to a direct child of the inbox (so a symlink or junction pointing elsewhere is refused), has been quiet for two seconds, contains something publishable, is under the size cap and the count cap (100). `module.json` (name and description, optional, 8 KB at most, control characters removed) is read for display and never published.
- An imported submission is **always off**, flagged `from_inbox` and `new`. Switching it on or pressing "Mark as reviewed" clears `new`. Nothing in this path can publish, switch a module on, or change a setting.
- Removing a submission moves its folder to `inbox/.removed/` so the scanner cannot import it again; ordinary modules are never touched on remove.
- A single `.md` file, or a folder with `index.md` and no `index.html`, is classified as a `doc`. The site renders it with a small Markdown renderer that builds DOM nodes from text (never `innerHTML`), allows only `http(s)`, `mailto`, `#` and same-module relative links, and only `https` or same-module images.

## Per-module options

`Store.set_options` holds display options that never widen what is shared: `hide` (item names dropped by the live hub before anything is sent, from a top-level list and from lists of objects inside an allowed field; the list itself is not written to the manifest), `view` (`auto` or a named view the site knows; views in `TAB_VIEWS` also get a nav tab, created by the site at load time from the manifest), `poll` (seconds, 1 to 60) and `listed` (static modules only: `false` writes `listed: false` so the site draws no card, for files that only support another view).

## The modular site (0.4+)

Until 0.3 the site was a folder you edited by hand. From 0.4 Halbach can build it from a model.

```
site.json ──► sitemodel.py ──► sitebuilder.py ──► stage folder ──► publisher.py ──► Cloudflare Pages
 (model)       validate,        pages, hashed        (real files)     wrangler deploy,
               history          assets, CSP,                           verify
                                headers, sitemap
                   ▲
     registry.py ──┘  block / view / theme / template plugins

classic mode: the site folder is copied to the stage (publisher.build_stage) and the same deploy runs
```

- **`sitemodel.py`** holds `site.json` (version 1): brand, theme, settings, pages with blocks, redirects, nav links and linked sites. Every edit is validated; a refused edit leaves the model exactly as it was, and an accepted edit saves a history snapshot (the last 100 are kept). A model written by a newer Halbach is refused.
- **`registry.py`** finds built-in and user plugins (blocks, views, themes, templates), validates their manifests, and assembles the Content-Security-Policy host list from the blocks a site actually uses, so a Twitch host appears only when a Twitch block is present. User plugins are untrusted until a pinned digest matches (the registry supports this; the UI to install them is not wired yet).
- **`sitebuilder.py`** turns the model into real files in a staging folder: one `index.html` per page carrying the page's data as inert JSON, a shared runtime, content-hashed scripts and stylesheets, the `_headers` file (the CSP, the area rules, immutable caching under `/_halbach/*`), `robots.txt`, `sitemap.xml`, `404.html`, `_redirects`, and a merge of extra files from the site folder (its own `index.html`, `css/` and `js/` are skipped). It also checks Cloudflare's limits (20,000 files, 25 MB per file, and the header and redirect caps).
- **`siteservice.py`** is the only mutator. `apply(action, args)` covers page and block add/update/delete/move/duplicate, brand, theme, settings, redirects, nav links, template loading, classic and package import, restore, and linked-site edits. Every call is all-or-nothing and saves a history snapshot. It also runs the quality checks and builds the publish plan.
- **`publisher.py`** stages modules for classic mode and runs `wrangler pages deploy` then verifies the result. A modular publish builds through `SiteService` first and reuses the same deploy step.
- **`legacyimport.py`** reads a classic Halbach starter-site folder (tabs home/game/live/videos/forge/more/projects/about plus the runtime Pets tab) into a model; an unrecognised folder imports as plain text pages with a note.

### The preview route

`/preview/...` serves the last preview build with the **same headers Cloudflare Pages will send**, so report-only versus enforce (for the CSP) can be checked before publishing. Cloudflare merges header rules that match more than one pattern; to *replace* a header for a sub-path the builder (and the preview route) detach it first with `! Header-Name` and then set it — `sitebuilder.parse_headers` / `headers_for` implement the same semantics locally. The preview is a route of the loopback control panel and is never published.

## Areas and the drop endpoint (0.5)

An **area** is a page one other person can send content to without any key of yours. The only thing added to the public surface is the drop endpoint on the live hub.

```
collaborator ──► halbach area init | connect | send ──► POST /drop/<area>/bundle (live hub, via the tunnel)
                                                             │
   owner's PC:   quarantine ─► validate (bundle.py) ─► review ─► approve ─► approved/vNNNN ─► next Publish
                                                              └─ reject (with a note the sender can read)
```

- **`areas.py`** holds area configuration and the review pipeline: scrypt passphrase fingerprints (the plain passphrase is shown once and never stored), per-address lockout, submissions, approval versions with rollback, an audit log (hashed addresses, never the key), and the trees the builder merges.
- **`bundle.py`** treats every zip as hostile. Entry names and metadata are checked, extraction is manual and streaming into a temporary folder that is removed on any failure, and there are size, compression-ratio, depth and entry-count caps, a magic-number check that must agree with the extension, and image dimension caps. Nothing is extracted before every rule passes.
- **`dropserver.py`** is that one public door. Authentication comes first (constant-time, before any body is read); then size, rate and disk limits. A wrong key, an unknown area and a switched-off area all answer the same 401, and no cookies are used (so there is no CSRF surface).
- **`areaclient.py`** is the collaborator's side and its CLI (`halbach area init | connect | send | status | limits`).
- The builder merges an area's approved content **only under the area's own prefix** (`/<slug>/`) and gives it a stricter policy allowing only what the area's own blocks need. Everything under `/<area>/files/` is served with `default-src 'none'; sandbox`, so nothing in it can run.

## Threat model

| Threat | Mitigation |
|---|---|
| A web page you have open calls the local API | Per-launch token; foreign `Origin` refused |
| DNS rebinding to `127.0.0.1` | `Host` header must be the server's own address |
| Publishing a secret by pointing at a folder | Never-publish list applied to every folder |
| A live service leaking private fields | Allow-list only; locked names can never be allowed |
| Pointing a live module at someone else's server (SSRF) | Only `127.0.0.1` / `localhost` accepted |
| Pointing at a whole drive or home folder | Refused |
| Credential theft | The hub never sees credentials; `wrangler` and `cloudflared` hold their own logins |
| Reaching the control panel through the tunnel | The tunnel points at the live hub only; the panel is a different server on a different port |
| A service redirecting the hub elsewhere | Redirects are never followed |
| A chat or tool submitting something harmful or private | Submissions are inert until you review, switch on and publish; never-publish list on the way in; text previews are shown as text; Markdown is rendered without HTML |
| A submission escaping the inbox (symlink, junction, `..`) | Must resolve to a direct child of the inbox; otherwise skipped with a warning |
| Flooding the inbox | 100 submissions, 100 MB each |
| Two hubs overwriting each other's state | OS-level instance lock per state folder |
| A force-killed hub leaving its tunnel running | The connector lives in a kill-on-close job object (Windows) |
| Hidden items leaking through the manifest or the stream | Hiding happens in the hub before sending; the hide list is not published (tested) |
| A crowd hammering your PC | Per-module viewer cap, one-second response cache |
| Hotlinking live data from other sites | CORS only for your own origins (a convenience, not a security boundary: the data is public by design) |
| A block property injecting script into a generated page | Schema validators reject unsafe values; all model text is drawn into the page through `textContent`/the `h()` helper, never `innerHTML`; the runtime contract test checks every `ctx.*` a block uses |
| Pointing a linked site at an internal address (SSRF) | Private and local hosts are never probed; the address must be https |
| A stolen area passphrase | Revoke or regenerate instantly; a wrong key, an unknown area and a switched-off area all answer the same 401; lockout and rate limits slow guessing; submissions still need review; the audit log shows use |
| A malicious area archive | Every rule in `bundle.py`; manual streaming extraction; size, ratio, depth and count caps; a quarantine folder outside any served path |
| Script or HTML delivered through area content | Data-only pages, safe Markdown, no script/HTML/SVG allowed; a strict CSP on the area's prefix; files under the area served sandboxed |
| Defacement or phishing on your domain | Review by default; the area is confined to its own prefix with a stricter policy; freeze and roll back in one click |
| Filling your disk through the drop endpoint | Per-area and global quotas, a free-disk floor, and a cap on concurrent uploads |
| Probing for areas | Uniform responses and a single 401 (no listing endpoint); area slugs are public, keys are not |

## Not handled

- A hostile program already running as you on this PC can read the state file and call the hub. That is outside what a local tool can defend against.
- The fingerprint uses whole-second mtimes, so two edits to a file of identical size within one second can be missed.
- Anything an allowed field contains is public. Allow-listing protects what you did not choose to share, not what you did.
