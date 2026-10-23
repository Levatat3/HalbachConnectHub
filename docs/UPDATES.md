# Updates: checking for a newer Halbach, and installing it safely

Halbach can look on GitHub for a newer version and install it after you say yes. It never updates by itself, and it never touches your sites, areas, keys, settings or history: those live in your state folder (`%LOCALAPPDATA%\HalbachConnectHub`), and an update only replaces the program files.

## Using it

**In the window:** Settings → **Updates**. *Check now* asks GitHub. If something newer exists you see its version and what changed, then **Update to …**, which asks you to confirm. Afterwards, quit Halbach and open it again to use the new version. *Go back a version* puts the previous program files back.

**From a terminal:**

```
halbach update check
halbach update apply          (asks first; --yes skips the question)
halbach update rollback
```

**Once a day:** if *Look for updates once a day* is on (the default), the hub asks GitHub at most once every 24 hours whether a newer version exists and shows a banner. This only reads; nothing is downloaded or installed until you press Update. Switch it off in Settings.

## What it follows (the channel)

- **release** (default): the newest tagged version (`v0.6.0`) of the Halbach repository. Draft and pre-release versions are ignored. You get the release notes from GitHub.
- **branch:NAME**: the newest commit of a branch (for example `branch:main`). It compares commits, so a copy that is *newer* than the branch is never offered an older one. The "what changed" list is the commit titles in between.

The repository is fixed in the program (`Levatat3/HalbachConnectHub`). It is not a setting and nothing read from the network can change it.

## How the install stays safe

1. **Only GitHub, only https.** Every address and every redirect must be on GitHub's own hosts. The download is size-capped (40 MB).
2. **The archive is checked before anything is unpacked:** no unsafe or absolute file names, no `..`, no links, no password protection, one top folder, and only plain program and document files. A program-type file (`.exe`, `.dll`, `.bat`, `.pyd` and so on) inside the program folders refuses the whole update.
3. **Only the program is unpacked:** `halbach_connecthub/`, `third_party/`, and a few top-level documents (and the `docs/` files this copy already has). Tests, notes, build files and everything else in the repository are left out.
4. **It is test-started before it is installed.** The new copy is unpacked beside the old one and every module is imported in a separate, isolated Python. If anything fails, nothing is changed.
5. **A backup comes first.** The program files about to be replaced are copied to `program-backups/` in your state folder (the last five are kept).
6. **The swap is undoable.** Each item is moved aside and replaced one at a time. If any step fails (for example Windows has a file locked), everything already moved is put straight back.
7. **It is checked again after installing.** If the installed copy does not start, the old files are put back automatically.
8. **You confirm exactly what you saw.** If a newer version appears between your look and your click, the update stops and asks you to check again.

## What it will not do

- It will not update a **git checkout** (a folder with `.git`): it tells you to use `git pull`, so your own changes are never overwritten. It will not touch a copy installed with **pip** either.
- It will not install an older version, or the same version again.
- It does not start the new version for you. Quit and open Halbach again.
- It does not verify a signature: the trust is that the files come over https from the repository named above. If that GitHub account were ever taken over, an update could carry bad code, which is why you always see the version and what changed first, and why nothing installs without your click.

## For the person who publishes

- Tag a release (`v0.6.0`) on GitHub, or leave tags alone and let people follow a branch.
- The version a tag announces must match `__version__` in `halbach_connecthub/__init__.py` inside it, or the update is refused as mislabelled.
- A copy that does not have the updater yet (anything before the version that introduced it) has to be updated once by hand; after that it can update itself.
