# A/B startup experiments for Material Skin

This branch contains two **browser-only** comparisons. Run them on your Windows laptop against your normal Lyrion Music Server. **No modified Material Skin plugin needs to be installed on the server.** The Python script intercepts only the test browser's HTML response for the early-loading variant and uses isolated localStorage for the home-list scenarios.

## Windows PowerShell

After switching to this experiment branch, use the same Python virtual environment from the existing [benchmark instructions](README.md).

```powershell
git fetch origin
git switch experiment/early-nowplaying-and-home-lists
git pull --ff-only origin experiment/early-nowplaying-and-home-lists

.\performance\.venv\Scripts\python.exe performance\experiment.py --base-url "http://YOUR-LMS-SERVER:9000" --runs 5 --headed
```

Add `--player "Living Room"` if you wish to select a particular player; without it the tool detects one by a read-only LMS request. For a shorter targeted run, use `--mode now-playing` or `--mode home`. Use the same network, player and music playback state for comparison.

| Scenario | What changes *only in the test browser*? |
| --- | --- |
| `np-original` | Upstream loading sequence |
| `np-early` | `nowplaying-functions.js` is moved from the deferred list directly after `nowplaying-page.js` and before `main.js` |
| `home-scrollable-on` | Home grid with New Music, Random Releases, Playlists, Radios and Explore |
| `home-scrollable-off` | Same home grid without scrollable lists |

The settings match the five rows in your supplied screenshot. On/off variants are set through `lms-material::detailedHomeItems` in isolated Playwright contexts. The normal browser profile and LMS settings remain unchanged.

The script cycles the scenario order and runs paired fresh-context cold / same-context warm navigations. It tracks the first player status, deferred JS readiness, title visibility, initial home-extra request completion, and first visible grid header. **App-ready alone is not sufficient to judge a home page with additional lists.**

Output is saved under `performance/results/YYYYMMDD-HHMMSS-experiment/`: `experiment-summary.md`, `experiment-runs.csv`, and `experiment-results.json`. Share these three with ChatGPT. No IP addresses, player IDs, track names, request payloads, cookies, HAR files or screenshots are written. The script inspects LMS JSON-RPC payloads transiently only to classify the `home-extra` request.

The actual source change is additionally present in `MaterialSkin/HTML/material/index.html`, **only as a proof of concept**. Do not deploy the modified plugin yet. First confirm the measured A/B outcome and ensure early loading does not make the initial screen slower. If the currently installed server plugin has already applied the same change, the experimental HTML rewrite deliberately reports an incompatibility rather than silently producing a misleading result.

**Note:** A header may not appear if home data is unavailable; this is reported as n/a. Request durations may overlap. Material Skin can return generic artwork, and the existing title/cover markers remain observable proxies rather than internal lifecycle signals.
