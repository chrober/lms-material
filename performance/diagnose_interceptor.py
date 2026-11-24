#!/usr/bin/env python3
"""Isolate HTML routing versus loader timing on a trusted local Lyrion server.

Only sanitized diagnostics are printed. No server changes and no captured HTML.
"""
from __future__ import annotations

import argparse
import asyncio
from collections import Counter
import re
import sys

from playwright.async_api import async_playwright, Error as PlaywrightError

from benchmark import PROBE_SCRIPT, detect_player, server_origin, view_url
from experiment import HOME_ITEMS, home_storage_script, make_early_html

CASES = ("baseline", "continue", "fulfill-original", "fulfill-text", "modified")
ROUTE_PATTERN = re.compile(r"/material/(?:index\.html)?(?:\?|$)")
FINAL_PATTERN = re.compile(r"/material/\?(?=.*\bmsk--np-only\b).*")
ERROR_CLASSES = ("ReferenceError", "TypeError", "SyntaxError", "SecurityError",
                 "NetworkError", "URIError", "EvalError", "RangeError")


def safe_error(message: str) -> str:
    """Print a useful error class but censor URLs, paths and quoted values."""
    name = next((kind for kind in ERROR_CLASSES if kind in message), "OtherError")
    text = re.sub(r"https?://\S+", "[url]", message, flags=re.I)
    text = re.sub(r"\b(?:\d{1,3}\.){3}\d{1,3}\b", "[ip]", text)
    text = re.sub(r"(['\"])(?:(?!\1).)*?\1", "[value]", text)
    text = re.sub(r"(?:[A-Za-z]:\\|/)[^\s,;()]+", "[path]", text)
    text = re.sub(r"[^\x20-\x7e]", "?", text.splitlines()[0])[:160]
    return name + ": " + text


def failed_key(request) -> str:
    m = re.search(r"net::ERR_[A-Z_]+", request.failure or "")
    kind = request.resource_type
    if kind not in ("document", "script", "stylesheet", "image", "font",
                    "xhr", "fetch", "other"):
        kind = "other"
    return kind + "/" + (m.group(0) if m else "unknown")


async def diagnose_case(browser, origin, player, name, timeout, observe):
    context = await browser.new_context(viewport={"width": 1440, "height": 900},
                                        device_scale_factor=1, service_workers="block")
    await context.add_init_script(script=home_storage_script(HOME_ITEMS))
    await context.add_init_script(script=PROBE_SCRIPT)
    routed = [0]
    route_errors = []
    upstream_status = []

    if name != "baseline":
        async def route_handler(route):
            routed[0] += 1
            try:
                if name == "continue":
                    await route.continue_()
                    return
                reply = await route.fetch()
                upstream_status.append(reply.status)
                if name == "fulfill-original":
                    await route.fulfill(response=reply)
                else:
                    html = await reply.text()
                    await route.fulfill(response=reply, body=(
                        make_early_html(html) if name == "modified" else html))
            except (PlaywrightError, ValueError) as exc:
                route_errors.append(safe_error(str(exc)))
                try:
                    await route.abort()
                except PlaywrightError:
                    pass
        await context.route(ROUTE_PATTERN, route_handler)

    page = await context.new_page()
    page_errors = []
    failed = Counter()
    responses = Counter()
    page.on("pageerror", lambda err: page_errors.append(safe_error(str(err))))
    page.on("requestfailed", lambda req: failed.update([failed_key(req)]))
    page.on("response", lambda resp: responses.update([resp.request.resource_type]))
    nav_error = None
    state = {}
    try:
        await page.goto(view_url(origin, "np-only", player),
                        wait_until="domcontentloaded", timeout=timeout)
        await page.wait_for_url(FINAL_PATTERN, timeout=timeout)
        await page.wait_for_timeout(observe)
        state = await page.evaluate("""() => ({
            vueMounted: !!document.querySelector('#app')?.__vue__,
            probeAppReady: window.__materialBenchmark?.appReady != null,
            rawTemplateVisible: !!document.body?.innerText?.includes('{{ snackbar.msg }}'),
            handlerAvailable: typeof window.nowplayingOnPlayerStatus === 'function',
            scriptTags: document.scripts.length
        })""")
    except PlaywrightError as exc:
        nav_error = type(exc).__name__
    finally:
        await context.close()
    return dict(case=name, intercepts=routed[0], statuses=upstream_status,
                navigation_error=nav_error, state=state, responses=dict(responses),
                failed=dict(failed.most_common(12)), page_errors=page_errors[:2],
                route_errors=route_errors[:2])


async def run(args) -> int:
    origin = server_origin(args.base_url)
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=not args.headed)
        try:
            player = args.player
            if not player:
                api = await pw.request.new_context()
                try:
                    player = await detect_player(api, origin)
                finally:
                    await api.dispose()
            if not player:
                raise ValueError("No player detected; provide --player")
            print("Comparing interception modes against the unchanged LMS.")
            for case in CASES:
                result = await diagnose_case(browser, origin, player, case,
                                             args.timeout_ms, args.observe_ms)
                print("\n=== " + case + " ===")
                for key in ("intercepts", "statuses", "navigation_error", "state",
                            "responses", "failed", "page_errors", "route_errors"):
                    print(key + ": " + repr(result[key]))
            print("\nOnly categorized errors and timing-independent state are printed.")
        finally:
            await browser.close()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--player", help="Optional player name or ID (never printed)")
    parser.add_argument("--headed", action="store_true")
    parser.add_argument("--timeout-ms", type=int, default=5000)
    parser.add_argument("--observe-ms", type=int, default=2000)
    args = parser.parse_args()
    if args.timeout_ms < 1000 or args.observe_ms < 0:
        parser.error("timeout must be >=1000 ms and observe >=0 ms")
    try:
        return asyncio.run(run(args))
    except (PlaywrightError, ValueError) as exc:
        print("Diagnostic failed: " + safe_error(str(exc)), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
