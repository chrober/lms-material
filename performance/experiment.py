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
from urllib.parse import urlsplit

from playwright.async_api import async_playwright, Error as PlaywrightError

from benchmark import PROBE_SCRIPT, detect_player, measure, percentile, server_origin, view_url

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

# Material can render script tags with varying paths, query strings, attribute
# orders, whitespace, or bundles depending on the installed plugin version.
# Inspect actual script tags instead of assuming one exact output format.
SCRIPT_TAG = re.compile(
    r'<script\b[^>]*\bsrc\s*=\s*(["\'])([^"\']+)\1[^>]*>\s*</script\s*>',
    flags=re.IGNORECASE | re.DOTALL,
)
DEFERRED_ENTRY = re.compile(r'(["\'])nowplaying-functions\1', flags=re.IGNORECASE)


# Diagnostic summaries intentionally contain no document text, URLs, query
# strings, HTML attributes, cookies, or network payloads.
SCRIPT_OPEN = re.compile(
    r"<script\b[^>]*\bsrc\s*=\s*(['\"])([^'\"]+)\1",
    flags=re.IGNORECASE | re.DOTALL,
)


def safe_script_basename(src: str) -> str:
    """Return a bounded filename, never a host/path/query or a private token."""
    name = urlsplit(src.replace('&amp;', '&')).path.rsplit('/', 1)[-1]
    return name if re.fullmatch(r'[A-Za-z0-9._-]{1,72}', name) else '[nonstandard]'


def safe_document_path(url: str) -> str:
    """Only expose ordinary Material paths, never query strings or hosts."""
    path = urlsplit(url).path
    return path if re.fullmatch(r'/material/[A-Za-z0-9_./-]{0,80}', path) else '[other]'


def html_profile(html: str) -> dict:
    """Identify bootstrap style without exporting potentially private HTML."""
    names = [safe_script_basename(m.group(2)) for m in SCRIPT_OPEN.finditer(html)]
    return {
        'scripts': len(names),
        'script_files': names[:30],
        'deferred_entry': len(DEFERRED_ENTRY.findall(html)),
        'has_app_root': bool(re.search(r"<[^>]+\bid\s*=\s*['\"]app['\"]", html, re.I)),
        'has_deferred_loader': 'addJsToDocument' in html,
        'has_material_revision': 'LMS_MATERIAL_REVISION' in html,
    }


def format_html_profile(label: str, url: str, status: int | None, html: str) -> str:
    info = html_profile(html)
    return (f'{label}: path={safe_document_path(url)}, '
            f'status={status if status is not None else "n/a"}, '
            f'scripts={info["scripts"]}, files={info["script_files"]}, '
            f'deferred-entry={info["deferred_entry"]}, '
            f'app-root={info["has_app_root"]}, '
            f'deferred-loader={info["has_deferred_loader"]}, '
            f'revision-marker={info["has_material_revision"]}')


async def inspect_installed_html(args) -> int:
    """Inspect *actual* served document(s) using Chromium without rewrites.

    A plain HTTP GET can return a wrapper/launcher before a JavaScript redirect.
    Capture both the direct result and browser document responses to distinguish
    that from a genuinely bundled/different Material Skin release.
    """
    origin = server_origin(args.base_url)
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=not args.headed, executable_path=args.chromium_path)
        try:
            context = await browser.new_context(service_workers='block')
            try:
                target = view_url(origin, 'normal', args.player)
                direct = await context.request.get(target, timeout=args.timeout_ms)
                print(format_html_profile('Direct HTTP response', direct.url,
                                          direct.status, await direct.text()))
                page = await context.new_page()
                documents = []
                js_assets = set()

                def on_response(response):
                    if response.request.resource_type == 'document':
                        documents.append(response)

                def on_request(request):
                    if request.resource_type == 'script':
                        js_assets.add(safe_script_basename(request.url))

                page.on('response', on_response)
                page.on('request', on_request)
                try:
                    await page.goto(target, wait_until='domcontentloaded',
                                    timeout=args.timeout_ms)
                    # Allow document redirects and deferred scripts to finish.
                    try:
                        await page.wait_for_selector('#app', state='attached',
                                                     timeout=min(4000, args.timeout_ms))
                    except PlaywrightError:
                        pass
                    await page.wait_for_timeout(1200)
                    for index, response in enumerate(documents[:6], start=1):
                        try:
                            body = await response.text()
                            print(format_html_profile(f'Browser document {index}',
                                                      response.url, response.status, body))
                        except PlaywrightError:
                            print(f'Browser document {index}: body unavailable')
                    print(format_html_profile('Final browser DOM', page.url,
                                              None, await page.content()))
                    print('Script requests (filenames only): '
                          + str(sorted(js_assets)[:50]))
                    try:
                        state = await page.evaluate("""() => ({
                          appPresent: !!document.querySelector('#app'),
                          appVisible: !!document.querySelector('#app:not([v-cloak])'),
                          nowplayingFunction: typeof window.nowplayingOnPlayerStatus === 'function',
                          hasBus: typeof window.bus !== 'undefined'
                        })""")
                        print('Frontend markers: '+str(state))
                    except PlaywrightError:
                        print('Frontend markers: unavailable')
                finally:
                    await page.close()
            finally:
                await context.close()
        finally:
            await browser.close()
    print('Diagnostic finished. No HTML, URLs, credentials, or personal media '
          'data were saved. Share the printed diagnostic lines.')
    return 0



