"""Unit regression tests for the browser-only Material Skin A/B experiment."""
import json
import unittest
from experiment import (
    make_early_html, fulfill_early_html, home_storage_script, scenarios,
    summarize, html_profile, format_html_profile, safe_script_basename,
)

BASE_HTML = '''<html><head></head><body>
<script src="html/js/nowplaying-page.js?r=1.5.6"></script>
<script src="html/js/main.js?r=1.5.6"></script>
<script type="text/javascript">
function loadOtherFiles() {
  addJsToDocument("html/js/", ["browse-resp", "browse-functions",
                              "nowplaying-functions", "prompt-dialog"], "1.5.6");
}
if (window.addEventListener) {
  window.addEventListener("load", loadOtherFiles, false);
}
</script></body></html>'''

PACKAGED_HTML = '''<html><head></head><body>
<script src="html/js/material.min.js?r=1.5.6"></script>
<script src="material/custom.js"></script>
<script type="text/javascript">
function loadOtherFiles() {
  var script = document.createElement("script");
  script.src = "html/js/material-deferred.min.js?r=1.5.6";
  document.body.appendChild(script);
  var link = document.createElement("link");
  link.href = "html/css/lib-deferred.min.css?r=1.5.6";
  link.rel = "stylesheet";
  document.body.appendChild(link);
}
if (window.addEventListener) {
  window.addEventListener("load", loadOtherFiles, false);
} else if (window.attachEvent) {
  window.attachEvent("onload", loadOtherFiles);
} else {
  window.onload = loadOtherFiles;
}
</script></body></html>'''


