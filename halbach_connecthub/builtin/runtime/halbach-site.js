// halbach-site.js: the small generic runtime every generated page loads.   (SCAFFOLD: not implemented yet)
//
// Each generated page is a real HTML file (e.g. /workshop/index.html) containing:
//   - the page shell (brand, nav, <main id="hs-page">, footer), and
//   - the site model as inert JSON: <script type="application/json" id="hs-site">...</script>
// This file reads that JSON, draws the nav and the page's blocks, and loads each block's script on demand.
//
// Host API v1 (what block, view and theme plugins may rely on; see docs/PLUGINS.md):
//   HalbachSite.registerBlock(type, function (container, props, ctx) {})
//   HalbachSite.registerView(name, function (container, data, ctx) {})       // live-data looks, e.g. "pets"
//   ctx.el(tag, className, text)         build DOM safely (text goes in with textContent)
//   ctx.markdown(text) -> Node           safe Markdown renderer (no raw HTML)
//   ctx.module(id) -> manifest entry     ctx.base -> path prefix of this page's site root
//   ctx.live.run(box, moduleId, render)  poll a live module with online/offline handling
//   ctx.theme -> {id, vars}              ctx.onVisible(fn) -> run fn when the block scrolls into view
//
// Legacy links: if location.hash names a page (e.g. #more) the runtime redirects to its real path.
(function () {
  "use strict";
  window.HalbachSite = window.HalbachSite || { blocks: {}, views: {} };
  // TODO(phase 1): boot(), renderNav(), renderPage(), loadBlockScripts(), legacyHashRedirect()
})();
