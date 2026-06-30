import tempfile
import unittest
from pathlib import Path

from x2md import build_markdown, load_xquik_tweet, tweet_from_xquik


class XquikJsonTest(unittest.TestCase):
    def test_converts_search_payload(self):
        tweet = tweet_from_xquik(
            {
                "tweets": [
                    {
                        "id": "123",
                        "text": "Saved from Xquik",
                        "url": "https://x.com/alice/status/123",
                        "createdAt": "2026-06-30T00:00:00Z",
                        "replyCount": 0,
                        "author": {"name": "Alice", "username": "alice"},
                        "media": [{"type": "photo", "url": "https://example.com/photo.jpg"}],
                    }
                ]
            },
            "https://x.com/alice/status/123",
            "alice",
            "123",
        )

        self.assertEqual(tweet["id"], "123")
        self.assertEqual(tweet["replies"], 0)
        self.assertEqual(tweet["author"]["screen_name"], "alice")
        self.assertEqual(tweet["media"]["photos"], [{"url": "https://example.com/photo.jpg"}])

    def test_loads_json_payload_for_markdown(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "tweet.json"
            path.write_text(
                '{"tweet":{"id":"456","text":"From file","author":{"username":"bob"}}}',
                encoding="utf-8",
            )

            tweet = load_xquik_tweet(
                str(path),
                "https://x.com/bob/status/456",
                "bob",
                "456",
            )
            markdown, frontmatter = build_markdown(
                tweet,
                "https://x.com/bob/status/456",
                0,
                True,
            )

        self.assertIn("From file", markdown)
        self.assertEqual(frontmatter["author"], "bob (@bob)")


if __name__ == "__main__":
    unittest.main()
