"""Regression tests for the browser interceptor diagnostic."""
import unittest
from types import SimpleNamespace
from diagnose_interceptor import CASES, ROUTE_PATTERN, FINAL_PATTERN, safe_error, failed_key


class DiagnosticTests(unittest.TestCase):
    def test_modes(self):
        self.assertEqual(CASES, ("baseline", "continue", "fulfill-original",
                                 "fulfill-text", "modified"))

    def test_route_and_redirect(self):
        self.assertTrue(ROUTE_PATTERN.search("/material/?player=redacted"))
        self.assertFalse(ROUTE_PATTERN.search("/material/html/js/material.min.js"))
        self.assertTrue(FINAL_PATTERN.search("/material/?layout=desktop&css=msk--np-only"))

    def test_sanitize_error(self):
        msg = ("TypeError: Cannot find 'private track' at "
               "http://192.168.1.111:9000/material/?player=Secret")
        result = safe_error(msg)
        self.assertIn("TypeError", result)
        self.assertNotIn("private track", result)
        self.assertNotIn("192.168.1.111", result)
        self.assertNotIn("Secret", result)

    def test_failed_key(self):
        self.assertEqual(failed_key(SimpleNamespace(
            resource_type="script", failure="net::ERR_ABORTED")),
            "script/net::ERR_ABORTED")
        self.assertEqual(failed_key(SimpleNamespace(
            resource_type="xhr", failure=None)), "xhr/unknown")


if __name__ == "__main__":
    unittest.main()
