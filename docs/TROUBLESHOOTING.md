# Troubleshooting and FAQ

Plain fixes for the things that go wrong most often. Every entry below comes
from what the code actually does; if something here does not match what you see,
that is worth reporting.

| What you see | Why | What to do |
|---|---|---|
| **A window theme did not stick.** You changed colours or a background and the window went back to the old look. | The theme maker has two buttons that do different jobs. **Save** puts the theme in the shared FunkThemes library; **Done** applies the theme you are looking at. They are not the same button. | Press **Done** to apply it (press **Save** as well if you want it kept in the library). The hub also keeps the window theme itself, so it now survives a new port, a cleared browser profile and a restart. **Settings → Window theme → Reset** goes back to the default. |
| **The Site preview is blank**, or it says *There is no preview yet. Press Preview first.* | Nothing has been built into the preview folder yet, or the last build had problems and produced nothing. The preview only serves what a successful build wrote. | Open **Site → Preview** and press **Rebuild preview**. If it reports problems instead of building, fix them first: **Site → Overview** lists each one with the page or block it is on. |
| **Publish says there are problems to fix first.** | The publish plan refuses to run while the modular site has errors, so a broken page cannot go out. | **Site → Overview**: the red lines are the errors, each naming the page or block. Fix them and press Publish again. Warnings do not stop a publish. |
| **Publishing fails with a Node.js message** (*Node.js (npx) isn't installed or isn't on PATH. Install Node.js LTS first.*) | Publishing runs `wrangler pages deploy` through `npx`, and `npx` is not on your PATH. | Install Node.js LTS, then reopen the hub so it picks up the new PATH, and Publish again. |
| **Publishing fails with a Cloudflare sign-in message** (*Not signed in to Cloudflare. Run: npx wrangler&#64;4 login*). | Halbach uses your own `wrangler` login and has not found one (or it expired). | Run `npx wrangler@4 login` once in a terminal, then Publish again. Halbach never handles your password. |
| **Publishing fails about the Pages project** (*Cloudflare doesn't know a Pages project with that name*). | The project name in Settings does not match a Pages project in your Cloudflare account. | Check the name in **Settings** against the Pages dashboard, save it, and Publish again. |
| **The tunnel will not connect.** Settings shows *cloudflared not found*, or the tunnel stays stopped/not connected. | `cloudflared` is not installed or not on your PATH, or the one-time `cloudflared tunnel login`/`create`/`route` steps have not been done. | Put `cloudflared.exe` on your PATH, or in the `bin` folder inside the state folder, do the steps Settings lists (once), then press **Start tunnel**. Nothing looks for `cloudflared` anywhere else, and Halbach does not download it for you. |
| **An area upload is refused.** The sender sees plain reasons; the window shows the same reasons on the area's **Review** tab. | Every bundle is treated as hostile and checked before anything is kept. Common reasons: a file type the area does not allow, bytes that do not match the file's extension, a path or file-name trick, too many or too large files, a block the area does not allow, or a malformed `area.json`. | The reason names the file or the block that failed. Fix it and send again. The full list of checks is in [AREAS.md](AREAS.md#what-is-checked-and-refused-with-a-plain-reason); by default no HTML, scripts, SVG, programs or archives are allowed. |
| **A sender is locked out for 15 minutes.** | Ten wrong passphrases from the same address lock that address out (a deliberate limit), so guesses cannot grind away at the key. | Wait 15 minutes and send again, or make a new passphrase for the area. A wrong key, an unknown area and a switched-off area all answer the same way, so nothing is leaked. |
| **A published page says *This page needs JavaScript to show its content.*** | The modular site draws its blocks in the browser with JavaScript. If JavaScript is off or blocked, only that notice is shown. | Turn JavaScript on for the site. This is expected today: pages are not yet pre-rendered for browsers without JavaScript. |

## Where to look next

- Running it, settings and the publish rules: [README.md](../README.md).
- The modular site (pages, blocks, themes): [MODULAR.md](MODULAR.md).
- Areas and linked sites: [AREAS.md](AREAS.md).
- Design and threat model: [ARCHITECTURE.md](ARCHITECTURE.md).
