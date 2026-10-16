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
4. keeps only allowed top-level fields and strips private-looking keys at every depth;
5. answers with CORS only for your own domain, `www.`, and any extra origins you list.

`GET /status` lists which live modules are on, which is how the site decides between showing data and showing "offline". Everything else is a 404, and any method other than `GET` is a 405.

The tunnel manager only starts and stops an already-configured `cloudflared` named tunnel (`tunnel run --url http://127.0.0.1:<hub port> <name>`). It never downloads cloudflared and never handles a login.

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
| A crowd hammering your PC | Per-module viewer cap, one-second response cache |
| Hotlinking live data from other sites | CORS only for your own origins (a convenience, not a security boundary: the data is public by design) |

## Not handled

- A hostile program already running as you on this PC can read the state file and call the hub. That is outside what a local tool can defend against.
- The fingerprint uses whole-second mtimes, so two edits to a file of identical size within one second can be missed.
- Anything an allowed field contains is public. Allow-listing protects what you did not choose to share, not what you did.
