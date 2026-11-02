import hashlib
import json
import os
import sqlite3
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path

from .extract import LABELS, VERSION, extract, validate_record, validate_value
from .ingest import read_source, MAX_BYTES
from .languages import language_config
from .model import DEFAULT_MODEL, PROMPT_VERSION, cache_key, request_extraction, validate_model_output
from . import judge
from .validation import crosscheck_readings


def dumps(value):
    return json.dumps(value, ensure_ascii=False, indent=2)


def now():
    return datetime.now(timezone.utc).isoformat()


class Store:
    def __init__(self, db="data/notices.sqlite"):
        self.path = Path(db)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path, timeout=10)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.executescript("""
        CREATE TABLE IF NOT EXISTS documents (
          id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL UNIQUE, source_name TEXT NOT NULL,
          source_bytes BLOB NOT NULL, submitted_at TEXT NOT NULL, pages_json TEXT,
          reader_json TEXT, proposal_json TEXT, extractor_version TEXT NOT NULL,
          state TEXT NOT NULL CHECK(state IN ('received','processing','failed','rejected','needs_review','confirmed')),
          error TEXT, revision INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS processing_options (
          document_id TEXT PRIMARY KEY REFERENCES documents(id), language TEXT NOT NULL,
          provider TEXT NOT NULL, model TEXT NOT NULL, ocr_mode TEXT NOT NULL DEFAULT 'auto');
        CREATE TABLE IF NOT EXISTS model_cache (
          cache_key TEXT PRIMARY KEY, raw_json TEXT NOT NULL, created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS judge_reports (
          id INTEGER PRIMARY KEY, document_id TEXT NOT NULL REFERENCES documents(id),
          revision INTEGER NOT NULL, model TEXT NOT NULL, judge_version TEXT NOT NULL,
          report_json TEXT NOT NULL, values_json TEXT NOT NULL, created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS review_events (
          id INTEGER PRIMARY KEY, document_id TEXT NOT NULL REFERENCES documents(id),
          event TEXT NOT NULL, reviewer TEXT NOT NULL, revision INTEGER NOT NULL,
          values_json TEXT NOT NULL, created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS corrections (
          id INTEGER PRIMARY KEY, document_id TEXT NOT NULL REFERENCES documents(id),
          field TEXT NOT NULL, value_json TEXT NOT NULL, reason TEXT NOT NULL,
          reviewer TEXT NOT NULL, created_at TEXT NOT NULL, revision INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS notices (
          document_id TEXT PRIMARY KEY REFERENCES documents(id),
          issuer TEXT NOT NULL, subject TEXT NOT NULL, reference_number TEXT,
          issued_date TEXT NOT NULL, deadline TEXT, event_date TEXT, location TEXT,
          required_action TEXT NOT NULL, confirmed_by TEXT NOT NULL,
          confirmed_at TEXT NOT NULL, revision INTEGER NOT NULL);
        """)
        columns = {r[1] for r in self.conn.execute("PRAGMA table_info(processing_options)")}
        if "ocr_mode" not in columns:
            self.conn.execute("ALTER TABLE processing_options ADD COLUMN ocr_mode TEXT NOT NULL DEFAULT 'auto'")

    def close(self):
        self.conn.close()

    def get(self, doc_id):
        row = self.conn.execute("SELECT * FROM documents WHERE id=?", (doc_id,)).fetchone()
        if row is None:
            raise ValueError("Document not found.")
        doc = {k: row[k] for k in row.keys() if k != "source_bytes"}
        for key in ["pages_json", "reader_json", "proposal_json"]:
            raw = doc.pop(key)
            doc[key[:-5]] = json.loads(raw) if raw else None
        options = self.conn.execute("SELECT language,provider,model,ocr_mode FROM processing_options WHERE document_id=?", (doc_id,)).fetchone()
        doc["processing_options"] = dict(options) if options else {"language": "hi", "provider": "baseline", "model": "", "ocr_mode": "auto"}
        corrections = [dict(r) for r in self.conn.execute(
            "SELECT * FROM corrections WHERE document_id=? ORDER BY id", (doc_id,))]
        effective = {k: v["value"] for k, v in (doc["proposal"] or {}).get("fields", {}).items()}
        for correction in corrections:
            effective[correction["field"]] = json.loads(correction["value_json"])
        doc.update(effective=effective, corrections=corrections)
        doc["judge_reports"] = [dict(r) for r in self.conn.execute(
            "SELECT * FROM judge_reports WHERE document_id=? ORDER BY id DESC", (doc_id,))]
        doc["review_events"] = [dict(r) for r in self.conn.execute(
            "SELECT * FROM review_events WHERE document_id=? ORDER BY id", (doc_id,))]
        return doc

    def ingest(self, path, language="hi", provider="baseline", model=DEFAULT_MODEL, ocr_mode="auto"):
        language_config(language)
        if provider not in {"baseline", "groq"}:
            raise ValueError("Provider must be baseline or groq.")
        if provider == "baseline" and language != "hi":
            raise ValueError("The labelled baseline is Hindi-only. Select Groq for Kannada, Urdu or Arabic.")
        path = Path(path)
        if path.stat().st_size > MAX_BYTES:
            raise ValueError("File exceeds 15 MiB.")
        raw = path.read_bytes()
        if not raw:
            raise ValueError("File is empty.")
        fingerprint = hashlib.sha256(raw).hexdigest()
        doc_id = str(uuid.uuid4())
        with self.conn:
            self.conn.execute("""INSERT OR IGNORE INTO documents
                (id,fingerprint,source_name,source_bytes,submitted_at,extractor_version,state)
                VALUES (?,?,?,?,?,?,'received')""",
                (doc_id, fingerprint, path.name, raw, now(), VERSION))
            if self.conn.execute("SELECT 1 FROM documents WHERE id=?", (doc_id,)).fetchone():
                self.conn.execute("INSERT INTO processing_options VALUES (?,?,?,?,?)", (doc_id, language, provider, model, ocr_mode))
        existing = self.conn.execute("SELECT id FROM documents WHERE fingerprint=?", (fingerprint,)).fetchone()[0]
        if existing != doc_id:
            return {"duplicate": True, "document": self.get(existing)}
        return {"duplicate": False, "document": self.process(doc_id)}

    def process(self, doc_id):
        # Claim in SQLite, release transaction before expensive OCR. Explicit recovery only.
        with self.conn:
            changed = self.conn.execute("""UPDATE documents SET state='processing',error=NULL
                WHERE id=? AND state IN ('received','failed')""", (doc_id,)).rowcount
        if not changed:
            raise ValueError("Only received/failed documents can retry. Use recover after an interrupted worker has stopped.")
        row = self.conn.execute("SELECT source_name,source_bytes FROM documents WHERE id=?", (doc_id,)).fetchone()
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(suffix=Path(row["source_name"]).suffix, delete=False) as stream:
                temporary = Path(stream.name)
                stream.write(row["source_bytes"])
            options = self.get(doc_id)["processing_options"]
            checkpoint = self.get(doc_id)
            if checkpoint["pages"] is not None:
                pages, reader = checkpoint["pages"], checkpoint["reader"]
            else:
                pages, reader = read_source(temporary, self.path.parent / "ocr-cache", options["language"], options["ocr_mode"])
            # Reading is a durable checkpoint, even if the external extractor fails.
            with self.conn:
                self.conn.execute("UPDATE documents SET pages_json=?,reader_json=? WHERE id=?", (dumps(pages), dumps(reader), doc_id))
            if options["provider"] == "groq":
                alternate = reader.get("alternate_text_pages", [])
                identity = cache_key(pages, options["language"], options["model"], alternate)
                cached = self.conn.execute("SELECT raw_json FROM model_cache WHERE cache_key=?", (identity,)).fetchone()
                if cached:
                    raw_output = json.loads(cached[0])
                else:
                    raw_output = request_extraction(pages, options["language"], options["model"], alternate)
                    with self.conn:
                        self.conn.execute("INSERT OR IGNORE INTO model_cache VALUES (?,?,?)", (identity, dumps(raw_output), now()))
                proposal = validate_model_output(raw_output, pages, options["model"], alternate)
                proposal["cache_hit"] = bool(cached)
            else:
                proposal = extract(pages)
            proposal = crosscheck_readings(proposal, reader)
            state = "needs_review" if proposal["admitted"] else "rejected"
            with self.conn:
                self.conn.execute("""UPDATE documents SET pages_json=?, reader_json=?, proposal_json=?,
                    state=?, extractor_version=?, revision=revision+1 WHERE id=?""",
                    (dumps(pages), dumps(reader), dumps(proposal), state, proposal["extractor_version"], doc_id))
        except Exception as exc:
            with self.conn:
                self.conn.execute("UPDATE documents SET state='failed',error=?,revision=revision+1 WHERE id=?",
                                  (str(exc), doc_id))
        finally:
            if temporary:
                temporary.unlink(missing_ok=True)
        return self.get(doc_id)

    def recover(self, doc_id):
        with self.conn:
            changed = self.conn.execute("""UPDATE documents SET state='failed',error='Interrupted worker reset',
                revision=revision+1 WHERE id=? AND state='processing'""", (doc_id,)).rowcount
        if not changed:
            raise ValueError("Document is not processing.")
        return self.get(doc_id)

    def reprocess(self, doc_id, revision, refresh_reading=False):
        with self.conn:
            changed = self.conn.execute("""UPDATE documents SET state='received',revision=revision+1
                WHERE id=? AND revision=? AND state IN ('needs_review','confirmed','rejected','failed')""",
                (doc_id, revision)).rowcount
            if not changed:
                raise ValueError("Stale revision or document is actively processing.")
            self.conn.execute("DELETE FROM notices WHERE document_id=?", (doc_id,))
            if refresh_reading:
                self.conn.execute("UPDATE documents SET pages_json=NULL,reader_json=NULL WHERE id=?", (doc_id,))
        return self.process(doc_id)

    def correct(self, doc_id, field, value, reason, reviewer, revision):
        if not reason.strip() or not reviewer.strip():
            raise ValueError("Reviewer and correction reason are required.")
        value = validate_value(field, value)
        with self.conn:
            changed = self.conn.execute("""UPDATE documents SET revision=revision+1
                WHERE id=? AND revision=? AND state='needs_review'""", (doc_id, revision)).rowcount
            if not changed:
                raise ValueError("Stale revision or document is not awaiting review. Reload it.")
            self.conn.execute("""INSERT INTO corrections
                (document_id,field,value_json,reason,reviewer,created_at,revision) VALUES (?,?,?,?,?,?,?)""",
                (doc_id, field, dumps(value), reason, reviewer, now(), revision + 1))
        return self.get(doc_id)

    def confirm(self, doc_id, reviewer, revision):
        if not reviewer.strip():
            raise ValueError("Reviewer is required.")
        doc = self.get(doc_id)
        corrected = {c["field"] for c in doc["corrections"]}
        for field, proposal in (doc["proposal"] or {}).get("fields", {}).items():
            if proposal["issues"] and field not in corrected:
                raise ValueError(field + " has unresolved review issues. Correct it or explicitly clear an optional field.")
        values = validate_record(doc["effective"])
        with self.conn:
            changed = self.conn.execute("""UPDATE documents SET state='confirmed',revision=revision+1
                WHERE id=? AND revision=? AND state='needs_review'""", (doc_id, revision)).rowcount
            if not changed:
                raise ValueError("Stale revision or document is not awaiting review. Reload it.")
            fields = list(LABELS)
            columns = ["document_id"] + fields + ["confirmed_by", "confirmed_at", "revision"]
            self.conn.execute("INSERT INTO notices (" + ",".join(columns) + ") VALUES (" +
                ",".join("?" for _ in columns) + ")",
                [doc_id] + [values[f] for f in fields] + [reviewer, now(), revision + 1])
            self.conn.execute("INSERT INTO review_events (document_id,event,reviewer,revision,values_json,created_at) VALUES (?,?,?,?,?,?)",
                (doc_id, "confirmed", reviewer, revision + 1, dumps(values), now()))
        return self.get(doc_id)

    def reopen(self, doc_id, revision):
        snapshot = self.get(doc_id)
        with self.conn:
            changed = self.conn.execute("""UPDATE documents SET state='needs_review',revision=revision+1
                WHERE id=? AND revision=? AND state='confirmed'""", (doc_id, revision)).rowcount
            if not changed:
                raise ValueError("Stale revision or document is not confirmed.")
            self.conn.execute("DELETE FROM notices WHERE document_id=?", (doc_id,))
            self.conn.execute("INSERT INTO review_events (document_id,event,reviewer,revision,values_json,created_at) VALUES (?,?,?,?,?,?)",
                (doc_id, "reopened", "local operator", revision + 1, dumps(snapshot["effective"]), now()))
        return self.get(doc_id)

    def judge(self, doc_id, model=judge.DEFAULT_JUDGE_MODEL):
        doc = self.get(doc_id)
        if doc["state"] not in {"needs_review", "confirmed"}:
            raise ValueError("Judge requires a readable, admitted circular.")
        language = doc["processing_options"]["language"]
        alternate = (doc["reader"] or {}).get("alternate_text_pages", [])
        key = judge.identity(doc["pages"], doc["effective"], language, model, alternate)
        cached = self.conn.execute("SELECT raw_json FROM model_cache WHERE cache_key=?", (key,)).fetchone()
        if cached:
            report = judge.validate_report(json.loads(cached[0]))
        else:
            report = judge.assess(doc["pages"], doc["effective"], language, model, alternate)
            with self.conn:
                self.conn.execute("INSERT OR IGNORE INTO model_cache VALUES (?,?,?)", (key, dumps(report), now()))
        with self.conn:
            # The report stays linked to its original snapshot if another reviewer edited meanwhile.
            self.conn.execute("""INSERT INTO judge_reports
                (document_id,revision,model,judge_version,report_json,values_json,created_at)
                VALUES (?,?,?,?,?,?,?)""", (doc_id, doc["revision"], model, judge.VERSION,
                                            dumps(report), dumps(doc["effective"]), now()))
        return {"document_id": doc_id, "revision": doc["revision"], "advisory_only": True,
                "cache_hit": bool(cached), "model": model, "report": report}

    def list(self):
        return [dict(r) for r in self.conn.execute(
            "SELECT id,source_name,state,revision,submitted_at,error FROM documents ORDER BY submitted_at")]

    def export(self, destination, profile="original"):
        destination = Path(destination)
        if destination.resolve() == self.path.resolve():
            raise ValueError("Export destination cannot be the database.")
        records = [dict(r) for r in self.conn.execute("SELECT * FROM notices ORDER BY confirmed_at,document_id")]
        payload = {"schema_version": "1.0", "exported_at": now(), "records": records}
        if profile == "ascii-experimental":
            from .romanize import transform_record, transformation_metadata
            payload["transformation"] = transformation_metadata()
            payload["records"] = [transform_record(record, self.get(record["document_id"])["processing_options"]["language"]) for record in records]
        elif profile != "original":
            raise ValueError("Export profile must be original or ascii-experimental.")
        destination.parent.mkdir(parents=True, exist_ok=True)
        temp = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=destination.parent,
                                             delete=False, suffix=".tmp") as stream:
                temp = Path(stream.name)
                stream.write(dumps(payload) + "\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp, destination)
        finally:
            if temp:
                temp.unlink(missing_ok=True)
        return {"exported": len(records), "path": str(destination.resolve())}
