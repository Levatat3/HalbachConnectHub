# Security review of the public surface (task 2.7)

Single-reviewer pass by Beam (2026-10-19) over the code listed in the handoff plan, using section 8 of
`_notes/HANDOFF-PLAN.md` as the checklist. An independent second reviewer is still desirable before release.
Companion files: `_notes/TECHNICAL-AUDIT.audit` (DeepSeek) and `_notes/PROJECT-AUDIT.audit` (LongCat).

## Scope reviewed
`dropserver.py`, `areas.authenticate` + `areas.py` (scrypt, lockout, submissions, review, versions, audit, tree merge),
`bundle.py`, the area merge in `sitebuilder.py`, `linkedsites.py`, `live.py`, `server.py` `/preview` route,
plus `store.py`, `siteschema.py`, `registry.py`, `sitemodel.py`.

## Method
For each of the 18 invariants in section 8: read the guarding code and the named tests, mark OK / PARTIAL / GAP,
and record any new hostile-corpus cases worth adding. No source was changed by this review; findings are listed
below for the owner to decide.

## Invariant checklist

| # | Invariant | Status | Note |
|---|---|---|---|
| 1 | Window API needs per-launch token + matching Host + same-origin Origin | OK | `server.py` checks all three; tests in `test_server.py`. |
| 2 | Live hub serves only read-only module data and (with a usable area) `/drop/` | OK | `live.py` route table; `test_live.py`. |
| 3 | Drop checks key before reading body; wrong/unknown/disabled area give same 401 | OK | `dropserver.handle` authenticates before `_receive`; `test_dropserver`. |
| 4 | Brute force locks the address out; locks expire | OK | `areas.authenticate` + `Locked`; tests present. |
| 5 | Upload size enforced from Content-Length before and while streaming; slow senders cut off; nothing left in quarantine | OK | `_receive` checks length, reads with deadline, `finally` removes tmp. |
| 6 | Hostile zip never writes outside temp folder; nothing left on failure | OK | `bundle.validate_bundle` + tests. |
| 7 | Area writes only under its own address; files sandboxed; symlinks never copied | OK | `sitebuilder` area merge confined to `/<slug>/`; symlink tests skipped on this Windows account (run under Linux). |
| 8 | Area content cannot contain scripts/HTML/SVG/modules/outside links/unallowed blocks | OK | `bundle` allow-lists; tests. |
| 9 | Passphrases shown once, stored as scrypt; audit holds no keys/raw addresses; errors include no server paths | OK | `areas` + `dropserver.GENERIC`. |
| 10 | Model reaches page as inert JSON; drawn with textContent | OK | `sitebuilder` escaping + runtime. |
| 11 | URLs allow-listed (https/http/mailto/#/site-relative); CSS variable values validated | OK | `siteschema`. |
| 12 | `.funktheme` reduced to colours/numbers/effect names; pictures refused; unknown keys dropped | OK | `siteschema.clean_funktheme`. |
| 13 | Funk's engine byte-for-byte his; no network calls | OK | `test_funktheme.TestEngine`. |
| 14 | Key in linked address needs explicit confirmation; never printed; private hosts never probed | OK | `linkedsites` + tests. Probe now pins the resolved IP (task 2.9) to close the DNS-rebinding race. |
| 15 | Classic mode untouched | OK | `test_publisher`, `test_siteservice`. |
| 16 | Refused edit leaves model unchanged; home page cannot be deleted | OK | `siteservice` restore-on-refusal. |
| 17 | Preview never leaves the machine; never serves files outside preview folder | OK | `server.py` `/preview` + test. |
| 18 | Shared FunkThemes folder written only with sanitised names inside it | OK | `test_funktheme`. |

## New / confirmed findings (for the owner)

1. **Funk theme XSS surface in the owner window (MED, TA-001).** `funktheme.js` interpolates theme values into `style`/`value` attributes without escaping; the shared library returns any `*.funktheme` verbatim and Import merges an arbitrary file into the live theme. Script execution is currently blocked by the window CSP, so the residual is UI spoofing/phishing (forms allowed by `form-action`), but a future CSP regression turns it into owner-token XSS. **Recommendation:** sanitize themes at the hub boundary the way `clean_funktheme` does, but allowing `data:` images; coordinate upstream escape work with Funk. Also note the engine is hash-pinned, so upstream changes need a digest update.

2. **Unvalidated submission id is a path traversal (MED, TA-002).** `areas.py` `_sub_dir` joins `sid` straight into a path; `approve`/`reject` operate on it. Server-generated ids mean only the owner token can reach it (MED not CRITICAL), but a crafted id escapes the area folder. **Recommendation:** regex-validate `sid` inside `_sub_dir` (`^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$` or the existing sortable-id shape). Cheap, high value.

3. **`CF-Connecting-IP` trusted verbatim (LOW, TA-003).** Used for lockout, per-address limits and audit identity; any local process can rotate it. Known gap 5.6; acceptable while the live hub is loopback-bound. **Recommendation:** document; no code change unless the hub is ever exposed directly.

4. **No anti-framing on the window (LOW, TA-004).** The hub's own pages send no `X-Frame-Options`/`frame-ancestors`. Only relevant if an attacker can get the owner to visit a page that frames the hub; the token + Origin checks still gate every action. **Recommendation:** add `X-Frame-Options: DENY` (or `frame-ancestors 'none'`) to window responses, low cost.

5. **Linked-site probe gaps (LOW, TA-005).** The probe does not re-check that the connected peer is the vetted IP's owner beyond the initial resolve; now pinned (2.9). Also note the probe resolves the host twice in the *old* code; fixed. Remaining: the response body is read up to 2048 bytes and discarded (fine).

6. **Stuck `.part` files on crash (LOW, TA-006).** An upload interrupted by a process crash leaves a `.zip`/`.part` in quarantine; the normal `finally` handles the graceful case. **Recommendation:** sweep stale files older than N hours on startup (optional).

7. **Audit best-effort (LOW, TA-007).** `audit.jsonl` append is best-effort; a full disk drops entries silently. Acceptable for a single-user tool; note it.

8. **Theme window CSP regression risk (INFO).** See finding 1. The window CSP must keep `script-src` strict; a regression turns TA-001 into full XSS. **Recommendation:** add a test asserting the window CSP string contains no `unsafe-inline`/`unsafe-eval`.

## Hostile-corpus additions recommended
- Bundle: a zip whose `area.json` has a `sid`-like traversal in a top-level key (already covered by traversal tests, but add an explicit `../` in `submission` id if any).
- Themes: a `.funktheme` whose `name` or a colour value contains `</style><script>`; assert it is refused or sanitized, and that the window never renders it unescaped.
- Linked sites: a host whose DNS returns a private IP after a public one (rebinding) — covered by the new resolver test; extend to a second resolution returning a different IP.

## Conclusion
No CRITICAL or HIGH findings. The two MED items (TA-001 theme XSS surface, TA-002 sid traversal) are the priority fixes before release; both are small and localized. The 18 invariants hold as implemented. Symlink handling (invariants 7) remains unverified on this Windows account and must be run under Linux/WSL before release.