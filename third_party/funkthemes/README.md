# FunkThemes (third-party, by Funk)

FunkThemes is Funk's shared theme engine and theme maker, used across his apps (Claude Watch, FunkStudio, FunkDrop, ...). Halbach ConnectHub 0.4 uses it for two things: the **Funk theme** for the modular site (gradients and animated backgrounds), and a **window theme** for the Halbach window itself. Funk is a co-developer of this integration.

- Source: the `FunkThemes-2026-10-17` drop, sent by Funk. The engine was updated on 2026-10-22 to **FunkThemes 2** (the Claude Watch master of 2026-10-22 19:00, sent by Funk's Claude over Chat Link); the sample themes, `Theme Maker.html` and `for-developers/` below are still from the 10-17 drop.
- `halbach_connecthub/builtin/themes/funk/funktheme.js` is the FunkThemes 2 `funktheme.js`, **byte for byte**. A test pins its fingerprint so it stays his file; to take a new version, replace the file and update the fingerprint in `tests/test_funktheme.py` in the same commit.
- `halbach_connecthub/funkthemes.py` is his `for-developers/themes.py` (the shared library code) with one small addition: the `HALBACH_FUNKTHEMES_DIR` variable, so tests and other platforms can move the folder.
- `halbach_connecthub/builtin/themes/funk/theme.js` and `theme.css` are Halbach's glue: they apply a theme and map the engine's tokens (`--s1`, `--brand`, ...) onto Halbach's variables.
- The files in this folder (`README.txt`, `Theme Maker.html`, `themes/`, `for-developers/`) are the rest of his drop, kept for reference and as the sample themes the tests use. `Theme Maker.html` still works on its own.

## How it fits

- **Shared library.** Themes live in `%LOCALAPPDATA%\FunkThemes` as `.funktheme` files, the same folder his apps use. A theme saved in the maker inside Halbach appears in his apps, and the other way round.
- **On the public site** a theme is copied into the site model (and so into the History snapshots) and drawn by the engine in the visitor's browser. Halbach validates every `.funktheme` before it goes into the model: only plain colours, numbers and effect names are kept, and picture themes are refused for now. FunkThemes 2's extra settings are accepted with strict lists: the `spectrum` effect, each effect's own controls (`fxValues`: only the keys that effect declares, each of the right type and range), up to two extra effect layers (`fxLayers`: opacity, one of five blend modes, own speed, amount, colour and controls) and `motion` (fps 15/30/60, half resolution, mouse drift, pause behind fullscreen). `motion.listen` (react to music) is always forced off on a site, as there is no audio source there. Still refused or dropped: `image`, `video`, `web` and `link` styles (Wallpaper Engine), any `asset:` value and `sound`. The engine still contains the code for those; on a site they can never be reached, and a test fails if the engine gains an effect or control the validator has not been looked at for.
- **In the window** the maker previews live; pressing Done saves the choice in the browser's own storage (the window theme) or in the site (the site theme).

- **Credit line.** Every page that uses the Funk theme shows "Powered by FunkThemes" in its footer (and on the 404 page). Each page has a switch in the page editor, and the Halbach window shows the same line while a Funk window theme is on. Switching it off is allowed but noted in the site checks.

## For Funk: things to confirm

- That the licence for `funktheme.js` and `themes.py` is fine for Halbach's repository (MIT, public on GitHub). There is no licence file in the drop, so we have assumed MIT; say so if you want a different notice.
- That the token names (`--bg --s1 --s2 --s3 --line --line2 --text --dim --faint --brand --brand2 --grad --page --page-blur --solid`) are a stable contract, because Halbach's mapping depends on them.
- Picture themes: the site refuses them for now (size, and copies in history). If you would rather have them as image files next to the site, tell us.
