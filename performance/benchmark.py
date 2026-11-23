#!/usr/bin/env python3
"""Read-only, repeatable Chromium startup benchmark for LMS Material Skin.

Browser data/URL queries are not written to the default results. CDP traces and
screenshots are explicitly opt-in because they can include personal information.
"""
from __future__ import annotations

import argparse
import asyncio
import csv
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import statistics
import sys
import time
from urllib.parse import urlencode, urlsplit, urlunsplit

try:
    from playwright.async_api import async_playwright, Error as PlaywrightError
except ImportError:
    raise SystemExit("Playwright missing. Install: python -m pip install -r performance/requirements.txt")


PROBE_SCRIPT = r"""(() => {
  const values = { appReady: null, npVisible: null, trackVisible: null,
                   coverVisible: null, longTasks: [], fcp: null, lcp: null };
  Object.defineProperty(window, '__materialBenchmark', {value: values});
  try {
    new PerformanceObserver(list => {
      for (const item of list.getEntries()) values.longTasks.push(item.duration);
    }).observe({type: 'longtask', buffered: true});
  } catch (_) {}
  try {
    new PerformanceObserver(list => {
      for (const item of list.getEntries()) {
        if (item.name === 'first-contentful-paint') values.fcp = item.startTime;
      }
    }).observe({type: 'paint', buffered: true});
  } catch (_) {}
  try {
    new PerformanceObserver(list => {
      for (const item of list.getEntries()) values.lcp = item.startTime;
    }).observe({type: 'largest-contentful-paint', buffered: true});
  } catch (_) {}
  let queued = false;
  function visible(element) {
    if (!element || !element.isConnected) return false;
    const style = window.getComputedStyle(element);
    return style.display !== 'none' && style.visibility !== 'hidden'
      && element.getClientRects().length > 0;
  }
  function check() {
    queued = false;
    const root = document.getElementById('app');
    const mounted = root && !root.hasAttribute('v-cloak')
      && visible(root.querySelector('.lms-app'));
    if (mounted && values.appReady === null) values.appReady = performance.now();
    const np = document.getElementById('np-page');
    if (mounted && visible(np) && values.npVisible === null) {
      values.npVisible = performance.now();
    }
    const title = np && np.querySelector('.np-title');
    if (mounted && visible(np) && visible(title) && title.textContent.trim()
        && values.trackVisible === null) values.trackVisible = performance.now();
    const cover = np && np.querySelector('img.np-cover');
    if (mounted && visible(np) && visible(cover) && cover.complete
        && cover.naturalWidth > 0 && values.coverVisible === null) {
      values.coverVisible = performance.now();
    }
  }
  function schedule() {
    if (!queued) {
      queued = true;
      requestAnimationFrame(check);
    }
  }
  new MutationObserver(schedule).observe(document, {subtree:true, childList:true, attributes:true, characterData:true});
  document.addEventListener('DOMContentLoaded', schedule, {once:true});
  window.addEventListener('load', schedule, {once:true});
  schedule();
})()"""

JS_RESULTS = r"""() => {
  const nav = performance.getEntriesByType('navigation')[0];
  const timings = window.__materialBenchmark || {};
  const stats = performance.getEntriesByType('resource');
  const resources = stats.map(r => ({
    type: r.initiatorType, duration: r.duration, transferSize: r.transferSize,
    encodedBodySize: r.encodedBodySize,
    // Browser-side classification only: never export original request URLs.
    pathClass: (() => {
      try {
        const u = new URL(r.name, location.href);
        if (u.origin !== location.origin) return 'external';
        if (/^\/material\/html\/js\/[^/]+\.js$/i.test(u.pathname)) return 'material-js';
        if (/^\/material\/html\/lib\/[^/]+\.js$/i.test(u.pathname)) return 'library-js';
        if (/\.css$/i.test(u.pathname)) return 'css';
        if (/^\/jsonrpc\.js$/i.test(u.pathname)) return 'lms-jsonrpc';
        if (/^\/cometd/i.test(u.pathname)) return 'lms-cometd';
        if (/^\/(music|imageproxy|contributor)\//i.test(u.pathname)) return 'artwork';
        if (/\.(png|jpe?g|webp|gif|svg)$/i.test(u.pathname)) return 'image';
        return 'other';
      } catch (_) { return 'other'; }
    })(),
    safeName: (() => {
      try {
        const u = new URL(r.name, location.href);
        if (u.origin !== location.origin) return 'external';
        // Only expose a static asset filename; never return a query, artwork ID,
        // user path, server hostname, player name, or response body.
        const match = u.pathname.match(/^\/material\/html\/(?:js|lib)\/[a-z0-9._-]+\.(?:js|css)$/i);
        return match ? match[0] : '';
      } catch (_) { return ''; }
    })()
  }));
  return {
    timeOrigin: performance.timeOrigin,
    marks: {appReady: timings.appReady, npVisible: timings.npVisible,
            trackVisible: timings.trackVisible, coverVisible: timings.coverVisible},
    fcp: timings.fcp, lcp: timings.lcp,
    longTasks: timings.longTasks || [],
    nav: nav && {
      responseStart: nav.responseStart, responseEnd: nav.responseEnd,
      domContentLoaded: nav.domContentLoadedEventEnd, loadEnd: nav.loadEventEnd,
      transferSize: nav.transferSize, encodedBodySize: nav.encodedBodySize,
      redirectCount: nav.redirectCount
    },
    resources
  };
}"""


