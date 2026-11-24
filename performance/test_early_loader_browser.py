"""Browser checks for early deferred-loader registration without HTTP interception."""
import inspect
import os
import shutil
import unittest
from pathlib import Path

from playwright.async_api import async_playwright
from early_loader import EARLY_LOADER_INIT_SCRIPT, EARLY_LOADER_STATE_JS
from experiment import run_experiment


class HookSafetyTests(unittest.TestCase):
    def test_experiment_has_no_http_rewrite_route(self):
        source = inspect.getsource(run_experiment)
        self.assertIn('add_init_script(script=EARLY_LOADER_INIT_SCRIPT)', source)
        self.assertNotIn('ctx.route(', source)
        self.assertNotIn('fulfill_early_html(', source)
        self.assertNotIn('make_early_html(', source)

    def test_hook_targets_only_named_material_loader(self):
        self.assertIn("listener.name === 'loadOtherFiles'", EARLY_LOADER_INIT_SCRIPT)
        self.assertIn("type === 'load'", EARLY_LOADER_INIT_SCRIPT)
        self.assertIn("nativeAddEventListener.apply(this, arguments)", EARLY_LOADER_INIT_SCRIPT)
        self.assertIn("registrations", EARLY_LOADER_STATE_JS)
        self.assertIn("firings", EARLY_LOADER_STATE_JS)


class HookChromiumTests(unittest.IsolatedAsyncioTestCase):
    async def test_hook_moves_only_material_loader_to_domcontentloaded(self):
        async with async_playwright() as pw:
            executable = os.environ.get('MATERIAL_PERF_TEST_CHROMIUM')
            if not executable:
                bundled = Path(pw.chromium.executable_path)
                executable = str(bundled) if bundled.exists() else shutil.which('chromium')
            if not executable:
                self.skipTest('Chromium not installed')
            browser = await pw.chromium.launch(headless=True, executable_path=executable)
            try:
                context = await browser.new_context()
                await context.add_init_script(script=EARLY_LOADER_INIT_SCRIPT)
                page = await context.new_page()
                html = """<!doctype html><html><head><script>
                  window.sequence = [];
                  function loadOtherFiles() {window.sequence.push('deferred');}
                  window.addEventListener('load', loadOtherFiles, false);
                  window.addEventListener('DOMContentLoaded', () => window.sequence.push('dom'));
                  window.addEventListener('load', () => window.sequence.push('other-load'));
                </script></head><body><p>Test</p></body></html>"""
                await page.goto('about:blank')
                await page.set_content(html, wait_until='load')
                state = await page.evaluate(EARLY_LOADER_STATE_JS)
                order = await page.evaluate('window.sequence')
                self.assertEqual(state['registrations'], 1)
                self.assertEqual(state['firings'], 1)
                self.assertLessEqual(state['registeredAt'], state['firedAt'])
                self.assertEqual(order[0], 'deferred')
                self.assertEqual(order[-1], 'other-load')
                self.assertEqual(order.count('deferred'), 1)
                self.assertEqual(order.count('dom'), 1)
                await context.close()
            finally:
                await browser.close()

    async def test_baseline_waits_for_window_load(self):
        async with async_playwright() as pw:
            executable = os.environ.get('MATERIAL_PERF_TEST_CHROMIUM') or shutil.which('chromium')
            if not executable and not Path(pw.chromium.executable_path).exists():
                self.skipTest('Chromium not installed')
            browser = await pw.chromium.launch(
                headless=True, executable_path=executable or str(pw.chromium.executable_path))
            try:
                page = await browser.new_page()
                html = """<script>
                  window.sequence=[];
                  function loadOtherFiles(){window.sequence.push('deferred');}
                  window.addEventListener('load', loadOtherFiles);
                  window.addEventListener('DOMContentLoaded',()=>window.sequence.push('dom'));
                </script>"""
                await page.goto('about:blank')
                await page.set_content(html, wait_until='load')
                self.assertEqual(await page.evaluate('window.sequence'), ['dom','deferred'])
                await page.close()
            finally:
                await browser.close()


if __name__ == '__main__':
    unittest.main()
