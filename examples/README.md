# Example: rendering modules on your site

`modules.js` is the script a site uses to show what Halbach ConnectHub publishes. It reads `manifest.json`
and draws each module by type (page, doc, game, gallery, image, file) and shows an "offline" message for
live modules when your PC is off.

Add to your page:

```html
<div class="cards" id="moreGrid"></div>
<div id="moreView" hidden></div>
<script src="js/modules.js"></script>
<script>window.ConnectModules.init();</script>
```

It expects two element ids, `moreGrid` and `moreView`, and a few CSS classes (`card`, `screen`, `empty`,
`gallery`, `livebox`, `liverow`). Style them to match your site. Game modules load the Ruffle emulator
from a CDN. Page modules run in a sandboxed iframe.

`doc` modules are Markdown. The renderer in this file builds the page from DOM nodes (it never sets `innerHTML`), so a submission cannot inject script. Add the `.doc` styles from the author's stylesheet, or write your own.

The `claude-skill/` folder has a Claude Code skill (`halbach-publish`) that wraps a project up as a public-safe Markdown page and sends it to the hub's inbox with `halbach submit`.
