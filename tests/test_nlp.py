import sqlite3
import unittest

from server.db import _repair_legacy_sentiment_false_positives
from server.nlp import analyze_text


class NlpTests(unittest.TestCase):
    def test_spanish_promotional_copy_is_not_negative_or_ads(self):
        text = (
            "Si ya conoces Plaud por sus grabadoras con IA, Plaud Note Pro y "
            "Plaud NotePin S, Plaud One es el siguiente paso. Descubre ..."
        )

        result = analyze_text(text)

        self.assertEqual("neutral", result["sentiment"])
        self.assertEqual([], result["sentiment_explanation"]["negative_terms"])
        self.assertNotIn("ads", result["topics"])

    def test_english_negative_words_still_match_as_words(self):
        result = analyze_text("This is a bad ad")

        self.assertEqual("negative", result["sentiment"])
        self.assertIn("bad", result["sentiment_explanation"]["negative_terms"])
        self.assertIn("ads", result["topics"])

    def test_repairs_existing_false_positive_sentiment(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute(
            "CREATE TABLE records (id TEXT PRIMARY KEY, title TEXT, body TEXT, sentiment TEXT, sentiment_score REAL, intent TEXT, topics_json TEXT)"
        )
        conn.execute(
            "INSERT INTO records VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                "spanish-ad",
                "",
                "Si ya conoces Plaud por sus grabadoras con IA, Plaud One es el siguiente paso.",
                "negative",
                -0.3333,
                "observation",
                '["ads"]',
            ),
        )

        _repair_legacy_sentiment_false_positives(conn)
        row = conn.execute("SELECT * FROM records WHERE id = 'spanish-ad'").fetchone()

        self.assertEqual("neutral", row["sentiment"])
        self.assertEqual("observation", row["intent"])
        self.assertEqual("[]", row["topics_json"])


if __name__ == "__main__":
    unittest.main()
