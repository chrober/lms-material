"""Pure unit tests; no LMS server or browser is required."""
import tempfile
import unittest
from pathlib import Path

from benchmark import percentile, report_summary, resource_summary, server_origin, slowest_resources, view_url, write_reports


class BenchmarkUnitTests(unittest.TestCase):
    def test_origin_normalization(self):
        self.assertEqual(server_origin('http://192.0.2.5:9000/'), 'http://192.0.2.5:9000')
        self.assertEqual(server_origin('https://example.invalid'), 'https://example.invalid')
        for text in ['file:///tmp/example', 'http://user:secret@localhost:9000',
                     'https://host.example/material', 'https://host.example/?token=secret',
                     'http://localhost:99999']:
            with self.subTest(text=text), self.assertRaises(ValueError):
                server_origin(text)

    def test_view_url_encodes_player(self):
        self.assertEqual(view_url('http://localhost:9000', 'np-only', 'Living Room'),
                         'http://localhost:9000/material/np-only?player=Living+Room')
        self.assertIn('player=a%26b', view_url('http://localhost:9000', 'normal', 'a&b'))

    def test_summary_excludes_missing(self):
        runs = [dict(view='normal', mode='cold', app_ready_total_ms=v,
                     lcp_ms=None, error=None) for v in [10, 30, 20, None]]
        out = report_summary(runs)['normal/cold']
        self.assertEqual(out['samples'], 4)
        self.assertEqual(out['app_ready_samples'], 3)
        self.assertEqual(out['app_ready_median_ms'], 20)
        self.assertEqual(percentile([10, 20, 30], .9), 28)

    def test_resource_redaction(self):
        resources = [dict(pathClass='material-js', safeName='/material/html/js/main.js',
                          duration=100, transferSize=2048),
                     dict(pathClass='artwork', safeName='', duration=200, transferSize=100)]
        self.assertEqual(resource_summary(resources)['material-js']['count'], 1)
        self.assertEqual(slowest_resources(resources)[0]['asset'], 'artwork')

    def test_reports_omit_private_configuration(self):
        run = dict(view='normal', mode='cold', app_ready_total_ms=100.0,
                   lcp_ms=50.0, error=None, resource_groups={}, slowest_resources=[])
        with tempfile.TemporaryDirectory() as tmp:
            write_reports(Path(tmp), [run], {'view': 'normal'})
            results = (Path(tmp) / 'results.json').read_text()
            self.assertIn('normal/cold', results)
            self.assertNotIn('http://', results)
            self.assertTrue((Path(tmp) / 'runs.csv').exists())
            self.assertTrue((Path(tmp) / 'summary.md').exists())


if __name__ == '__main__':
    unittest.main()
