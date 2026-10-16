# Architecture

## Pieces

```
   your PC                                              the internet
  ┌──────────────────────────────┐
  │ ConnectHub window (browser)  │
  │        │  token + Host check │
  │ local server  127.0.0.1:8780 │
  │   store.py  (state, rules)   │
  │        │                     │       static ──► site host (Pages)  ──► visitors
  │  publisher  ─────────────────┼──────────────┘
  │                              │
  │  live hub (planned) ─────────┼── tunnel ──► visitors   (only while the PC is on)
  └──────────────────────────────┘
```

- **`store.py`** owns the state file and every rule: validation, never-publish list, locked fields, change tracking. The server and UI never bypass it.
- **`server.py`** is a thin HTTP layer over the store. Loopback only, token on every API call, `Host` and `Origin` checks.
- **`ui/`** is plain HTML, CSS and JS. No build step, no framework, no inline scripts (the server sends a strict Content-Security-Policy).

## Pending-change tracking

A static module has `on` (what you chose) and `published_fp` (a fingerprint of what the host currently has, or `null`). The fingerprint is a hash of `(relative path, size, mtime)` for the files that would be uploaded. So:

| `on` | `published_fp` | fingerprint now | shown as |
|---|---|---|---|
| yes | none | any | will publish (add) |
| yes | same | same | on the site |
| yes | set | different | changed (update) |
| no | set | any | will remove |

Editing a file inside a watched folder therefore makes the change appear by itself.

## Planned: static publish

1. Copy the site folder to a staging directory, skipping the never-publish list.
2. Copy each enabled static module into `modules/<id>/`.
3. Write `manifest.json` listing enabled modules (id, name, kind, entry path).
4. Run `wrangler pages deploy <staging> --project-name <project>`.
5. On success, `mark_published()` records the new fingerprints.

The site reads `manifest.json` to build its tabs, so adding a module adds a tab with no code change.

## Planned: live modules

A second local server (the "hub") is the only thing the tunnel points at. It serves a module only if it is switched on, and passes only the allowed fields of a JSON response, enforcing the viewer cap. The service itself stays unreachable from outside. If the PC is off, the tunnel is down and the site shows "offline" for live modules.

## Threat model

| Threat | Mitigation |
|---|---|
| A web page you have open calls the local API | Per-launch token; foreign `Origin` refused |
| DNS rebinding to `127.0.0.1` | `Host` header must be the server's own address |
| Publishing a secret by pointing at a folder | Never-publish list applied to every folder |
| A live service leaking private fields | Allow-list only; locked names can never be allowed |
| Pointing a live module at someone else's server (SSRF) | Only `127.0.0.1` / `localhost` accepted |
| Pointing at a whole drive or home folder | Refused |
| Credential theft | The hub never sees credentials; `wrangler` holds its own login |

## Not handled

- A hostile program already running as you on this PC can read the state file and call the hub. That is outside what a local tool can defend against.
- The fingerprint uses whole-second mtimes, so two edits to a file of identical size within one second can be missed.
