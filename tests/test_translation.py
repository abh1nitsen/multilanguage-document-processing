import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from document_processing.store import Store
from document_processing.translate import validate_proposal


class TranslationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / "test.sqlite")
        root = Path(__file__).resolve().parents[1]
        self.doc = self.store.ingest(root / "examples/school.txt")["document"]
        self.raw = {"issuer": "Adarsh School, Bhopal", "subject": "Parent meeting", "location": "School auditorium",
                    "required_action": "Parents should attend at 10 am with the progress report."}

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def confirm_source(self):
        self.doc = self.store.confirm(self.doc["id"], "tester", self.doc["revision"])

    def draft(self):
        with patch("document_processing.translate.propose", return_value=self.raw):
            return self.store.translate(self.doc["id"], "fake")

    def test_translation_requires_confirmed_source(self):
        with self.assertRaises(ValueError):
            self.draft()

    def test_review_and_ascii_export_preserve_original_dates_and_data(self):
        self.confirm_source()
        draft = self.draft()
        target = Path(self.tmp.name) / "english.json"
        with self.assertRaises(ValueError):
            self.store.export(target, "english-reviewed")
        values = dict(self.raw, reference_number="AV/2026/42")
        reviewed = self.store.confirm_english(draft["id"], draft["revision"], values, "reviewer", "Checked meaning against source")
        self.assertEqual(reviewed["state"], "confirmed")
        self.store.export(target, "english-reviewed")
        self.assertTrue(target.read_text(encoding="utf-8").isascii())
        self.assertEqual(reviewed["effective"]["issued_date"], "2026-11-02")
        self.assertEqual(self.store.get(self.doc["id"])["effective"]["issuer"], "आदर्श विद्यालय, भोपाल")
        with self.assertRaises(ValueError):
            self.store.confirm_english(draft["id"], draft["revision"], values, "other", "Stale edit")

    def test_source_change_invalidates_translation(self):
        self.confirm_source()
        draft = self.draft()
        self.store.reopen(self.doc["id"], self.doc["revision"])
        with self.assertRaises(ValueError):
            self.store.confirm_english(draft["id"], draft["revision"], dict(self.raw, reference_number="AV/2026/42"), "reviewer", "Checked")

    def test_model_cannot_invent_absent_source_fields(self):
        with self.assertRaises(ValueError):
            validate_proposal(self.raw, {"issuer": "संस्था", "subject": "बैठक", "location": None, "required_action": "आएँ"})

    def test_review_blocks_non_ascii(self):
        self.confirm_source()
        draft = self.draft()
        with self.assertRaises(ValueError):
            self.store.confirm_english(draft["id"], draft["revision"], dict(self.raw, reference_number="ई४२"), "reviewer", "Checked")
