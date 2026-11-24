# Material Skin: startup A/B benchmark

This is a read-only local Playwright benchmark against the user's **unchanged**
Lyrion Music Server. It does not deploy plugin changes or affect normal browser
settings. The experiment compares a single deferred-loader registration change
and two normal-home list configurations.

## Windows PowerShell

Run from your existing checked-out fork:

```powershell
git pull --ff-only origin experiment/early-nowplaying-and-home-lists

# One cold and one warm start per Now Playing variant:
.\performance\.venv\Scripts\python.exe performance\experiment.py --base-url "http://YOUR-LMS-SERVER:9000" --mode now-playing --runs 1 --headed --timeout-ms 12000
```

Only if both `np-original` and `np-early` show numeric `app` and `title`
values, and the visible UI is correct, proceed with more runs:

```powershell
.\performance\.venv\Scripts\python.exe performance\experiment.py --base-url "http://YOUR-LMS-SERVER:9000" --mode now-playing --runs 10 --headed
```

The report is written under `performance/results/<timestamp>-experiment/` as
`experiment-summary.md`, `experiment-runs.csv`, and `experiment-results.json`.

## How the early variant now works (no HTTP interception)

Previous browser-only approaches used `route.fetch()/route.fulfill()` to
rewrite Material's HTML. A real-LMS diagnostic isolated a defect in that
method: even when the **original, unmodified HTML** was fulfilled, Vue never
initialized and 17 requests failed. In contrast both the baseline browser
load and `route.continue()` succeeded. Hence results from those failed
variants do **not** measure a performance effect of the intended optimization.

The current `np-early` variant uses a Playwright **init script** in an
isolated browser context. Before the page's own scripts execute, the init
script intercepts only the registration

```javascript
window.addEventListener('load', loadOtherFiles, false)
```

and registers that existing, unchanged function for `DOMContentLoaded`
instead. Every other event listener is forwarded unchanged. **The HTML,
JavaScript bundles and CSS responses are served directly by Chromium,
without any Playwright request routing or interception.** No bundle is
executed synchronously during HTML parsing. The installed plugin remains
unmodified.

The benchmark prints `Early-load strategy: init-script window.addEventListener
hook (no HTTP interception)`. Its new result fields are:

- `early_hook_registered`: number of loader registrations matched (expected 1)
- `early_hook_fired`: number of early loader invocations (expected 1)
- `early_loader_fired_ms`: start-relative timestamp of the changed loader
  invocation

A missing or duplicated hook is a benchmark error. Independently check
JavaScript errors, HTTP failures, app-ready, title visibility, retry counts,
FCP and LCP. Numeric values alone do not certify correctness; visually check
the UI during the one-run smoke test. Stop rather than repeat broken runs.

A Chromium fixture validates that only the Material loader is redirected,
that its callback fires once, and that unrelated `window.load` listeners
still fire on the original event. Python unit tests and the GitHub CI workflow
validate the regression properties. A successful test on the real user's LAN
server is still required before claiming any speedup.

## Normal home screen with and without scrollable lists

```powershell
.\performance\.venv\Scripts\python.exe performance\experiment.py --base-url "http://YOUR-LMS-SERVER:9000" --mode home --runs 5 --headed
```

In isolated test-browser storage, the ON variant enables New Music, Random
Releases, Playlists, Radios and Explore; the OFF variant disables them.

Earlier measurements from five cold/warm pairs found:

| Metric (median) | Lists off | Lists on |
| --- | ---: | ---: |
| Cold app ready | 463 ms | 395 ms |
| Warm app ready | 232 ms | 226 ms |
| Cold LCP | 516 ms | 1540 ms |
| Warm LCP | 468 ms | 1304 ms |
| Cold request count | 91 | 149 |
| First home header, cold | n/a | 1651 ms |
| First home header, warm | n/a | 883 ms |
| Home-extra completed, cold | n/a | 851 ms |
| Home-extra completed, warm | n/a | 886 ms |

The home-extra response timing and first visible header represent separate
events. Response latency and parallel resource durations cannot be added.
The main Vue app-ready marker is not the same as the time until home content
has finished rendering.

## Debugging and privacy

The existing `--inspect-html` option identifies installed release assets.
The diagnostic `performance/diagnose_interceptor.py` records only categorized
errors and safely demonstrates the prior interception failure. It is **not**
part of the revised A/B execution.

Default results do not export server IPs, player IDs, song names, playlist
names, cookies, response bodies, HAR archives or screenshots. Each cold run
uses a fresh isolated Chromium context; its paired warm navigation reuses
that context. Server state and normal browser profiles are untouched.
Only run these tests against a trusted local server.

Installed releases use `material.min.js` and
`material-deferred.min.js`, constructed by the upstream `mkrel.py`.
**Do not merge or deploy a production plugin change until the A/B result and
functionality have been verified.**

PR: https://github.com/chrober/lms-material/pull/2