def html_shape(html: str) -> str:
    """Non-sensitive signature of the response. Never includes URLs or HTML."""
    scripts = [m.group(2).split('?', 1)[0].split('#', 1)[0].lower()
               for m in SCRIPT_TAG.finditer(html)]
    return (f'nowplaying-page={sum(x.endswith("/nowplaying-page.js") for x in scripts)}, '
            f'main={sum(x.endswith("/main.js") for x in scripts)}, '
            f'deferred-entry={len(DEFERRED_ENTRY.findall(html))}, '
            f'scripts={len(scripts)}, has-head={bool(re.search(r"</head\s*>", html, re.I))}')


def _remove_deferred_entry(html: str) -> str:
    entries = list(DEFERRED_ENTRY.finditer(html))
    if len(entries) != 1:
        raise ValueError(f'Expected one deferred Now Playing list entry; {html_shape(html)}')
    entry = entries[0]
    before, after = html[:entry.start()], html[entry.end():]
    following_comma = re.match(r'\s*,', after)
    if following_comma:
        return before.rstrip() + ' ' + after[following_comma.end():].lstrip()
    preceding_comma = re.search(r',\s*$', before)
    if preceding_comma:
        return before[:preceding_comma.start()] + after
    raise ValueError(f'Deferred entry is not part of a comma-separated list; {html_shape(html)}')


def _script_filename(src: str) -> str:
    return src.split('?', 1)[0].split('#', 1)[0].rsplit('/', 1)[-1].lower()


def make_early_html(html: str) -> str:
    """Move a deferred JS file to the synchronous bootstrap, without duplicating it.

    The script's position is selected from the HTML actually served by LMS.
    If no original page script tag is present, insert before main.js; if that
    too is bundled, use </head> only when a deferred list is recognizable.
    """
    tags = list(SCRIPT_TAG.finditer(html))
    if any(_script_filename(m.group(2)) == 'nowplaying-functions.js' for m in tags):
        raise ValueError('Server already loads Now Playing functions early; no valid A/B comparison.')
    deferred = list(DEFERRED_ENTRY.finditer(html))
    if len(deferred) != 1:
        raise ValueError(f'Cannot identify the deferred Now Playing entry; {html_shape(html)}')

    page = [m for m in tags if _script_filename(m.group(2)) == 'nowplaying-page.js']
    mains = [m for m in tags if _script_filename(m.group(2)) == 'main.js']
    if len(page) > 1 or len(mains) > 1:
        raise ValueError(f'Ambiguous bootstrap scripts; {html_shape(html)}')
    anchor = page[0] if page else (mains[0] if mains else None)
    if anchor:
        src = anchor.group(2)
        src = re.sub(r'(?:nowplaying-page|main)\.js(?=\?|#|$)', 'nowplaying-functions.js', src, flags=re.I)
        insert_at = anchor.end() if page else anchor.start()
    else:
        # Some Material releases bundle bootstrap JavaScript. A head insert is
        # safe when the app clearly has its deferred loader, but it is more
        # render-blocking; the A/B measurements must decide whether it helps.
        head_end = re.search(r'</head\s*>', html, flags=re.I)
        if not head_end or 'addJsToDocument' not in html:
            raise ValueError(f'Cannot find a safe early-script insertion point; {html_shape(html)}')
        insert_at = head_end.start()
        version = re.search(r'LMS_MATERIAL_REVISION\s*=\s*["\']([^"\']+)', html)
        src = 'html/js/nowplaying-functions.js'
        if version:
            from urllib.parse import quote
            src += '?r=' + quote(version.group(1), safe='')

    import html as html_lib
    new_tag = '\n  <script src="' + html_lib.escape(html_lib.unescape(src), quote=True) + '"></script>'
    result = html[:insert_at] + new_tag + html[insert_at:]
    result = _remove_deferred_entry(result)
    updated_tags = [m for m in SCRIPT_TAG.finditer(result)
                    if _script_filename(m.group(2)) == 'nowplaying-functions.js']
    if len(updated_tags) != 1 or DEFERRED_ENTRY.search(result):
        raise ValueError(f'Experimental rewrite did not yield one early script; {html_shape(result)}')
    return result


