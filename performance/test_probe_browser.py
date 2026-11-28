"""Optional real-Chromium smoke test; skips if Chromium is not installed."""
import os
import shutil
import unittest
from pathlib import Path

from playwright.async_api import async_playwright
from benchmark import JS_RESULTS, PROBE_SCRIPT


FIXTURE = '''<!doctype html><html><head>
<script>const DEFAULT_COVER='data:image/svg+xml,%3Csvg xmlns=%22http://www.w3.org/2000/svg%22 width=%221%22 height=%221%22%3E%3C/svg%3E';</script>
<script>
var bus = { $emit: function(name, data) {
  if (name === 'playerStatus') {
    try { nowplayingOnPlayerStatus(data); }
    catch (e) { setTimeout(function() {
      bus.$emit('refreshStatus'); nowplayingOnPlayerStatus(data);
    }, 1000); }
  }
} };
//# sourceURL=http://example.invalid/material/html/js/nowplaying-page.js
</script>
<script>
setTimeout(function() {
  bus.$emit('playerStatus', {current:{title:'PRIVATE TRACK TITLE NEVER EXPORTED'}});
}, 150);
setTimeout(function() {
  window.nowplayingOnPlayerStatus = function(data) {
    document.querySelector('.np-title').textContent = data.current.title;
    document.querySelector('.np-cover').src = 'data:image/svg+xml,%3Csvg xmlns="http://www.w3.org/2000/svg" width="1" height="1"%3E%3C/svg%3E';
  };
}, 400);
</script></head><body><div id="app"><div class="lms-app"><div id="np-page">
<p class="np-title"></p><img class="np-cover" alt="" src="data:image/svg+xml,%3Csvg xmlns=%22http://www.w3.org/2000/svg%22 width=%221%22 height=%221%22%3E%3C/svg%3E"></div></div></div></body></html>'''


class ProbeChromiumTests(unittest.IsolatedAsyncioTestCase):
    async def test_missing_deferred_function_and_fallback_are_observed(self):
        async with async_playwright() as pw:
            configured = os.environ.get('MATERIAL_PERF_TEST_CHROMIUM')
            installed = Path(pw.chromium.executable_path)
            browser_path = configured or (str(installed) if installed.exists() else shutil.which('chromium'))
            if not browser_path:
                self.skipTest('Chromium not installed: python -m playwright install chromium')
            browser = await pw.chromium.launch(headless=True, executable_path=browser_path)
            try:
                context = await browser.new_context()
                await context.add_init_script(script=PROBE_SCRIPT)
                page = await context.new_page()
                await page.goto('about:blank')
                await page.set_content(FIXTURE)
                await page.wait_for_function('window.__materialBenchmark?.trackVisible !== null', timeout=3000)
                probe = await page.evaluate(JS_RESULTS)
                diagnostics, marks = probe['diagnostics'], probe['marks']
                self.assertEqual(diagnostics['statusCount'], 1)
                self.assertEqual(diagnostics['statusBeforeDeferredCount'], 1)
                self.assertIs(diagnostics['firstStatusHadDeferred'], False)
                self.assertEqual(diagnostics['retryScheduledCount'], 1)
                self.assertLess(marks['firstPlayerStatus'], marks['deferredFunctionReady'])
                self.assertLess(marks['deferredFunctionReady'], marks['trackVisible'])
                self.assertLess(marks['firstRetryScheduled'], marks['firstRefreshAfterRetry'])
                self.assertIsNotNone(marks['artworkCandidateVisible'])
                self.assertGreaterEqual(marks['artworkCandidateVisible'], marks['firstRefreshAfterRetry'])
                self.assertNotIn('PRIVATE TRACK TITLE', str(probe))
            finally:
                await browser.close()


if __name__ == '__main__':
    unittest.main()