class ExperimentTests(unittest.TestCase):
    def test_html_profile_handles_two_script_wrapper(self):
        shell = ('<html><head></head><body>'
                 '<script src="https://a/private.js?key=SECRET"></script>'
                 '<script src="material.js"></script></body></html>')
        self.assertEqual(html_profile(shell)['scripts'], 2)
        profile = format_html_profile(
            'Direct', 'http://192.168.1.111:9000/material/?token=SECRET',
            200, shell)
        self.assertIn("files=['private.js', 'material.js']", profile)
        self.assertNotIn('192.168', profile)
        self.assertNotIn('SECRET', profile)

    def test_profile_sanitization(self):
        self.assertEqual(html_profile(BASE_HTML)['deferred_entry'], 1)
        self.assertEqual(safe_script_basename(
            'https://host/s/path.js?secret=abc'), 'path.js')
        self.assertEqual(safe_script_basename(
            'https://host/s/bad%20name.js?key=xyz'), '[nonstandard]')

    def test_unbundled_changes_event_only(self):
        result = make_early_html(BASE_HTML)
        self.assertIn('window.addEventListener("DOMContentLoaded", loadOtherFiles, false)', result)
        self.assertIn('"nowplaying-functions", "prompt-dialog"', result)
        self.assertEqual(result.count('nowplaying-page.js'), 1)
        self.assertEqual(result.count('main.js'), 1)
        self.assertEqual(result.count('nowplaying-functions'), 1)
        self.assertEqual(result.count('addJsToDocument'), 1)
        self.assertNotIn('window.addEventListener("load", loadOtherFiles', result)

    def test_release_changes_event_only_and_preserves_bundle_order(self):
        result = make_early_html(PACKAGED_HTML)
        self.assertIn('window.addEventListener("DOMContentLoaded", loadOtherFiles, false)', result)
        self.assertEqual(result.count('material.min.js'), 1)
        self.assertEqual(result.count('material-deferred.min.js'), 1)
        self.assertIn('document.body.appendChild(script)', result)
        self.assertIn('lib-deferred.min.css', result)
        self.assertIn('window.attachEvent("onload", loadOtherFiles)', result)
        self.assertEqual(
            result.replace('"DOMContentLoaded", loadOtherFiles', '"load", loadOtherFiles'),
            PACKAGED_HTML)

    def test_no_movement_or_synchronous_execution(self):
        result = make_early_html(PACKAGED_HTML)
        self.assertEqual(
            result.index('material.min.js') < result.index('loadOtherFiles'),
            True)
        self.assertNotIn('<script src="html/js/material-deferred.min.js', result)

    def test_single_quotes(self):
        original = PACKAGED_HTML.replace(
            'window.addEventListener("load", loadOtherFiles, false)',
            "window.addEventListener('load',loadOtherFiles,false)")
        result = make_early_html(original)
        self.assertIn("window.addEventListener('DOMContentLoaded',loadOtherFiles,false)", result)

    def test_reject_invalid_and_already_modified(self):
        for invalid in (
            '<html><body>unsupported</body></html>',
            BASE_HTML.replace('function loadOtherFiles()', 'function unused()'),
            BASE_HTML.replace('window.addEventListener("load", loadOtherFiles, false)', ''),
            make_early_html(BASE_HTML),
            BASE_HTML.replace(
                'window.addEventListener("load", loadOtherFiles, false)',
                'window.addEventListener("load", loadOtherFiles, false);'
                'window.addEventListener("load", loadOtherFiles, false)'),
        ):
            with self.subTest(invalid=invalid[:70]), self.assertRaises(ValueError):
                make_early_html(invalid)

    def test_scenarios_and_storage(self):
        enabled, disabled = scenarios('home')
        self.assertEqual(enabled[0], 'home-scrollable-on')
        self.assertEqual(enabled[3], [
            'std_new', 'std_random', 'std_playlists', 'std_radios', 'std_explore'])
        self.assertEqual(disabled[3], [])
        script = home_storage_script(enabled[3])
        self.assertIn('lms-material::detailedHomeItems', script)
        self.assertIn(json.dumps(json.dumps(enabled[3])), script)
        self.assertEqual(len(scenarios('all')), 4)
        self.assertEqual(len(scenarios('now-playing')), 2)

    def test_summary_medians(self):
        rows = [
            dict(scenario='np-early', mode='cold', app_ready_total_ms=x,
                 first_player_status_total_ms=1300,
                 track_visible_total_ms=x+100,
                 deferred_function_ready_total_ms=500, home_extra_count=0,
                 status_before_deferred_count=0, error=None)
            for x in (400, 500)
        ]
        result = summarize(rows)['np-early/cold']
        self.assertEqual(result['app_ready_ms'], 450)
        self.assertEqual(result['title_visible_ms'], 550)
        self.assertEqual(result['status_before_js_runs'], 0)
        self.assertEqual(result['errors'], 0)


class RouteTests(unittest.IsolatedAsyncioTestCase):
    async def test_invalid_html_is_returned_unchanged(self):
        calls = []
        class Reply:
            async def text(self): return '<html><body>unsupported</body></html>'
        class Route:
            async def fetch(self): return Reply()
            async def fulfill(self, **kwargs): calls.append(kwargs)
            async def abort(self): calls.append('aborted')
        applied, errors = [0], []
        await fulfill_early_html(Route(), applied, errors)
        self.assertEqual(applied, [0])
        self.assertEqual(len(errors), 1)
        self.assertIn('deferred Material loader', errors[0])
        self.assertEqual(len(calls), 1)
        self.assertEqual(set(calls[0]), {'response'})

    async def test_valid_html_only_switches_event(self):
        calls = []
        class Reply:
            async def text(self): return PACKAGED_HTML
        class Route:
            async def fetch(self): return Reply()
            async def fulfill(self, **kwargs): calls.append(kwargs)
        applied, errors = [0], []
        await fulfill_early_html(Route(), applied, errors)
        self.assertEqual(applied, [1])
        self.assertEqual(errors, [])
        result = calls[0]['body']
        self.assertEqual(result.count('material-deferred.min.js'), 1)
        self.assertIn('"DOMContentLoaded", loadOtherFiles', result)
        self.assertIn('document.body.appendChild(script)', result)


if __name__ == '__main__':
    unittest.main()