def server_origin(raw: str) -> str:
    parsed = urlsplit(raw)
    if (parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password
            or parsed.query or parsed.fragment or parsed.path not in ('', '/')):
        raise ValueError('Use only the server origin, e.g. http://192.168.1.10:9000 (no credentials, path or query).')
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError('Invalid port in server URL.') from exc
    if port is not None and not 0 < port < 65536:
        raise ValueError('Port out of range.')
    return urlunsplit((parsed.scheme, parsed.netloc, '', '', ''))


def view_url(origin: str, view: str, player: str | None) -> str:
    path = '/material/' if view == 'normal' else '/material/np-only'
    return origin + path + ('?' + urlencode({'player': player}) if player else '')


def resource_summary(resources: list[dict]) -> dict:
    groups: dict[str, dict] = {}
    for item in resources:
        group = item.get('pathClass', 'other')
        if group not in groups:
            groups[group] = {'count': 0, 'duration_ms_total': 0.0, 'transfer_kb': 0.0}
        groups[group]['count'] += 1
        groups[group]['duration_ms_total'] += max(0, item.get('duration') or 0)
        groups[group]['transfer_kb'] += max(0, item.get('transferSize') or 0) / 1024
    return {key: {'count': val['count'],
                  'duration_ms_total': round(val['duration_ms_total'], 1),
                  'transfer_kb': round(val['transfer_kb'], 1)} for key, val in sorted(groups.items())}


def slowest_resources(resources: list[dict], limit: int = 15) -> list[dict]:
    ranked = sorted(resources, key=lambda item: item.get('duration') or 0, reverse=True)
    return [{'asset': r.get('safeName') or r.get('pathClass') or 'other',
             'duration_ms': safe_milliseconds(r.get('duration')),
             'transfer_kb': safe_milliseconds((r.get('transferSize') or 0) / 1024)}
            for r in ranked[:limit]]


def safe_milliseconds(value) -> float | None:
    return round(value, 1) if isinstance(value, (int, float)) and value >= 0 else None


def percentile(values: list[float], percentage: float) -> float | None:
    if not values:
        return None
    values = sorted(values)
    rank = (len(values) - 1) * percentage
    lo, hi = int(rank), min(int(rank) + 1, len(values) - 1)
    return round(values[lo] + (values[hi] - values[lo]) * (rank - lo), 1)


def report_summary(rows: list[dict]) -> dict:
    result: dict = {}
    for view in ('normal', 'np-only'):
        for mode in ('cold', 'warm'):
            items = [r for r in rows if r['view'] == view and r['mode'] == mode]
            if not items:
                continue
            def median(field):
                values = [r[field] for r in items if r.get(field) is not None]
                return safe_milliseconds(statistics.median(values)) if values else None
            app = [r['app_ready_total_ms'] for r in items if r['app_ready_total_ms'] is not None]
            result[f'{view}/{mode}'] = {
                'samples': len(items), 'app_ready_samples': len(app),
                'app_ready_median_ms': median('app_ready_total_ms'),
                'app_ready_p90_ms': percentile(app, .9),
                'np_visible_median_ms': median('np_visible_total_ms'),
                'track_visible_median_ms': median('track_visible_total_ms'),
                'cover_visible_median_ms': median('cover_visible_total_ms'),
                'ttfb_median_ms': median('ttfb_ms'),
                'dom_content_loaded_median_ms': median('dom_content_loaded_ms'),
                'fcp_median_ms': median('fcp_ms'),
                'lcp_median_ms': median('lcp_ms'),
                'longtask_total_median_ms': median('longtask_total_ms'),
                'request_count_median': median('request_count'),
                'error_count': sum(bool(r['error']) for r in items),
            }
    return result


