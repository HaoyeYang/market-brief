import unittest

from notify import build_email


class NotifyTests(unittest.TestCase):
    def test_email_contains_private_viewer_link_and_no_html_tracking(self):
        email = build_email(
            "SUCCESS", "2026-07-20.preopen — 8,000 chars",
            "sender@example.com", "reader@example.com",
            "https://private-viewer.run.app",
        )
        self.assertEqual(email["To"], "reader@example.com")
        self.assertIn("SUCCESS", email["Subject"])
        body = email.get_content()
        self.assertIn("https://private-viewer.run.app", body)
        self.assertIn("Google IAP", body)
        self.assertNotIn("<img", body)


if __name__ == "__main__":
    unittest.main()