async def fulfill_early_html(route, rewrite_count: list[int], rewrite_errors: list[str]) -> None:
    """Handle HTML intercepts without raising inside Playwright's event loop."""
    try:
        reply = await route.fetch()
        response_body = await reply.text()
    except PlaywrightError:
        rewrite_errors.append('Could not fetch the original Material HTML response')
        await route.abort()
        return
    try:
        patched = make_early_html(response_body)
    except ValueError as exc:
        rewrite_errors.append(str(exc))
        # Fulfill the original HTML, then report failure outside the listener.
        # Never write server responses/URLs to the benchmark results.
        await route.fulfill(response=reply)
        return
    rewrite_count[0] += 1
    await route.fulfill(response=reply, body=patched)


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
        browser = await pw.chromium.launch(headless=not args.headed, executable_path=args.chromium_path)
        try:
            player = args.player
            if player is None:
                api = await pw.request.new_context()
                try:
                    player = await detect_player(api, origin)
                finally:
                    await api.dispose()
            # Fail fast if the installed Material version does not expose the
            # expected deferred loader. This GET is read-only and its response
            # is checked only in memory. It does not affect test browser cache.
            if any(early for _, _, early, _ in cases):
                validation = await pw.request.new_context()
                try:
                    try:
                        response = await validation.get(view_url(origin, 'normal', player),
                                                        timeout=args.timeout_ms)
                        if response.status >= 400:
                            raise ValueError(f'LMS returned HTTP {response.status} for the Material page')
                        make_early_html(await response.text())
                    except (ValueError, PlaywrightError) as exc:
                        detail = str(exc) if isinstance(exc, ValueError) else type(exc).__name__
                        print('Early-load HTML validation failed: ' + detail + '\n'
                              'The installed Material plugin may use a different script layout. '
                              'Use --mode home to measure scrollable lists separately.', file=sys.stderr)
                        return 2
                finally:
                    await validation.dispose()
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
                    rewrite_errors = []
                    if early:
                        async def rewrite(route):
                            await fulfill_early_html(route, rewrite_count, rewrite_errors)
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
                            if rewrite_errors:
                                print('Unsupported Material HTML for np-early: '
                                      + rewrite_errors[0] + '\n'
                                      'The installed plugin may emit a different script layout. '
                                      'No server files were changed.', file=sys.stderr)
                                return 2
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
    parser.add_argument('--inspect-html', action='store_true',
                        help='Show sanitized script filenames for actual Material pages and redirects, then exit')
    parser.add_argument('--player', help='Player name/ID; otherwise first connected player')
    parser.add_argument('--mode', choices=['all','now-playing','home'], default='all')
    parser.add_argument('--runs', type=int, default=5)
    parser.add_argument('--headed', action='store_true')
    parser.add_argument('--chromium-path', help='Optional local Chromium executable')
    parser.add_argument('--timeout-ms', type=int, default=30000)
    parser.add_argument('--observe-ms', type=int, default=5000)
    parser.add_argument('--out', type=Path)
    args = parser.parse_args()
    try:
        if args.inspect_html:
            return asyncio.run(inspect_installed_html(args))
        return asyncio.run(run_experiment(args))
    except ValueError as exc:
        print(f'Invalid input/experiment: {exc}', file=sys.stderr)
        return 2

if __name__ == '__main__':
    raise SystemExit(main())
