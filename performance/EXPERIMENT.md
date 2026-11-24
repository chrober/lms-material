# Material Skin startup A/B experiments

A repeatable Playwright benchmark against the user's **unchanged** local Lyrion Music Server. All changes are applied only to isolated test-browser contexts; no plugin installation, server changes, browser profile changes, or playback actions are required.

## Windows setup

From the checkout of `chrober/lms-material`:

```powershell
git fetch origin
git switch experiment/early-nowplaying-and-home-lists
git pull --ff-only origin experiment/early-nowplaying-and-home-lists

# Only if not previously installed:
.\performance\.venv\Scripts\python.exe -m pip install -r performance\requirements.txt
.\performance\.venv\Scripts\python.exe -m playwright install chromium
```

## Experiments

### Now Playing: DOMContentLoaded vs window.load

The browser-only variant **np-early** replaces a single event name in the served HTML:

```javascript
window.addEventListener("load", loadOtherFiles, false);
```

becomes

```javascript
window.addEventListener("DOMContentLoaded", loadOtherFiles, false);
```

No JavaScript source file, bundle, URL, loading order, or actual loader is changed. `loadOtherFiles()` continues to load the deferred JS and CSS in precisely the same way, but is triggered as soon as the HTML finishes parsing rather than waiting for all assets. Both unbundled checkout HTML and compiled release HTML use this loader registration.

This supersedes the previous experimental attempt to **synchronously** inject the entire `material-deferred.min.js` into the HTML. That attempt was invalid: in the first real LMS test it prevented the application from initializing (timeouts, one JavaScript error, 17 failed resources). **Do not deploy the previous code.** The current A/B variant tests a smaller event-timing change, not a synchronous bundle move.

One smoke run:

```powershell
.\performance\.venv\Scripts\python.exe performance\experiment.py --base-url "http://YOUR-LMS-SERVER:9000" --mode now-playing --runs 1 --headed
```

The console should report `Validated early-load strategy: DOMContentLoaded deferred loader (packaged)` for the installed production release.

If **both** `np-original` and `np-early` initialize and display a title (app and title must be numbers, not `None`), run ten alternating pairs:

```powershell
.\performance\.venv\Scripts\python.exe performance\experiment.py --base-url "http://YOUR-LMS-SERVER:9000" --mode now-playing --runs 10 --headed
```

Compare app-ready, deferred-function-ready, first player status, title-visible, 1000-ms retry counts, FCP/LCP, failed requests and script errors. The intended success condition is earlier title visibility **without** regressions in correctness or initial app readiness. The benchmark cannot itself certify full UI correctness; inspect the headed browser if anything looks wrong.

### Normal home screen: scrollable lists on/off

```powershell
.\performance\.venv\Scripts\python.exe performance\experiment.py --base-url "http://YOUR-LMS-SERVER:9000" --mode home --runs 5 --headed
```

The independent `home-scrollable-on` test enables New Music, Random Releases, Playlists, Radios and Explore; `home-scrollable-off` disables those lists. Both force the home grid view only inside the benchmark browser.

The user's initial measurements (five cold and five warm runs per setting) showed:

| Median | Lists OFF | Lists ON |
| --- | ---: | ---: |
| Cold app-ready | 463 ms | 395 ms |
| Warm app-ready | 232 ms | 226 ms |
| Cold LCP | 516 ms | 1540 ms |
| Warm LCP | 468 ms | 1304 ms |
| Cold request count | 91 | 149 |
| First visible home header, cold | n/a | 1651 ms |
| First visible home header, warm | n/a | 883 ms |
| Home-extra completed, cold | n/a | 851 ms |
| Home-extra completed, warm | n/a | 886 ms |

The home requests and rendering add substantial work, but *app-ready is not the same as a fully populated home screen*. Request durations overlap; do not sum them. The early Now Playing race may also occur when the normal UI is used.

## Files and privacy

A timestamped directory under `performance/results/` contains:
- `experiment-summary.md` — scenario medians and error counts
- `experiment-runs.csv` — per-run measurements
- `experiment-results.json` — detailed sanitized timings/resource categories

Share these three files for analysis. Server addresses, track names, player identifiers, request payloads, cookies, HARs and screenshots are not written by default. The `--inspect-html` option prints sanitized script filenames and response structure without exporting the HTML. Both the UI experiments and the inspection should be run **only against a trusted local server**.

The performance measurements use a dedicated Chromium context for each cold run and a repeated navigation within that same context for a warm run. The test rotates the scenario order to reduce systematic bias.

## Development and status

- Source repository: https://github.com/chrober/lms-material
- Pull request: https://github.com/chrober/lms-material/pull/2 (draft until LAN validation)
- The root `mkrel.py` packages source files into `material.min.js` and `material-deferred.min.js`; do not expect individual JS script tags in an installed release.
- The experimental source-file change in `MaterialSkin/HTML/material/index.html` is a **separate proof of concept** and is not required or installed for the browser-only benchmark.
- Do **not** merge or deploy the experimental plugin code without controlled A/B measurements and functional verification.

## Diagnosing an unresponsive browser-only A/B variant

If np-early displays raw Vue template markers such as {{ snackbar.msg }},
do NOT run additional performance pairs. This means application initialization
did not complete, so there is no valid performance comparison.

Run the isolated interception diagnostic once to distinguish Playwright HTML
routing problems from problems with the modified loader timing:

    .\performance\.venv\Scripts\python.exe performance\diagnose_interceptor.py --base-url "http://YOUR-LMS-SERVER:9000" --headed

It checks five independent cases with fresh browser contexts:
baseline (no route); continue (route passthrough); fulfill-original
(response re-served unchanged); fulfill-text (same HTML text re-served);
modified (the DOMContentLoaded experiment).

The diagnostic prints only aggregated response/failure categories, app-ready
booleans, and redacted JavaScript error categories. It does not save HTML,
URLs, credentials, cookies or track metadata. The installed LMS remains
unchanged. A result showing that fulfill-original fails while continue
succeeds would implicate the interception/response forwarding mechanism.
If only modified fails, investigate the rewritten event timing instead.

