import importlib.util
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from hindi_records.store import Store


@unittest.skipUnless(importlib.util.find_spec("streamlit"), "optional demo is not installed")
class DemoTests(unittest.TestCase):
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