async def detect_player(api, origin: str) -> str:
    """Query players without modifying playback state. Never report player IDs/names."""
    try:
        response = await api.post(origin + '/jsonrpc.js',
                                  data={'id': 1, 'method': 'slim.request',
                                        'params': ['', ['players', '0', '100']]}, timeout=10000)
        if not response.ok:
            raise ValueError(f'LMS JSON-RPC returned HTTP {response.status}')
        result = (await response.json()).get('result') or {}
        players = result.get('players_loop') or []
        player = next((p.get('playerid') for p in players if p.get('playerid')), None)
        if not player:
            raise ValueError('No available Lyrion player. Supply --player <name-or-id>.')
        return player
    except PlaywrightError as exc:
        raise ValueError('Could not retrieve players from LMS JSON-RPC; check server URL and LAN access.') from exc


async def start_cdp_trace(context, page):
    session = await context.new_cdp_session(page)
    finished = asyncio.get_running_loop().create_future()
    session.on('Tracing.tracingComplete', lambda data: finished.set_result(data) if not finished.done() else None)
    await session.send('Tracing.start', {
        'categories': '-*,devtools.timeline,blink.user_timing,loading,disabled-by-default-devtools.timeline',
        'transferMode': 'ReturnAsStream', 'options': 'sampling-frequency=10000',
    })
    return session, finished


async def stop_cdp_trace(session, completed, dest: Path):
    await session.send('Tracing.end')
    event = await asyncio.wait_for(completed, timeout=30)
    handle = event.get('stream')
    if not handle:
        raise RuntimeError('CDP did not provide a trace stream')
    try:
        with dest.open('wb') as out:
            while True:
                chunk = await session.send('IO.read', {'handle': handle})
                if chunk.get('base64Encoded'):
                    import base64
                    out.write(base64.b64decode(chunk['data']))
                else:
                    out.write(chunk.get('data', '').encode('utf-8'))
                if chunk.get('eof'):
                    break
    finally:
        await session.send('IO.close', {'handle': handle})
        await session.detach()


async def measure(page, origin: str, view: str, mode: str, player: str | None,
                  timeout_ms: int, observe_ms: int, artifacts: Path,
                  capture_screenshot: bool, capture_cdp: bool) -> dict:
    event = {'failed_requests': 0, 'http_errors': 0, 'script_errors': 0, 'requests': 0}
    def request_failed(_):
        event['failed_requests'] += 1
    def response_received(resp):
        event['requests'] += 1
        if resp.status >= 400:
            event['http_errors'] += 1
    def page_error(_):
        event['script_errors'] += 1
    page.on('requestfailed', request_failed)
    page.on('response', response_received)
    page.on('pageerror', page_error)
    error = None
    probe = {}
    started_epoch = time.time() * 1000
    started_mono = time.perf_counter()
    trace = None
    if capture_cdp:
        trace = await start_cdp_trace(page.context, page)
    try:
        await page.goto(view_url(origin, view, player), wait_until='domcontentloaded', timeout=timeout_ms)
        if view == 'np-only':
            # np-only is a JS redirect, not a separate frontend. Measure both hops.
            await page.wait_for_url(re.compile(r'/material/\?(?=.*\bmsk--np-only\b).*'), timeout=timeout_ms)
        await page.wait_for_function('window.__materialBenchmark && window.__materialBenchmark.appReady !== null',
                                     timeout=timeout_ms, polling=100)
        await page.wait_for_timeout(observe_ms)
        probe = await page.evaluate(JS_RESULTS)
        if capture_screenshot:
            await page.screenshot(path=str(artifacts / f'{view}-{mode}.png'), full_page=False)
    except (PlaywrightError, asyncio.TimeoutError) as exc:
        # Do not store raw exception messages: they might contain a player ID or server URL.
        error = type(exc).__name__
        try:
            probe = await page.evaluate(JS_RESULTS)
        except PlaywrightError:
            pass
    finally:
        if trace is not None:
            try:
                await stop_cdp_trace(trace[0], trace[1], artifacts / f'{view}-{mode}-cdp.json')
            except Exception:
                event['trace_failed'] = True
        page.remove_listener('requestfailed', request_failed)
        page.remove_listener('response', response_received)
        page.remove_listener('pageerror', page_error)

    marks = probe.get('marks') or {}
    nav = probe.get('nav') or {}
    time_origin = probe.get('timeOrigin')
    def total(marker):
        val = marks.get(marker)
        if val is None or time_origin is None:
            return None
        return safe_milliseconds(time_origin + val - started_epoch)
    long_tasks = probe.get('longTasks') or []
    resources = probe.get('resources') or []
    return {
        'view': view, 'mode': mode, 'error': error,
        'elapsed_ms': safe_milliseconds((time.perf_counter() - started_mono) * 1000),
        'app_ready_total_ms': total('appReady'),
        'np_visible_total_ms': total('npVisible'),
        'track_visible_total_ms': total('trackVisible'),
        'cover_visible_total_ms': total('coverVisible'),
        # Below metrics are relative to the final document navigation. A JS
        # redirect from /np-only is NOT included in these browser nav numbers.
        'ttfb_ms': safe_milliseconds(nav.get('responseStart')),
        'html_download_end_ms': safe_milliseconds(nav.get('responseEnd')),
        'dom_content_loaded_ms': safe_milliseconds(nav.get('domContentLoaded')),
        'load_end_ms': safe_milliseconds(nav.get('loadEnd')),
        'fcp_ms': safe_milliseconds(probe.get('fcp')),
        'lcp_ms': safe_milliseconds(probe.get('lcp')),
        'longtask_count': len(long_tasks),
        'longtask_total_ms': safe_milliseconds(sum(long_tasks)),
        'longtask_max_ms': safe_milliseconds(max(long_tasks, default=0)),
        'request_count': event['requests'], 'http_errors': event['http_errors'],
        'failed_requests': event['failed_requests'], 'script_errors': event['script_errors'],
        'resource_groups': resource_summary(resources),
        'slowest_resources': slowest_resources(resources),
        'trace_failed': event.get('trace_failed', False),
    }


