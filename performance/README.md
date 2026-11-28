# Material Skin startup performance benchmark

A **read-only**, local, repeatable Chromium benchmark for [Material Skin](https://github.com/CDrummond/lms-material) on Lyrion Music Server. It measures the **installed, running Material Skin** served by your LMS instance, not a locally built frontend. No Material Skin source changes, plugin installation, server settings or playback commands are required.

The benchmark deliberately compares:

- **Normal Material UI**: `/material/?player=…`
- **Now Playing only**: `/material/np-only?player=…` (this route **redirects in JavaScript** to the normal Material UI with `msk--np-only` flags; it does *not* load a separate small frontend)
- **Cold**: a brand-new, isolated Chromium browser context with empty HTTP cache and storage.
- **Warm**: a second navigation to the same URL within that context, reusing its cache/storage. This is a **warm page reload**, not simply revisiting an already-open tab.

The benchmark runs five cold/warm pairs for each view by default and produces `results.json`, `runs.csv`, and `summary.md` with medians and P90 values. It also instruments the **initial player-status sequence** in the benchmark browser to test the hypothesis that the deferred Now Playing code sometimes misses the first status message. Everything runs on **your computer**, so the LMS host stays private on your LAN.

## Windows 11 quick start (PowerShell)

From the root of this GitHub fork:

```powershell
py -3.14 -m venv performance\.venv
.\performance\.venv\Scripts\python.exe -m pip install -r performance\requirements.txt
.\performance\.venv\Scripts\python.exe -m playwright install chromium
```

Run against the LAN address of your server (replace the *example* IP):

```powershell
.\performance\.venv\Scripts\python.exe performance\benchmark.py --base-url "http://192.168.1.10:9000" --runs 5 --headed
```

Use `--player "Your Player Name"` to select a specific player. Otherwise the script **only reads** LMS's `/jsonrpc.js` `players` command and selects the first player. It does not press Play or change server/player preferences. For `np-only`, an available player is required; without one the script reports an actionable error, rather than measuring an alert box.

If you use a different Python installation, replace `py -3.14` with the installed version. You may also run the script from any directory using its full path.

**Do not** point this at an Internet-exposed server; HTTP without TLS is commonplace on trusted LMS LANs. The tool does not support servers that require authentication.

## Files and metrics

Default output: `performance/results/YYYYMMDD-HHMMSS/` (gitignored). You can override with `--out C:\Temp\material-benchmark`.

| Metric | Meaning / caveats |
| --- | --- |
| `app_ready_total_ms` | Time from starting navigation to visible Vue `#app`, after `v-cloak` is removed. **Includes `np-only` redirect**. Primary comparability metric. |
| `np_visible_total_ms` | Visible `#np-page` in the fully expanded Now Playing view. May not occur for the normal UI. |
| `track_visible_total_ms` | Non-empty visible title in `#np-page .np-title`. Can be missing when player is idle. |
| `cover_visible_total_ms` | Visible, image-loaded `#np-page img.np-cover`. A placeholder cover also counts. |
| `artwork_candidate_visible_total_ms` | First image-loaded Now Playing cover whose source is **not** one of Material Skin's three known placeholder constants. This is a *candidate*, not proof of real cover art: an LMS-supplied generic image can still qualify. |
| `deferred_function_ready_total_ms` | First observation that `nowplayingOnPlayerStatus()` from the deferred `nowplaying-functions.js` is available. |
| `bus_hook_ready_total_ms` | When the benchmark attached its passive event-bus observer; status events before this point cannot be counted. |
| `first_player_status_total_ms` | First observed `playerStatus` event received from Material Skin's event bus. |
| `first_status_with_title_total_ms` | First observed status event containing a nonempty track-title field. The **value is never saved**. |
| `first_status_had_deferred`, `status_before_deferred_count` | Whether the deferred handler was available at the first observed status event and how many status events preceded it. |
| `first_retry_scheduled_total_ms`, `retry_scheduled_count` | A 1000-ms timer scheduled from `nowplaying-page.js` during a player-status event – matches the existing fallback code's signature. It is not proof of the underlying exception's cause. |
| `first_refresh_after_retry_total_ms` | A later `refreshStatus` event within 850–1350 ms after the first retry timer was scheduled. Timing correlation, not a definitive causal link. |
| `request_timeline` | First/last start/finish windows for generic JSON-RPC, CometD and script categories, plus the `nowplaying-functions.js` resource when observed. Relative to **final document navigation**, unlike total metrics. |
| `ttfb_ms` | Navigation `responseStart`, relative to the **final** HTML document (does not include the `np-only` redirect). |
| `dom_content_loaded_ms`, `load_end_ms` | Final document navigation timings. `loadEnd` may be `0` if observation ends early. |
| `fcp_ms`, `lcp_ms` | Browser paint metrics for the final document. Missing values are explicitly null. |
| `longtask_*` | Number/duration of observed main-thread tasks longer than 50 ms. |
| `resource_groups`, `slowest_resources` | Aggregated resource counts, transfer sizes and slowest requests (static JS filenames or **generic categories**, never URL query strings). |
| `http_errors`, `failed_requests`, `script_errors` | Error counts for the observation window. Long-polling/CometD requests can occasionally be canceled as part of a navigation. |

The tool uses a small `PerformanceObserver`/`MutationObserver` probe injected **only into the benchmark browser**. For startup diagnostics, it also observes `bus.$emit` (only the event name and whether a title exists) and watches for a 1000-ms `setTimeout` scheduled inside the upstream Now Playing status handler. All original callbacks and delays are forwarded unchanged; Material Skin itself is not modified. This can introduce a little measurement overhead and should not be treated as an official lifecycle API. It does not install or modify code on your LMS server. Readiness is an *observable UI proxy*, not an official Material Skin lifecycle event. CPU profiles will help validate why a metric is slow; they do not directly identify server-side bottlenecks. `resource_groups.duration_ms_total` sums durations of overlapping requests and is **not** the elapsed wall time.

The `summary.md` is a quick comparison; per-run details and sanitized slowest-resource lists are in `results.json`. `runs.csv` is convenient for Excel or further statistical analysis. The browser window size is fixed at 1440×900, device scale factor 1; keep the same machine, server state, display settings and player for before/after comparisons. For best comparability, keep a track playing during measurements, but the benchmark never starts playback itself.

**Security/privacy:** The normal `results.json`, `runs.csv` and `summary.md` do **not** contain server IP/host, player ID/name, track titles, full request URLs, headers, cookies or response bodies. They may still reveal performance characteristics or static asset names. All result directories are excluded by `.gitignore`.

## Optional deeper diagnostics

```powershell
# First cold run for each view: DevTools trace + one screenshot
.\performance\.venv\Scripts\python.exe performance\benchmark.py --base-url "http://192.168.1.10:9000" --player "Your Player Name" --runs 1 --headed --cdp-trace --screenshots

# Full Network HAR recordings for each cold/warm pair
.\performance\.venv\Scripts\python.exe performance\benchmark.py --base-url "http://192.168.1.10:9000" --runs 1 --har
```

`--cdp-trace` saves `<view>-cold-cdp.json` (first cold run only); open it from Chrome/Edge DevTools **Performance → Load profile** or `chrome://tracing`. `--har` saves `<view>-01.har` for the paired cold and warm navigations (response bodies omitted); open in DevTools Network or a HAR viewer. `--screenshots` saves a first cold browser screenshot per view.

**WARNING:** These opt-in files are **raw and sensitive**. Traces/HAR can contain your LAN IP address, complete URLs and query parameters (including player IDs), headers and potentially playlist or authentication information; screenshots display private music details. Keep them on your computer and sanitize them before sharing. They are excluded from Git by default. Never upload raw traces/HAR to a public issue.

## Investigating the initial player-status delay

The full result includes a new **First-player-status diagnostics** table in `summary.md`. These are the most useful values for `/material/np-only`:

- **First status earlier than deferred function** (`first_player_status_total_ms` < `deferred_function_ready_total_ms`), followed by `status_before_deferred_count > 0`, suggests a load-order problem.
- **Retry scheduled** (`retry_scheduled_count > 0`) indicates that a 1000-ms timer was scheduled in the upstream Now Playing event-handling code; combined with the missing-function observation, it strongly supports the suspected fallback path.
- **Function ready before first status, no retry**, but title still late: investigate LMS/CometD status delivery, Vue rendering, or the actual server response instead.
- **A title-bearing status arrived, but the UI title appeared much later**: look for errors in handling the status, or rendering delays.

Not all fields will appear: they can be null if the player is idle, the script never finishes, the browser attaches after an event, or an alternative Material Skin version uses another code path. **A missing or zero retry count is not proof no retry occurred**, particularly if the event-bus observer attached late. Compare the `bus_hook_ready_total_ms` offset to `first_player_status_total_ms`.

The diagnosis is intentionally *non-invasive*: no exceptions are suppressed, no network requests are delayed or blocked, and no player-status contents are retained. It cannot prove why the fallback was scheduled without more targeted instrumentation in a separate test branch.

Allow adequate observation time for slower player status retrieval (default `--observe-ms 3500`, or explicitly `--observe-ms 5000`). A second run with `--view np-only --runs 5 --headed` is enough; repeating the normal UI is optional.

```powershell
.\performance\.venv\Scripts\python.exe performance\benchmark.py --base-url "http://192.168.1.10:9000" --view np-only --runs 5 --headed --observe-ms 5000
```

The local server address above is an **example** and must be replaced. Please share only `summary.md`, `runs.csv` and `results.json`; do not upload opt-in raw HAR/CDP recordings or screenshots unless they have been carefully sanitized.

## Troubleshooting and interpretation

- **Playwright's browser is not installed:** Run `python -m playwright install chromium` using the same virtual environment. `--chromium-path` can also point to a local Chromium executable.
- **No player found:** Check that a player is connected to LMS, or specify `--player`. The tool selects the first reported player only if no override is provided.
- **`np-only` redirects:** Expected. Its initial redirect page has a small cost; `app_ready_total_ms` includes it, whereas final-document TTFB/FCP do not.
- **`track_visible_total_ms` is empty:** Start playback before measuring, or evaluate `app_ready_total_ms` and `np_visible_total_ms` instead.
- **Timeout / missing `app_ready_total_ms`:** Check `/material/` in an ordinary browser first, whether Material Skin is enabled, whether the server address is reachable, and whether authentication is required. The script returns a nonzero exit code if any sample never becomes app-ready.
- **Different results on second run:** Do not compare one-off runs. Repeat on the same device and look at the median/P90 for each cache scenario.
- **Headless vs headed:** Headed (`--headed`) is closer to an interactive desktop; keep the same mode for comparisons. Neither mode exactly reproduces Android System WebView/Fire TV performance.
- **Server-side delays:** If TTFB or JSON-RPC requests are slow, inspect LMS CPU/load/I/O separately. This browser benchmark does not collect server process metrics.

Run unit tests without an LMS server. The additional browser probe test runs when Chromium is installed (otherwise it is skipped):

```powershell
.\performance\.venv\Scripts\python.exe -m unittest discover -s performance -p "test_*.py" -v
```

## Future improvements

After a baseline, compare identical runs against a temporary Material Skin test installation based on a selected fork commit. Instrumentation in the plugin, if needed, should be a **separate** optional change driven by actual slow traces, not a prerequisite for collecting this baseline.
