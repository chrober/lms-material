"""Unit regression tests for the benchmark-only A/B experiment."""
import json
import unittest
from types import SimpleNamespace
from experiment import (make_early_html, fulfill_early_html, home_storage_script,
                        scenarios, summarize, html_profile, format_html_profile, safe_script_basename)

BASE_HTML = '''<html><body>
<script src="html/js/nowplaying-page.js?r=1.5.6"></script>
<script src="html/js/progressbar.js?r=1.5.6"></script>
<script src="html/js/main.js?r=1.5.6"></script>
<script>addJsToDocument("html/js/", ["browse-resp", "browse-functions", "nowplaying-functions", "prompt-dialog"], "1.5.6")</script>
</body></html>'''


class ExperimentTests(unittest.TestCase):
    def test_html_profile_handles_two_script_wrapper(self):
        shell = '<html><head></head><body><script src="https://a/private.js?key=SECRET"></script>' \
                '<script src="material.js"></script></body></html>'
        self.assertEqual(html_profile(shell)['scripts'], 2)
        result = format_html_profile('Direct', 'http://192.168.1.111:9000/material/?token=SECRET', 200, shell)
        self.assertIn("files=['private.js', 'material.js']", result)
        self.assertNotIn('192.168', result)
        self.assertNotIn('SECRET', result)
        self.assertNotIn('https://', result)

    def test_html_profile_handles_template_and_sanitizes_bad_filename(self):
        self.assertEqual(html_profile(BASE_HTML)['deferred_entry'], 1)
        self.assertEqual(html_profile(BASE_HTML)['scripts'], 3)
        self.assertEqual(safe_script_basename('https://host/s/path.js?secret=abc'), 'path.js')
        self.assertEqual(safe_script_basename('https://host/s/bad%20name.js?key=xyz'), '[nonstandard]')

    def test_early_script_exactly_once_before_main(self):
        result = make_early_html(BASE_HTML)
        self.assertEqual(result.count('nowplaying-functions.js?r='), 1)
        self.assertLess(result.index('nowplaying-page.js'), result.index('nowplaying-functions.js'))
        self.assertLess(result.index('nowplaying-functions.js'), result.index('main.js'))
        self.assertIn('"browse-functions", "prompt-dialog"', result)
        self.assertNotIn('"nowplaying-functions",', result)
        self.assertIn('nowplaying-functions.js?r=1.5.6', result)

    def test_reject_invalid_html(self):
        for html in [BASE_HTML.replace('nowplaying-page.js', 'nowplaying.page.js').replace('main.js', 'not-main.js'),
                     BASE_HTML.replace('"nowplaying-functions", ', ''),
                     BASE_HTML.replace('"nowplaying-functions", ', '"nowplaying-functions", "nowplaying-functions", '),
                     make_early_html(BASE_HTML)]:
            with self.subTest(html=html[:70]), self.assertRaises(ValueError):
                make_early_html(html)

    def test_accepts_script_attrs_paths_and_changed_query(self):
        html = BASE_HTML.replace(
            '<script src="html/js/nowplaying-page.js?r=1.5.6"></script>',
            '<script defer data-feature="nowplaying" src="/material/html/js/nowplaying-page.js?v=2026-11&amp;x=1" crossorigin="anonymous"></script>')
        changed = make_early_html(html)
        self.assertIn('src="/material/html/js/nowplaying-functions.js?v=2026-11&amp;x=1"', changed)
        self.assertEqual(changed.count('nowplaying-functions.js'), 1)

    def test_falls_back_to_main_when_page_script_is_bundled(self):
        html = BASE_HTML.replace('nowplaying-page.js', 'app-bundle.js')
        changed = make_early_html(html)
        self.assertEqual(changed.count('nowplaying-functions.js'), 1)
        self.assertLess(changed.index('nowplaying-functions.js'), changed.index('main.js'))

    def test_bundled_bootstrap_can_insert_into_head(self):
        html = BASE_HTML.replace('<html><body>', '<html><head></head><body>') \
                        .replace('nowplaying-page.js', 'app-bundle.js').replace('main.js', 'material-bundle.js')
        changed = make_early_html(html)
        self.assertEqual(changed.count('nowplaying-functions.js'), 1)
        self.assertLess(changed.index('nowplaying-functions.js'), changed.index('</head>'))

    def test_unrecognizable_html_fails_with_safe_diagnostics(self):
        html = '<html><body>unsupported</body></html>'
        with self.assertRaisesRegex(ValueError, 'deferred-entry=0') as error:
            make_early_html(html)
        self.assertNotIn('192.168', str(error.exception))

    def test_config_contains_only_requested_home_items(self):
        on = scenarios('home')[0]
        off = scenarios('home')[1]
        self.assertEqual(on[0], 'home-scrollable-on')
        self.assertEqual(on[3], ['std_new','std_random','std_playlists','std_radios','std_explore'])
        self.assertEqual(off[3], [])
        code=home_storage_script(on[3])
        self.assertIn('lms-material::detailedHomeItems', code)
        self.assertIn('lms-material::top-grid', code)
        self.assertIn(json.dumps(json.dumps(on[3])),code)
        self.assertEqual(len(scenarios('all')), 4)
        self.assertEqual(len(scenarios('now-playing')), 2)

    def test_summary_no_private_data(self):
        rows = [dict(scenario='np-early', mode='cold', app_ready_total_ms=400,
                     first_player_status_total_ms=1300, track_visible_total_ms=1340,
                     deferred_function_ready_total_ms=500, home_extra_count=0,
                     status_before_deferred_count=0,error=None),
                dict(scenario='np-early', mode='cold', app_ready_total_ms=500,
                     first_player_status_total_ms=1500, track_visible_total_ms=1510,
                     deferred_function_ready_total_ms=610, home_extra_count=0,
                     status_before_deferred_count=0,error=None)]
        result=summarize(rows)['np-early/cold']
        self.assertEqual(result['title_visible_ms'], 1425)
        self.assertEqual(result['status_before_js_runs'], 0)
        self.assertEqual(result['errors'], 0)


    def test_packaged_release_moves_deferred_once_and_preserves_css(self):
        # mkrel.py combines the individual source files in released builds.
        html = (
            '<html><head><script src="html/js/material.min.js?r=1.5.6"></script></head>'
            '<body><script src="material/custom.js"></script>'
            '<script type="text/javascript">function loadOtherFiles() {'
            'var script = document.createElement("script");'
            'script.src = "html/js/material-deferred.min.js?r=1.5.6";'
            'document.body.appendChild(script);'
            'var link = document.createElement("link");'
            'link.href = "html/css/lib-deferred.min.css?r=1.5.6";'
            'link.rel = "stylesheet"; document.body.appendChild(link);'
            '} window.addEventListener("load", loadOtherFiles);'
            '</script></body></html>')
        result = make_early_html(html)
        self.assertEqual(result.count('material-deferred.min.js'), 1)
        self.assertLess(result.index('material.min.js'), result.index('material-deferred.min.js'))
        self.assertLess(result.index('custom.js'), result.index('material-deferred.min.js'))
        self.assertLess(result.index('material-deferred.min.js'), result.index('loadOtherFiles'))
        self.assertIn('lib-deferred.min.css', result)
        self.assertNotIn('document.body.appendChild(script)', result)
        with self.assertRaises(ValueError):
            make_early_html(result)  # Must never run bundle twice.

    def test_packaged_loader_quotes_and_escaped_query(self):
        html = ('<html><body><script src="./html/js/material.min.js?v=42"></script>'
                '<script>function loadOtherFiles() {'
                "var script=document.createElement('script');"
                "script.src='html/js/material-deferred.min.js?v=42&amp;key=a';"
                'document.body.appendChild(script); }</script></body></html>')
        result = make_early_html(html)
        self.assertIn('src="html/js/material-deferred.min.js?v=42&amp;key=a"', result)
        self.assertNotIn('script.src=', result)

    def test_packaged_unknown_loader_rejected(self):
        html = ('<script src="html/js/material.min.js?r=1"></script>'
                '<script>function loadOtherFiles(){'
                'var script=document.createElement("script");'
                'script.src="html/js/material-deferred.min.js?r=1";'
                'document.head.appendChild(script);}</script>')
        with self.assertRaisesRegex(ValueError, 'Unsupported packaged'):
            make_early_html(html)