def write_reports(out: Path, rows: list[dict], config: dict) -> dict:
    summary = report_summary(rows)
    document = {
        'description': 'LMS Material Skin startup benchmark (no server URL, player ID, titles or request payloads)',
        'generated_at_utc': datetime.now(timezone.utc).isoformat(),
        'config': config, 'summary': summary, 'runs': rows,
    }
    (out / 'results.json').write_text(json.dumps(document, indent=2) + '\n', encoding='utf-8')
    fields = [k for k in rows[0] if k not in ('resource_groups', 'slowest_resources')] if rows else []
    with (out / 'runs.csv').open('w', newline='', encoding='utf-8') as fd:
        if rows:
            writer = csv.DictWriter(fd, fieldnames=fields)
            writer.writeheader()
            writer.writerows({key: run.get(key) for key in fields} for run in rows)
    lines = ['# Material Skin startup benchmark', '',
             'Times in milliseconds. App-ready: Vue root visible, v-cloak removed.',
             'Track/cover markers are optional; they may remain blank for an idle player.',
             'TTFB/FCP/LCP are measured from the final document navigation and exclude the /np-only JavaScript redirect.',
             'App-ready total includes the redirect and is measured against the initial navigation.', '',
             '| View | Cache | Successful | Median app-ready | P90 app-ready | Errors |',
             '| --- | --- | ---: | ---: | ---: | ---: |']
    for key, stat in summary.items():
        view, cache = key.split('/')
        median = f"{stat['app_ready_median_ms']:.1f} ms" if stat['app_ready_median_ms'] is not None else 'n/a'
        p90 = f"{stat['app_ready_p90_ms']:.1f} ms" if stat['app_ready_p90_ms'] is not None else 'n/a'
        lines.append(f"| {view} | {cache} | {stat['app_ready_samples']}/{stat['samples']} | {median} | {p90} | {stat['error_count']} |")
    (out / 'summary.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')
    return summary


