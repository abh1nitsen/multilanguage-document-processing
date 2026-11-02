import importlib.util
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from document_processing.store import Store


@unittest.skipUnless(importlib.util.find_spec("streamlit"), "optional demo is not installed")
class DemoTests(unittest.TestCase):
    def test_english_review_form_saves_separate_rendering(self):
        from streamlit.testing.v1 import AppTest
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "english-demo.sqlite"
            store = Store(db)
            doc = store.ingest(root / "examples/school.txt")["document"]
            doc = store.confirm(doc["id"], "source-reviewer", doc["revision"])
            english = {"issuer": "Adarsh School, Bhopal", "subject": "Parent meeting", "location": "School auditorium",
                "required_action": "Parents should attend at 10 am with the progress report."}
            with patch("document_processing.translate.propose", return_value=english):
                store.translate(doc["id"], "fake")
            store.close()
            with patch.dict(os.environ, {"CIRCULAR_DB": str(db)}):
                at = AppTest.from_file(str(root / "app.py")).run(timeout=30)
                self.assertFalse(at.exception)
                next(w for w in at.text_input if w.label == "English reviewer identifier (ASCII)").set_value("english-reviewer")
                next(w for w in at.text_input if w.label == "English review reason, including identifier decisions").set_value("Checked original meaning; reference retained")
                next(w for w in at.checkbox if "I checked the English" in w.label).check()
                next(w for w in at.button if w.label == "Confirm English rendering").click().run(timeout=30)
                self.assertFalse(at.exception)
            store = Store(db)
            try:
                self.assertEqual(store.english(doc["id"])["state"], "confirmed")
                self.assertEqual(store.get(doc["id"])["effective"]["subject"], "अभिभावक बैठक")
            finally:
                store.close()

    def test_confirmation_form_and_saved_state(self):
        from streamlit.testing.v1 import AppTest
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "demo.sqlite"
            store = Store(db)
            doc = store.ingest(root / "examples/school.txt")["document"]
            store.close()
            with patch.dict(os.environ, {"CIRCULAR_DB": str(db)}):
                at = AppTest.from_file(str(root / "app.py")).run(timeout=30)
                self.assertFalse(at.exception)
                reviewer = next(w for w in at.text_input if w.label == "Confirming reviewer")
                reviewer.set_value("UI reviewer")
                acknowledgement = next(w for w in at.checkbox if "I checked" in w.label)
                acknowledgement.check()
                confirm = next(w for w in at.button if w.label == "Confirm record")
                confirm.click().run(timeout=30)
                self.assertFalse(at.exception)
            store = Store(db)
            try:
                self.assertEqual(store.get(doc["id"])["state"], "confirmed")
                self.assertEqual(len(store.get(doc["id"])["review_events"]), 1)
            finally:
                store.close()