class RouteSafetyTests(unittest.IsolatedAsyncioTestCase):
    async def test_unsupported_response_is_served_unmodified_without_raising(self):
        calls = []
        class Reply:
            async def text(self):
                return '<html><body>unsupported Material</body></html>'
        class Route:
            async def fetch(self):
                return Reply()
            async def fulfill(self, **kwargs):
                calls.append(kwargs)
            async def abort(self):
                calls.append('aborted')
        applied, errors = [0], []
        await fulfill_early_html(Route(), applied, errors)
        self.assertEqual(applied, [0])
        self.assertEqual(len(errors), 1)
        self.assertIn('deferred-entry=0', errors[0])
        self.assertEqual(len(calls), 1)
        self.assertEqual(set(calls[0]), {'response'})

    async def test_valid_response_is_rewritten_once(self):
        calls = []
        class Reply:
            async def text(self):
                return BASE_HTML
        class Route:
            async def fetch(self):
                return Reply()
            async def fulfill(self, **kwargs):
                calls.append(kwargs)
        applied, errors = [0], []
        await fulfill_early_html(Route(), applied, errors)
        self.assertEqual(applied, [1])
        self.assertEqual(errors, [])
        self.assertEqual(len(calls), 1)
        self.assertIn('nowplaying-functions.js', calls[0]['body'])
        self.assertNotIn('"nowplaying-functions"', calls[0]['body'])

if __name__ == '__main__':
    unittest.main()