def args_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-url', required=True, help='Server origin only, e.g. http://192.168.1.10:9000')
    parser.add_argument('--player', help='LMS player name or ID; default: auto-detect first player via read-only JSON-RPC')
    parser.add_argument('--view', choices=('both', 'normal', 'np-only'), default='both')
    parser.add_argument('--runs', type=int, default=5, help='Cold+warm pairs per view (default 5)')
    parser.add_argument('--timeout-ms', type=int, default=30000, help='Per navigation/readiness timeout')
    parser.add_argument('--observe-ms', type=int, default=2000, help='Extra observation after app becomes visible')
    parser.add_argument('--out', type=Path, help='Output directory; default: performance/results/<timestamp>')
    parser.add_argument('--headed', action='store_true', help='Show Chromium windows (closest to interactive use)')
    parser.add_argument('--chromium-path', help='Use a locally installed Chromium binary instead of Playwright-managed Chromium')
    parser.add_argument('--screenshots', action='store_true', help='Save first cold screenshots (may contain music details)')
    parser.add_argument('--cdp-trace', action='store_true', help='Save raw DevTools CPU/network traces for first cold run (SENSITIVE)')
    parser.add_argument('--har', action='store_true', help='Save raw Network HAR per cold/warm pair (SENSITIVE)')
    return parser


async def run_benchmark(args) -> int:
    try:
        origin = server_origin(args.base_url)
    except ValueError as exc:
        print(f'Invalid --base-url: {exc}', file=sys.stderr)
        return 2
    if args.runs < 1 or args.runs > 100 or args.timeout_ms < 1000 or args.observe_ms < 0:
        print('--runs must be 1..100, --timeout-ms >= 1000, and --observe-ms >= 0', file=sys.stderr)
        return 2
    out = args.out or (Path(__file__).resolve().parent / 'results' / datetime.now().strftime('%Y%m%d-%H%M%S'))
    out.mkdir(parents=True, exist_ok=True)
    views = ['normal', 'np-only'] if args.view == 'both' else [args.view]
    rows = []
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=not args.headed, executable_path=args.chromium_path)
        try:
            player = args.player
            if not player:
                api = await pw.request.new_context()
                try:
                    try:
                        player = await detect_player(api, origin)
                    except (ValueError, PlaywrightError) as exc:
                        if 'np-only' in views:
                            print(f'Player auto-detection failed: {exc}', file=sys.stderr)
                            return 2
                        player = None
                finally:
                    await api.dispose()
            print(f'Benchmark: {len(views)} view(s) x {args.runs} cold/warm pairs. Player: {"selected" if player else "none"}.')
            for view in views:
                for number in range(1, args.runs + 1):
                    # An isolated context guarantees a fresh HTTP cache and browser storage.
                    context_args = {'viewport': {'width': 1440, 'height': 900},
                                    'device_scale_factor': 1, 'service_workers': 'block'}
                    if args.har:
                        context_args.update({'record_har_path': str(out / f'{view}-{number:02d}.har'),
                                             'record_har_mode': 'minimal',
                                             'record_har_content': 'omit'})
                    context = await browser.new_context(**context_args)
                    await context.add_init_script(script=PROBE_SCRIPT)
                    page = await context.new_page()
                    try:
                        for mode in ('cold', 'warm'):
                            name = f'{view}-{number:02d}-{mode}'
                            print(f'  {name}...', flush=True)
                            result = await measure(page, origin, view, mode, player,
                                                   args.timeout_ms, args.observe_ms,
                                                   out, args.screenshots and number == 1 and mode == 'cold',
                                                   args.cdp_trace and number == 1 and mode == 'cold')
                            result['run'] = number
                            rows.append(result)
                            print(f'    app ready: {result["app_ready_total_ms"]} ms, '
                                  f'TTFB: {result["ttfb_ms"]} ms, error: {result["error"]}')
                    finally:
                        await context.close()
        finally:
            await browser.close()
    config = {'view': args.view, 'runs': args.runs, 'timeout_ms': args.timeout_ms,
              'observe_ms': args.observe_ms, 'headed': args.headed,
              'player_selected': bool(player), 'screenshots': args.screenshots,
              'cdp_trace': args.cdp_trace, 'har': args.har}
    summary = write_reports(out, rows, config)
    print('\nSummary (median app-ready total):')
    for name, values in summary.items():
        print(f'  {name:15} {values["app_ready_median_ms"]} ms '
              f'({values["app_ready_samples"]}/{values["samples"]} valid)')
    print(f'Results: {out.resolve()}')
    return 0 if rows and all(r['app_ready_total_ms'] is not None for r in rows) else 1


def main() -> int:
    try:
        return asyncio.run(run_benchmark(args_parser().parse_args()))
    except KeyboardInterrupt:
        return 130
    except PlaywrightError as exc:
        print(f'Browser error ({type(exc).__name__}). Run: python -m playwright install chromium', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
