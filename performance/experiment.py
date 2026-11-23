#!/usr/bin/env python3
"""Client-side A/B experiment for Lyrion Material Skin initial loading.

Uses a temporary in-browser HTML response rewrite, without editing LMS or your
actual Material Skin installation. Requests are never sent to a third party.
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

from playwright.async_api import async_playwright

from benchmark import PROBE_SCRIPT, detect_player, measure, percentile, server_origin

# Corresponds to the five choices in the user's Material Skin home settings.
# 'Explore' controls placement, not a separate home-extra query.
HOME_ITEMS = ['std_new', 'std_random', 'std_playlists', 'std_radios', 'std_explore']
HOME_SCRIPT = r'''(() => {
  const val = {firstHeader: null};
  Object.defineProperty(window, '__homeExperiment', {value: val});
  let queued = false;
  function scan() {
    queued = false;
    const headers = document.querySelectorAll('#browse-list .grid-header');
    if (val.firstHeader === null) {
      for (const node of headers) {
        if (node.getClientRects().length && getComputedStyle(node).visibility !== 'hidden') {
          val.firstHeader = performance.now();
          break;
        }
      }
    }
  }
  function schedule() {
    if (queued) return;
    queued = true;
    requestAnimationFrame(scan);
  }
  new MutationObserver(schedule).observe(document, {subtree:true, childList:true, attributes:true});
  document.addEventListener('DOMContentLoaded', schedule, {once:true});
  schedule();
})()'''

# Includes the cache-buster already present in the installed plugin's script tag;
# never hard-codes a plugin version. Mirrors the single-file source experiment.
NOWPLAYING_SCRIPT = re.compile(
    r'(<script\s+src=["\']html/js/nowplaying-page\.js\?r=([^"\']+)["\']\s*></script>)',
    flags=re.IGNORECASE,
)
DEFERRED_ENTRY = re.compile(r'(["\'])nowplaying-functions\1\s*,\s*', re.IGNORECASE)


def make_early_html(html: str) -> str:
    """Promote only nowplaying-functions.js, preserving all other deferred loads."""
    if 'nowplaying-functions.js?r=' in html:
        raise ValueError('The LMS already loads nowplaying-functions.js early; compare against unmodified upstream.')
    if len(NOWPLAYING_SCRIPT.findall(html)) != 1:
        raise ValueError('Expected exactly one nowplaying-page.js script in the served HTML.')
    if len(DEFERRED_ENTRY.findall(html)) != 1:
        raise ValueError('Expected exactly one deferred nowplaying-functions list entry.')
    html = DEFERRED_ENTRY.sub('', html, count=1)
    def insert(match: re.Match) -> str:
        return match.group(1) + '\n  <script src="html/js/nowplaying-functions.js?r=' + match.group(2) + '"></script>'
    result = NOWPLAYING_SCRIPT.sub(insert, html, count=1)
    if result.count('nowplaying-functions.js?r=') != 1 or '"nowplaying-functions"' in result:
        raise ValueError('Experimental rewrite did not produce a single early script.')
    return result


def home_storage_script(items: list[str]) -> str:
    # Before Material scripts read preferences, configure only the isolated
    # benchmark context. Never persists to the user's normal browser profile.
    return (
        '(() => { if (location.pathname.startsWith("/material/")) {'
        'localStorage.setItem("lms-material::detailedHomeItems", '
        + json.dumps(json.dumps(items)) + ');'
        'localStorage.setItem("lms-material::top-grid", "true");'
        'localStorage.setItem("lms-material::gridPerView", "true");'
        '} })();'
    )


def scenarios(mode: str) -> list[tuple[str, str, bool, list[str]]]:
    result = []
    if mode in ('all', 'now-playing'):
        result.extend([
            ('np-original', 'np-only', False, HOME_ITEMS),
            ('np-early', 'np-only', True, HOME_ITEMS),
        ])
    if mode in ('all', 'home'):
        result.extend([
            ('home-scrollable-on', 'normal', False, HOME_ITEMS),
            ('home-scrollable-off', 'normal', False, []),
        ])
    return result


def summarize(rows: list[dict]) -> dict:
    output = {}
    for label in sorted({r['scenario'] for r in rows}):
        for mode in ('cold', 'warm'):
            matches = [r for r in rows if r['scenario'] == label and r['mode'] == mode]
            def median(key):
                vals = [r[key] for r in matches if isinstance(r.get(key), (float, int))]
                return round(statistics.median(vals), 1) if vals else None
            output[label + '/' + mode] = {
                'samples': len(matches),
                'app_ready_ms': median('app_ready_total_ms'),
                'first_status_ms': median('first_player_status_total_ms'),
                'title_visible_ms': median('track_visible_total_ms'),
                'deferred_ready_ms': median('deferred_function_ready_total_ms'),
                'home_request_start_ms': median('home_extra_start_ms'),
                'home_request_finished_ms': median('home_extra_finished_ms'),
                'first_home_header_ms': median('first_home_header_ms'),
                'lcp_ms': median('lcp_ms'),
                'request_count': median('request_count'),
                'home_request_runs': sum(r['home_extra_count'] > 0 for r in matches),
                'status_before_js_runs': sum(r['status_before_deferred_count'] > 0 for r in matches),
                'errors': sum(bool(r.get('error')) for r in matches),
            }
    return output


def write_reports(out: Path, rows: list[dict], config: dict):
    summary = summarize(rows)
    data = {'description': 'Read-only local A/B browser experiment (sanitized output)',
            'generated_at_utc': datetime.now(timezone.utc).isoformat(),
            'config': config, 'summary': summary, 'runs': rows}
    (out / 'experiment-results.json').write_text(json.dumps(data, indent=2) + '\n', encoding='utf-8')
    fields = [key for key in rows[0] if key not in ('slowest_resources', 'resource_groups', 'request_timeline')] if rows else []
    with (out / 'experiment-runs.csv').open('w', newline='', encoding='utf-8') as stream:
        writer = csv.DictWriter(stream, fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key) for key in fields})
    def ms(value):
        return f'{value:.1f}' if value is not None else 'n/a'
    lines = ['# Material Skin A/B startup experiment', '',
             'Browser-side A/B test against the **same unchanged LMS server**.',
             'All values in ms (median of successful samples). Cold and warm are separate.',
             'Early = serve nowplaying-functions.js before main.js in the benchmark browser only.',
             'Home ON = New Music, Random Releases, Playlists, Radios, Explore (grid view).',
             'Home OFF = no scrollable home lists (grid view).',
             'First-home-header measures first visible `.grid-header` DOM element and may be n/a.',
             'Home request completed and first visible header are independent timing markers.',
             '',
             '| Scenario / cache | App ready | First status | Title visible | JS ready | Home-extra finished | First home header | Errors |',
             '| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |']
    for name, x in summary.items():
        lines.append(f'| {name} | {ms(x["app_ready_ms"])} | {ms(x["first_status_ms"])} | '
                     f'{ms(x["title_visible_ms"])} | {ms(x["deferred_ready_ms"])} | '
                     f'{ms(x["home_request_finished_ms"])} | {ms(x["first_home_header_ms"])} | {x["errors"]}/{x["samples"]} |')
    lines.extend(['', 'Do not sum request durations: concurrent requests overlap.',
                  'No LAN addresses, names, track contents, headers, cookies or JSON-RPC payloads are exported.'])
    (out / 'experiment-summary.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')
    return summary


async def run_experiment(args) -> int:
    origin = server_origin(args.base_url)
    if not 1 <= args.runs <= 100 or args.observe_ms < 0 or args.timeout_ms < 1000:
        raise ValueError('--runs must be 1..100; --observe-ms >= 0; --timeout-ms >= 1000')
    out = args.out or Path(__file__).resolve().parent / 'results' / datetime.now().strftime('%Y%m%d-%H%M%S-experiment')
    out.mkdir(parents=True, exist_ok=True)
    rows = []
    cases = scenarios(args.mode)
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=not args.headed)
        try:
            player = args.player
            if player is None:
                api = await pw.request.new_context()
                try:
                    player = await detect_player(api, origin)
                finally:
                    await api.dispose()
            print(f'Running {len(cases)} scenarios x {args.runs} cold/warm pairs; player selected: {bool(player)}')
            for number in range(1, args.runs + 1):
                # Rotate scenario order so background server work does not always
                # favor the same variant. Cold and warm remain paired in context.
                order = cases[number % len(cases):] + cases[:number % len(cases)]
                for label, view, early, home_items in order:
                    ctx = await browser.new_context(viewport={'width': 1440, 'height': 900},
                                                    device_scale_factor=1, service_workers='block')
                    await ctx.add_init_script(script=home_storage_script(home_items))
                    await ctx.add_init_script(script=PROBE_SCRIPT)
                    await ctx.add_init_script(script=HOME_SCRIPT)
                    rewrite_count = [0]
                    if early:
                        async def rewrite(route):
                            reply = await route.fetch()
                            html = await reply.text()
                            try:
                                patched = make_early_html(html)
                            except ValueError as exc:
                                raise RuntimeError(f'Incompatible Material HTML: {exc}') from exc
                            rewrite_count[0] += 1
                            await route.fulfill(response=reply, body=patched)
                        await ctx.route(re.compile(r'/material/(?:index\.html)?(?:\?|$)'), rewrite)
                    page = await ctx.new_page()
                    try:
                        for mode in ('cold', 'warm'):
                            rewrites_before = rewrite_count[0]
                            clock_start = time.perf_counter()
                            started_epoch = time.time() * 1000
                            seen = set()
                            home_start, home_finished = [], []
                            def is_home(request):
                                if '/jsonrpc.js' not in request.url.split('?')[0]:
                                    return False
                                # Payload is inspected only in volatile memory; it
                                # is never printed, logged or written to results.
                                data = request.post_data or ''
                                return 'home-extra' in data and 'material-skin' in data
                            def on_request(req):
                                if is_home(req):
                                    seen.add(id(req))
                                    home_start.append((time.perf_counter() - clock_start) * 1000)
                            def on_finished(req):
                                if id(req) in seen:
                                    home_finished.append((time.perf_counter() - clock_start) * 1000)
                            page.on('request', on_request)
                            page.on('requestfinished', on_finished)
                            try:
                                row = await measure(page, origin, view, mode, player,
                                                    args.timeout_ms, args.observe_ms,
                                                    out, False, False)
                                hp = await page.evaluate('''() => ({
                                    firstHeader: window.__homeExperiment?.firstHeader ?? null,
                                    timeOrigin: performance.timeOrigin,
                                    headersVisible: [...document.querySelectorAll('#browse-list .grid-header')]
                                      .filter(x => x.getClientRects().length > 0).length
                                })''')
                                first_header = None
                                if hp['firstHeader'] is not None:
                                    # Navigation used the same wall-clock reference
                                    # as app_ready_total_ms, so include redirect.
                                    # Time-base conversion errors are < a few ms.
                                    first_header = round(hp['timeOrigin'] + hp['firstHeader'] - started_epoch, 1)
                            finally:
                                page.remove_listener('request', on_request)
                                page.remove_listener('requestfinished', on_finished)
                            row.update({'scenario': label, 'run': number,
                                        'home_extra_count': len(home_start),
                                        'home_extra_start_ms': round(home_start[0], 1) if home_start else None,
                                        'home_extra_finished_ms': round(home_finished[0], 1) if home_finished else None,
                                        'first_home_header_ms': first_header,
                                        'home_headers_visible': hp['headersVisible'],
                                        'rewrite_applied': (not early or rewrite_count[0] > rewrites_before)})
                            if early and rewrite_count[0] <= rewrites_before and row['error'] is None:
                                row['error'] = 'EarlyHTMLRewriteNotApplied'
                            rows.append(row)
                            print(f'  {number}/{args.runs} {label:20s} {mode:4s}  '
                                  f'app={row["app_ready_total_ms"]}ms  '
                                  f'title={row["track_visible_total_ms"]}ms  '
                                  f'home={row["home_extra_finished_ms"]}ms')
                    finally:
                        await ctx.close()
        finally:
            await browser.close()
    summary = write_reports(out, rows, {'mode':args.mode, 'runs':args.runs,
                                        'headed':args.headed,'observe_ms':args.observe_ms})
    print(f'Completed: {out.resolve()}')
    return 0 if rows and all(r['error'] is None for r in rows) else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-url', required=True)
    parser.add_argument('--player', help='Player name/ID; otherwise first connected player')
    parser.add_argument('--mode', choices=['all','now-playing','home'], default='all')
    parser.add_argument('--runs', type=int, default=5)
    parser.add_argument('--headed', action='store_true')
    parser.add_argument('--timeout-ms', type=int, default=30000)
    parser.add_argument('--observe-ms', type=int, default=5000)
    parser.add_argument('--out', type=Path)
    args = parser.parse_args()
    try:
        return asyncio.run(run_experiment(args))
    except ValueError as exc:
        print(f'Invalid input/experiment: {exc}', file=sys.stderr)
        return 2

if __name__ == '__main__':
    raise SystemExit(main())