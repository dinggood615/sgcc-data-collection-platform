import os
from pathlib import Path
import tempfile
import unittest

from app.database import CURATED_KEYWORDS, connect, init_db


class KeywordBaselineTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.previous_database = os.environ.get("DATABASE_PATH")
        os.environ["DATABASE_PATH"] = str(Path(self.temporary.name) / "keywords.sqlite3")

    def tearDown(self):
        if self.previous_database is None:
            os.environ.pop("DATABASE_PATH", None)
        else:
            os.environ["DATABASE_PATH"] = self.previous_database
        self.temporary.cleanup()

    def test_baseline_covers_requested_categories_and_replaces_existing_terms(self):
        init_db()
        with connect() as db:
            terms = {row["term"] for row in db.execute("SELECT term FROM keywords")}
        self.assertEqual(terms, set(CURATED_KEYWORDS))
        for required in ("信息化", "数字化", "软件实施", "人力外包", "驻场开发"):
            self.assertIn(required, terms)

    def test_cleared_keywords_do_not_return_on_restart(self):
        init_db()
        with connect() as db:
            db.execute("DELETE FROM keywords")
        init_db()
        with connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM keywords").fetchone()[0], 0)


if __name__ == "__main__":
    unittest.main()
