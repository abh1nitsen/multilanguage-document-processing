import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from document_processing import full_text
from document_processing.store import Store, dumps


class FullTextTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "records.sqlite"
        self.store = Store(self.path)
        source = Path(__file__).resolve().parents[1] / "examples/school.txt"
        self.doc = self.store.ingest(source)["document"]

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def set_pages(self, pages):
        with self.store.conn:
            self.store.conn.execute("UPDATE documents SET pages_json=? WHERE id=?", (dumps(pages), self.doc["id"]))

    def test_segments_cover_every_character_and_page(self):
        pages = ["शुरुआत\n" + "वाक्य १२। " * 1500 + "अंत", "दूसरा पृष्ठ"]
        chunks = list(full_text.segments(pages))
        for i, page in enumerate(pages, 1):
            selected = [c for c in chunks if c["page"] == i]
            self.assertEqual("".join(c["text"] for c in selected), page)
            self.assertTrue(all(len(c["text"]) <= full_text.CHUNK_LIMIT for c in selected))
            self.assertEqual(selected[-1]["end"], len(page))

    def test_full_text_persists_and_reuses_saved_translation(self):
        self.set_pages(["पहला पृष्ठ", "दूसरा पृष्ठ"])
        with patch("document_processing.full_text.translate_chunk", side_effect=[
                {"english_text": "First page in full.", "warnings": []},
                {"english_text": "Second page in full.", "warnings": ["Check original signature."]}]) as provider:
            result = self.store.translate_full_text(self.doc["id"], "fake")
            self.assertEqual(provider.call_count, 2)
        self.store.close()
        self.store = Store(self.path)
        with patch("document_processing.full_text.translate_chunk", side_effect=AssertionError("No repeat calls")):
            saved = self.store.translate_full_text(self.doc["id"], "fake")
        self.assertEqual(saved["id"], result["id"])
        self.assertEqual(len(saved["pages"]), 2)
        self.assertIn("Second page in full.", full_text.as_text(saved))
        self.assertEqual(saved["review_state"], "unreviewed")
        self.assertIsNone(self.store.english(self.doc["id"]))
        self.assertEqual(self.store.get(self.doc["id"])["state"], "needs_review")

    def test_failed_later_page_resumes_without_retranslating_first(self):
        self.set_pages(["पहला पृष्ठ", "दूसरा पृष्ठ"])
        with patch("document_processing.full_text.translate_chunk", side_effect=[
                {"english_text": "First page.", "warnings": []}, ValueError("Service unavailable")]):
            with self.assertRaisesRegex(ValueError, "Service unavailable"):
                self.store.translate_full_text(self.doc["id"], "fake")
        self.assertIsNone(self.store.full_english(self.doc["id"], "fake"))
        with patch("document_processing.full_text.translate_chunk", return_value={"english_text": "Second page.", "warnings": []}) as provider:
            result = self.store.translate_full_text(self.doc["id"], "fake")
        self.assertEqual(provider.call_count, 1)
        self.assertEqual(result["pages"][0]["english_text"], "First page.")

    def test_numbers_flagged_and_empty_output_rejected(self):
        result = full_text.validate_result({"english_text": "Attend the meeting.", "warnings": []}, "दिनांक ०७/१०/२०२१")
        self.assertTrue(result["warnings"])
        with self.assertRaises(ValueError):
            full_text.validate_result({"english_text": "", "warnings": []}, "पूर्ण पाठ")

    def test_identical_readings_from_different_files_share_calls_not_ownership(self):
        with patch("document_processing.full_text.translate_chunk", return_value={"english_text": "The complete notice.", "warnings": []}) as provider:
            first = self.store.translate_full_text(self.doc["id"], "fake")
            source = Path(__file__).resolve().parents[1] / "examples/school.txt"
            alternative = Path(self.tmp.name) / "same-with-bom.txt"
            alternative.write_bytes(b"\xef\xbb\xbf" + source.read_bytes())
            second_doc = self.store.ingest(alternative)["document"]
            second = self.store.translate_full_text(second_doc["id"], "fake")
            self.assertEqual(provider.call_count, 1)
        self.assertNotEqual(first["document_id"], second["document_id"])
        self.assertEqual(second["document_id"], second_doc["id"])

    def test_changed_reading_does_not_reuse_old_full_translation(self):
        with patch("document_processing.full_text.translate_chunk", return_value={"english_text": "A complete translation.", "warnings": []}):
            original = self.store.translate_full_text(self.doc["id"], "fake")
        self.set_pages(["बदला हुआ स्रोत"])
        self.assertIsNone(self.store.full_english(self.doc["id"], "fake"))
        self.assertEqual(self.store.conn.execute("SELECT COUNT(*) FROM full_text_translations").fetchone()[0], 1)
