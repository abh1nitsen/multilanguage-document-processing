import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from document_processing.extract import LABELS
from document_processing.judge import validate_report
from document_processing.languages import language_config
from document_processing.model import validate_model_output
from document_processing.store import Store


class ModelTests(unittest.TestCase):
    def test_whitespace_only_quote_differences_keep_exact_source_offsets(self):
        source = "ಸಂಸ್ಥೆ\nವಿಷಯ: ಕನ್ನಡ ಪಠ್ಯಪುಸ್ತಕಗಳನ್ನು\n2025-26ನೇ ಸಾಲಿಗೆ ಖರೀದಿಸುವ ಬಗ್ಗೆ."
        fields = {key: [] for key in LABELS}
        fields["issuer"] = [{"raw": "ಸಂಸ್ಥೆ", "page": 1}]
        fields["subject"] = [{"raw": "ಕನ್ನಡ ಪಠ್ಯಪುಸ್ತಕಗಳನ್ನು 2025-26ನೇ ಸಾಲಿಗೆ ಖರೀದಿಸುವ ಬಗ್ಗೆ.", "page": 1}]
        result = validate_model_output({"is_circular": True, "reason": "Circular", "fields": fields}, [source], "fake")
        self.assertTrue(result["admitted"])
        candidate = result["fields"]["subject"]["candidates"][0]
        evidence = candidate["evidence"]
        self.assertEqual(source[evidence["start"]:evidence["end"]], evidence["quote"])
        self.assertIn("\n", evidence["quote"])
        self.assertFalse(result["fields"]["subject"]["issues"])

    def test_whitespace_tolerance_does_not_accept_changed_words(self):
        fields = {key: [] for key in LABELS}
        fields["subject"] = [{"raw": "ಪಠ್ಯ ಪುಸ್ತಕ", "page": 1}]
        result = validate_model_output({"is_circular": True, "reason": "", "fields": fields}, ["ಪಠ್ಯ\nಪತ್ರ"], "fake")
        self.assertIsNone(result["fields"]["subject"]["value"])

    def test_evidence_not_found_is_withheld(self):
        fields = {k: [] for k in LABELS}
        fields["issuer"] = [{"raw": "Invented issuer", "page": 1}]
        fields["subject"] = [{"raw": "बैठक", "page": 1}]
        raw = {"is_circular": True, "reason": "Notice", "fields": fields}
        result = validate_model_output(raw, ["विषय: बैठक"], "fake")
        self.assertIsNone(result["fields"]["issuer"]["value"])
        self.assertFalse(result["admitted"])
        self.assertTrue(result["fields"]["issuer"]["issues"])

    def test_wrong_types_rejected(self):
        with self.assertRaises(ValueError):
            validate_model_output({"is_circular": "true", "reason": "", "fields": {}}, [], "fake")

    def test_all_mandatory_languages_configured(self):
        self.assertEqual([language_config(k)["ocr"] for k in ["hi", "kn", "ur", "ar"]], ["hin", "kan", "urd", "ara"])
        self.assertEqual(language_config("ur")["direction"], "rtl")

    def test_judge_cannot_confirm_and_is_cached(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(Path(tmp) / "test.sqlite")
            try:
                sample = Path(__file__).resolve().parents[1] / "examples/school.txt"
                doc = store.ingest(sample)["document"]
                report = {k: {"verdict": "supported", "reason": "Supported by source"} for k in LABELS}
                with patch("document_processing.judge.assess", return_value=report) as fake:
                    first = store.judge(doc["id"], "fake-judge")
                    second = store.judge(doc["id"], "fake-judge")
                    self.assertEqual(fake.call_count, 1)
                self.assertFalse(first["cache_hit"])
                self.assertTrue(second["cache_hit"])
                self.assertEqual(store.get(doc["id"])["state"], "needs_review")
                self.assertEqual(store.conn.execute("SELECT COUNT(*) FROM notices").fetchone()[0], 0)
            finally:
                store.close()

    def test_invalid_judge_verdict_rejected(self):
        report = {k: {"verdict": "auto_approve", "reason": "Trust me"} for k in LABELS}
        with self.assertRaises(ValueError):
            validate_report(report)

    def test_model_failure_reuses_reading_checkpoint(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(Path(tmp) / "test.sqlite")
            try:
                sample = Path(__file__).resolve().parents[1] / "examples/school.txt"
                with patch("document_processing.store.request_extraction", side_effect=ValueError("Service down")):
                    doc = store.ingest(sample, provider="groq")["document"]
                self.assertEqual(doc["state"], "failed")
                self.assertIsNotNone(doc["pages"])
                with patch("document_processing.store.read_source", side_effect=AssertionError("Do not repeat reading")), patch(
                        "document_processing.store.request_extraction", side_effect=ValueError("Still down")):
                    self.assertEqual(store.process(doc["id"])["state"], "failed")
            finally:
                store.close()


if __name__ == "__main__":
    unittest.main()
