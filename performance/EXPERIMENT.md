# Material Skin startup A/B experiments

This is a **diagnostic experiment**, not a proposed optimization for production. It measures the unchanged Material Skin installation on your local Lyrion server. The benchmark browser temporarily changes one served HTML response for the *early Now Playing* variant. **Nothing is deployed to LMS, no playback commands are sent, and your normal browser settings are not modified.**

## Why these experiments?

The prior ten `np-only` measurements showed that a cold page received `playerStatus` before `nowplayingOnPlayerStatus()` was available in 5/5 runs; on each cold run, title appeared roughly one second after a later `refreshStatus` event. The specific `1000ms` fallback timer was **not directly captured**. Moving the existing handler script before `main.js` tests the hypothesis without modifying anything else.

Material Skin's **Scrollable lists** in the main home view invoke `material-skin home-extra`, parse combined list data, then re-run the home grid layout. The second comparison measures the loading overhead of those rows. It does *not* assume this is the cause of a slow experience.

## Windows 11: run both A/B comparisons

From the root of your existing fork, after updating the experimental branch:

```powershell
git fetch origin
git switch experiment/early-nowplaying-and-home-lists
git pull --ff-only origin experiment/early-nowplaying-and-home-lists

.\performance\.venv\Scripts\python.exe performance\experiment.py --base-url "http://YOUR-LMS-SERVER:9000" --runs 5 --headed
```

If needed, set up Playwright in the same repository as described in [README.md](README.md). To select a player explicitly, add `--player "Living Room"`. The default is to discover a connected player using the read-only LMS API. Keep playback and server load reasonably stable. Never put an authentication token in the URL.

The default run performs **four scenarios**, five cold/warm pairs each, using identical Chromium viewports (1440×900) and fresh caches for cold starts:

| Scenario | Browser-only change | Primary measures |
| --- | --- | --- |
| `np-original` | No change | first status, handler ready, title visible |
| `np-early` | Load `nowplaying-functions.js` immediately after `nowplaying-page.js` and remove it from deferred list | same, for A/B comparison |
| `home-scrollable-on` | Grid home with `New Music`, `Random Releases`, `Playlists`, `Radios`, `Explore` | home-extra request and first visible header |
| `home-scrollable-off` | Same grid home, no scrollable lists | same baseline, without home-extra |

The home configuration corresponds to the five items in the supplied settings screenshot. Playwright sets `lms-material::detailedHomeItems` and `lms-material::top-grid` *inside its isolated contexts*, so you don't have to change your real Material settings. `Explore` changes how the home grid is arranged, but does not require an independent `home-extra` request.

Use one scenario pair at a time if desired:

```powershell
.\performance\.venv\Scripts\python.exe performance\experiment.py --base-url "http://YOUR-LMS-SERVER:9000" --mode now-playing --runs 5 --headed
.\performance\.venv\Scripts\python.exe performance\experiment.py --base-url "http://YOUR-LMS-SERVER:9000" --mode home --runs 5 --headed
```

The benchmark handles the `/material/np-only` JavaScript redirect and patches **only the final document** in the experimental case. The patch preserves the installed plugin's cache-buster. An incompatible document causes a deliberate error instead of silently producing a false comparison. **Do not install the experimental fork for this measurement**: the interception assumes the server still runs the original Material Skin code.

## Outputs and interpretation

Default: `performance/results/YYYYMMDD-HHMMSS-experiment/` (gitignored):

- `experiment-summary.md` — a human-readable comparison of the four scenarios and their cold/warm medians.
- `experiment-runs.csv` — individual browser timings and safe request category counts.
- `experiment-results.json` — machine-readable timings and sanitized aggregate resource statistics.

For Now Playing, compare **title visible** and **first player status** (not just `app ready`). For the normal home view, compare **home-extra request finish** and **first visible home header**; app-ready alone does not represent a populated home screen. A missing header is reported as `n/a` and should be investigated, not treated as a fast result. Network requests can overlap, so their total durations are not the wall-clock load time.

The initial benchmark's **`coverVisible` marker can count placeholder images**. The experiment retains the existing stricter `artworkCandidate` marker. The latter can still be a generic server image and does not by itself prove that the current album cover has fully rendered.

No HTTP request payloads, IP addresses, titles, player IDs, URLs, headers or cookies are written to these reports. Request payloads are only inspected transiently in memory to recognize the `material-skin home-extra` command. No HAR, screenshot or raw trace is recorded by this script.

### Caveats

- A Python/Playwright implementation measures Chromium, not Fire TV's Android WebView.
- A warm measurement means a re-navigation using the same browser context/cache, not resuming an already-open page.
- Production home preferences or third-party plugins may produce a different home configuration than this controlled five-list setup.
- The early script may increase initial parse/paint time even if it reduces title delay. Compare both metrics before deciding whether the change is beneficial.
- For server-side analysis (CPU, slow SQL, home-extra cost), a more detailed LMS profiling experiment would follow if the network/UI timing suggests it.

## Source experiment

The branch also changes `MaterialSkin/HTML/material/index.html` to load `nowplaying-functions.js` once, after `nowplaying-page.js` and before `main.js`. The benchmark's response rewrite mimics this change so the comparison can run on your *unchanged* LMS installation. This should be treated as a proof of concept until an A/B test demonstrates an improvement and verifies compatibility.

## Compatibility and troubleshooting

The initial A/B script assumed the running LMS returned a precisely formatted
`<script src="html/js/nowplaying-page.js?r=...">` tag. Installed Material Skin
versions may change attributes, query strings, file paths or bundle the startup
scripts. The A/B harness now reads external script tags, tries an anchor at
`nowplaying-page.js`, falls back to `main.js`, and finally (for a recognizable
deferred loader) inserts before `</head>`. It still requires exactly one
`nowplaying-functions` deferred-list entry, and refuses to compare if that
function is already loaded early. This remains an experiment, not a production fix.

Before running the scenarios, the tool checks the live `/material/` HTML once
using a read-only request. If the structure is unsupported, it exits with a
short **privacy-safe structural signature**, not a Playwright event-listener
stacktrace. No server HTML, network address, player ID or response payload is
saved. A failed browser-side rewrite likewise serves the original response and
reports an experiment error instead of crashing the event listener.

If your Material version is still unsupported, the **normal UI/scrollable-list
test** remains independent:

```powershell
.\performance\.venv\Scripts\python.exe performance\experiment.py --base-url "http://YOUR-LMS-SERVER:9000" --mode home --runs 5 --headed
```

For a quick compatibility check before the full experiment, run `--runs 1`.
The error message's counts (`nowplaying-page`, `main`, `deferred-entry`,
`scripts`, `has-head`) are safe to share. Do not share raw HTML, HAR, or browser
traces without redaction.
