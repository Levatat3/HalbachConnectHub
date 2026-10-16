# Example: rendering modules on your site

`modules.js` is the script a site uses to show what ConnectHub publishes. It reads `manifest.json`
and draws each module by type (page, game, gallery, image, file) and shows an "offline" message for
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
