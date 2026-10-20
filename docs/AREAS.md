# Areas: let someone fill a page of your site without giving them any keys

An **area** is a page (or a few) on your site that one other person can send content to. They never log in to anything of yours: no Cloudflare, no GitHub, no Halbach. They send a small zip over HTTPS, it waits in a review queue on your PC, and **you** approve it. It goes live at your next Publish.

The full design is in [design/AREAS.md](design/AREAS.md). This page says what is built in 0.5 and how to use it.

## For you (the owner)

1. **Set up the drop address once.** In Settings, set the **live host** (the tunnel's host name, for example `live.example.com`) and start the tunnel. Areas are reached through the same tunnel your live modules use. Your PC and the tunnel must be on while someone sends.
2. Open **Areas → New area**. Give it a name and an address (`/funkworkshop/`). Halbach shows its **passphrase once** (26 random characters). It keeps only a scrypt fingerprint of it. Send the passphrase to the person privately.
3. When they send something, **Areas** shows a badge. Open the area, then **Review**: you see how many pages, blocks and files, and what changed from what is live. **Preview it** builds the site with their content in your local preview. **Approve** or **Reject** (with a note they can read).
4. **Publish** as usual. Approving does not put anything on the internet by itself — unless the area is on the **Auto** trust level (below), where approved content is published by itself, debounced to at most one automatic publish every five minutes.

Everything else is in the area's tabs: **Versions** (every approval is a saved snapshot; roll back in one click), **Settings** (name, on/off, **paused** to stop new submissions instantly, which blocks they may use, size limits, show in menu), **Activity** (every attempt, with a hashed address, never the passphrase).

A **new passphrase** makes the old one stop working immediately. **Revoke** stops everyone.

### Trust levels

- **Review** (default): every valid submission waits for you.
- **Trusted**: a valid submission is approved automatically (still checked, still a saved version, still needs your Publish).
- **Auto**: a valid submission is approved *and published automatically* - at most one automatic publish every five minutes, no matter how fast submissions arrive. It goes out through the same Publish pipeline, so the area's content can only ever land under its own address. A standing banner in the window shows which areas publish by themselves, and **Freeze it** there (or pause the area, or switch its trust back) cancels the waiting publish at once and returns it to review. If the live site does not confirm a new automatic build, everything that publish pushed is put back, the area is frozen, and the previous content is deployed once more.

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
- Files are referred to as `files/<path>`. Allowed blocks by default: hero, text (Markdown), cards, links, image, divider, youtube, twitch. The owner can narrow this.
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

- **Area mode window** (a cut-down Halbach with a page editor and a Send button for the collaborator). Today the collaborator edits `area.json` and uses the command line.
- Single-file uploads (`/drop/<area>/file`), notifications by chat, and stripping camera data from pictures.
- A browser upload form for senders; the area's `GET /status` is only used by the command-line client.
- **Risks to weigh:** a stolen passphrase can send *valid* content, but it still waits for your review (or, in a Trusted area, goes live at your next Publish, limited to the area's own address and blocks). On an **Auto** area it is published by itself, debounced to one automatic publish every five minutes — with the standing banner, the freeze button and the automatic rollback as the ways back. Revoke or replace the passphrase in the window at any time.
