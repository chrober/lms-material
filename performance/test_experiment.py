"""Regression tests for the browser-side A/B experiment."""
import unittest
from experiment import make_early_html, scenarios, home_storage_script, summarize

SAMPLE = """<script src="html/js/nowplaying-page.js?r=123"></script>
<script src="html/js/main.js?r=123"></script>
<script>addJsToDocument("html/js/", ["browse-resp", "browse-functions", "nowplaying-functions", "prompt-dialog"], "123")</script>"""

class ABExperimentTests(unittest.TestCase):
    def test_rewrite_moves_one_script_before_main(self):
        result = make_early_html(SAMPLE)
        self.assertEqual(result.count("nowplaying-functions.js?r=123"), 1)
        self.assertLess(result.index("nowplaying-page.js"), result.index("nowplaying-functions.js"))
        self.assertLess(result.index("nowplaying-functions.js"), result.index("main.js"))
        self.assertNotIn('"nowplaying-functions"', result)
        self.assertIn('"browse-functions", "prompt-dialog"', result)

    def test_bad_or_already_modified_document_rejected(self):
        for source in (SAMPLE.replace("nowplaying-page.js", "notfound.js"),
                       SAMPLE.replace('"nowplaying-functions", ', ''),
                       make_early_html(SAMPLE)):
            with self.subTest(source=source[:60]), self.assertRaises(ValueError):
                make_early_html(source)

    def test_scenarios_are_controlled(self):
        cases = scenarios("home")
        self.assertEqual(cases[0][3], ["std_new", "std_random", "std_playlists", "std_radios", "std_explore"])
        self.assertEqual(cases[1][3], [])
        self.assertIn("lms-material::top-grid", home_storage_script(cases[0][3]))
        self.assertEqual(len(scenarios("all")), 4)

    def test_summary_groups_and_mediana(self):
        rows = [dict(scenario="np-early",mode="cold",app_ready_total_ms=x,first_player_status_total_ms=900,
                     track_visible_total_ms=x+100,deferred_function_ready_total_ms=200,
                     home_extra_count=0,status_before_deferred_count=0,error=None) for x in (200,300)]
        result = summarize(rows)["np-early/cold"]
        self.assertEqual(result["app_ready_ms"], 250)
        self.assertEqual(result["title_visible_ms"], 350)
        self.assertEqual(result["status_before_js_runs"], 0)

if __name__ == "__main__":
    unittest.main()
