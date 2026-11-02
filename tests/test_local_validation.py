import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

from document_processing.extract import extract, normalize_date
from document_processing.store import Store
from document_processing.validation import crosscheck_readings


class LocalChecks(unittest.TestCase):
    def test_conflicting_ocr_identifier_is_withheld(self):
        proposal = extract(["कार्यालय: उदाहरण परिषद\nविषय: बैठक\nक्रमांक: OCR/2024/44\n"])
        result = crosscheck_readings(proposal, {"alternate_text_pages": ["क्रमांक: OCR/2021/11"]})
        self.assertIsNone(result["fields"]["reference_number"]["value"])
        self.assertTrue(result["fields"]["reference_number"]["issues"])

    def test_arabic_and_kannada_numerals(self):
        self.assertEqual(normalize_date("٠٧/١٠/٢٠٢١"), "2021-10-07")
        self.assertEqual(normalize_date("೦೭/೧೦/೨೦೨೧"), "2021-10-07")

    def test_reprocessing_preserves_corrections(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(Path(tmp) / "records.sqlite")
            try:
                source = Path(__file__).resolve().parents[1] / "examples/school.txt"
                doc = store.ingest(source)["document"]
                doc = store.correct(doc["id"], "location", "विशेष कक्ष", "Inspected source", "tester", doc["revision"])
                doc = store.reprocess(doc["id"], doc["revision"])
                self.assertEqual(doc["effective"]["location"], "विशेष कक्ष")
            finally:
                store.close()
