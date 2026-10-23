# Changelog

All notable changes to Halbach ConnectHub are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); every entry below is
derived from this repository's commit history, not written after the fact.

## [Unreleased]

### Added
- **FunkThemes 2:** the bundled Funk engine is now Funk's 2026-10-22 master (82,428 bytes): the new **Spectrum**
  effect, per-effect settings, up to two stacked effect layers and motion settings. The site accepts them with
  strict whitelists; picture, video, web and Wallpaper Engine themes, assets and sound stay refused, and music
  reaction is forced off on a site.
- **Owner edit:** an area's page can be edited from the Areas screen. It waits in Review like anything the area sends.
- **Areas: their own stylesheet (tier 2):** an owner-enabled, reviewed CSS file per area, checked as written. Anything
  obfuscated or encrypted, any outside address and any script-like feature is refused.

## [0.5.0] - 2026-10-17

### Added
- **FunkThemes integration:** a `funk` site theme (Funk's animated engine), a theme
  maker in the window that saves to the shared `FunkThemes` folder, and a **Window
  theme** setting kept by the hub itself so it survives a new port or a cleared
  browser profile.
- A "Powered by FunkThemes" credit line on every Funk-themed page, with a per-page
  switch (and an information note when it is hidden).
- **Linked sites:** put another https site on yours as cards (the `linked-sites`
  block), a menu link, or its own page that links out or embeds it — with a safe
  address check (reachable, Cloudflare, embeddable) and a warning for keys in the
  address.
- **Areas:** a page one other person can send content to through the tunnel with no
  keys of yours: an area bundle validator (hostile-zip handling), a public drop
  endpoint (`/drop/<area>/...`, authenticated before any body is read), a review /
  approve / reject / rollback flow with versions and an audit log, a builder merge
  confined to the area's own address, a collaborator client and `halbach area ...`
  CLI.
- An **Areas** screen in the window: create, review with a rendered preview,
  approve, reject, versions, settings, activity.
- A collaborator window (`halbach --area <folder>`) with a page editor, preview
  and Send, isolated from the owner's routes and state, plus an `auto` trust level
  (off by default) that publishes an approved area itself.
- An optional real-site import check for tests, enabled by the
  `HALBACH_TEST_CLASSIC_SITE` environment variable.

### Fixed
- `ctx.modules` was missing in the site runtime; lazy embeds never started in the
  browser pane; Forge iframes were sandboxed too tightly; preview links and images
  needed a prefix for site-relative paths; slugs dropped apostrophes; submission ids
  now sort correctly.

## [0.4.0] - 2026-10-17

### Added
- **The modular site:** Halbach can build the whole site from a model instead of a
  hand-edited folder. Pages, blocks, themes, menu, redirects, settings, history with
  restore, and export/import.
- Site model foundation: schema validators, a plugin registry, and a versioned
  `site.json` with 100 history snapshots.
- A frontend runtime (`halbach-site.js`, `base.css`), 17 block scripts, the `auto`
  and `pets` views, and three starter themes.
- Site builder and legacy importer: real page per address, inert JSON page data,
  content-hashed assets, a CSP assembled from the blocks actually used, `_headers`,
  `robots.txt`, `sitemap.xml`, `404.html`, `_redirects`, extra-file merge, generated
  icon, and Cloudflare limit checks.
- Site service and modular publish path: a `/preview/...` route that serves the
  build with the real generated headers, and the site API.
- Site screens in the window: Overview with checks, Pages, a page editor whose forms
  are generated from block manifests, Design, Menu & redirects, Site settings,
  History, Preview with phone/tablet/desktop widths.
- Quality checks, a publish diff, export/import, and the `halbach site ...` CLI.
- Modular site documentation and packaging of the built-in plugins.

### Changed
- The `embed` block now leaves same-site paths unsandboxed.

## [0.3.0] - 2026-10-16

### Added
- **Drop-off inbox:** other tools and chats can send a folder, file or Markdown text
  with `halbach submit`; it arrives as a switched-off module marked "from a chat"
  and cannot publish or switch itself on.
- **Per-module options:** hide items by name, choose a live module's view (including
  a **Pets** view with its own nav tab), set its refresh rate, and keep asset-only
  folders out of the list.
- A live module can serve a list of objects (for example a list of bots).

### Changed
- Renamed the project to **Halbach ConnectHub**; README and package metadata point at
  the HalbachConnectHub repository.

### Fixed
- The tunnel connector now dies with the hub even if the hub is force-killed.
- One hub per state folder (an instance lock) so two hubs cannot overwrite each
  other's state.
- Script and stylesheet file names carry a content hash, so updates can never be
  served stale.

## [0.2.0] - 2026-10-15

### Added
- **Static publishing:** build the site plus switched-on modules, write
  `manifest.json`, deploy with `wrangler pages deploy`, then verify the live site
  shows the new build.
- A live hub and tunnel manager: a second local server is the only thing the tunnel
  points at; start and stop a Cloudflare named tunnel from the window.
- A Quit button and a launcher.

### Changed
- `wrangler` now runs from the hub data folder so its cache never lands in a project.

### Fixed
- Refuse to share a port with another hub.

## [0.1.0] - 2026-10-15

### Added
- Local control panel foundation: a loopback web server, a JSON state file, static
  and live module concepts, and the window UI.

[0.5.0]: https://github.com/Levatat3/HalbachConnectHub/compare/v0.4.0...v0.5.0
[0.4.0]: https://github.com/Levatat3/HalbachConnectHub/compare/v0.3.0...v0.4.0
[0.3.0]: https://github.com/Levatat3/HalbachConnectHub/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/Levatat3/HalbachConnectHub/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/Levatat3/HalbachConnectHub/releases/tag/v0.1.0
