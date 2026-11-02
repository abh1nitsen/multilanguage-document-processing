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
from .judge import DEFAULT_JUDGE_MODEL
from .validation import crosscheck_readings
from . import translate
from . import full_text


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
        CREATE TABLE IF NOT EXISTS english_translations (
          id INTEGER PRIMARY KEY, document_id TEXT NOT NULL REFERENCES documents(id),
          source_revision INTEGER NOT NULL, model TEXT NOT NULL, version TEXT NOT NULL,
          proposal_json TEXT NOT NULL, effective_json TEXT NOT NULL,
          state TEXT NOT NULL CHECK(state IN ('needs_review','confirmed')),
          revision INTEGER NOT NULL DEFAULT 0, reviewer TEXT, reason TEXT, reviewed_at TEXT);
        CREATE TABLE IF NOT EXISTS translation_judge_reports (
          id INTEGER PRIMARY KEY, translation_id INTEGER NOT NULL REFERENCES english_translations(id),
          translation_revision INTEGER NOT NULL, model TEXT NOT NULL,
          report_json TEXT NOT NULL, created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS full_text_translations (
          id INTEGER PRIMARY KEY, document_id TEXT NOT NULL REFERENCES documents(id),
          identity TEXT NOT NULL UNIQUE, source_revision INTEGER NOT NULL,
          language TEXT NOT NULL, model TEXT NOT NULL, version TEXT NOT NULL,
          reading TEXT NOT NULL, pages_json TEXT NOT NULL, created_at TEXT NOT NULL);
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

    def judge(self, doc_id, model=DEFAULT_JUDGE_MODEL):
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

    def english(self, doc_id):
        row = self.conn.execute("SELECT * FROM english_translations WHERE document_id=? ORDER BY id DESC LIMIT 1", (doc_id,)).fetchone()
        if row is None:
            return None
        item = dict(row)
        item["proposal"] = json.loads(item.pop("proposal_json"))
        item["effective"] = json.loads(item.pop("effective_json"))
        return item

    def full_english(self, doc_id, model=DEFAULT_MODEL, reading="primary"):
        doc, pages, alternate, key = self._full_translation_input(doc_id, model, reading)
        row = self.conn.execute("SELECT * FROM full_text_translations WHERE document_id=? AND identity=?",
                                (doc_id, key)).fetchone()
        if not row:
            return None
        result = dict(row)
        result["pages"] = json.loads(result.pop("pages_json"))
        result["review_state"] = "unreviewed"
        return result

    def _full_translation_input(self, doc_id, model, reading):
        doc = self.get(doc_id)
        if doc["state"] not in {"needs_review", "confirmed"} or not doc["pages"]:
            raise ValueError("Read an admitted circular before requesting full-document translation.")
        primary = doc["pages"]
        secondary = (doc["reader"] or {}).get("alternate_text_pages", [])
        if reading == "primary":
            pages, alternate = primary, secondary
        elif reading == "text_layer" and len(secondary) == len(primary):
            pages, alternate = secondary, primary
        else:
            raise ValueError("The selected source reading is unavailable.")
        key = full_text.identity([doc_id, full_text.document_key(
            pages, alternate, doc["processing_options"]["language"], model, reading)])
        return doc, pages, alternate, key

    def translate_full_text(self, doc_id, model=DEFAULT_MODEL, reading="primary", progress=None):
        doc, pages, alternate, key = self._full_translation_input(doc_id, model, reading)
        prior = self.full_english(doc_id, model, reading)
        if prior:
            return prior
        chunks = list(full_text.segments(pages))
        results = {i: {"page": i, "source_text": p, "english_text": "", "warnings": [], "chunks": []}
                   for i, p in enumerate(pages, 1)}
        for index, chunk in enumerate(chunks, 1):
            if progress:
                progress(index - 1, len(chunks))
            context = alternate[chunk["page"] - 1] if len(alternate) >= chunk["page"] else ""
            chunk_key = full_text.identity([full_text.VERSION, chunk["text"], context,
                doc["processing_options"]["language"], model])
            cached = self.conn.execute("SELECT raw_json FROM model_cache WHERE cache_key=?", (chunk_key,)).fetchone()
            if not chunk["text"].strip():
                result = {"english_text": "", "warnings": []}
            elif cached:
                result = full_text.validate_result(json.loads(cached[0]), chunk["text"])
            else:
                result = full_text.translate_chunk(chunk["text"], context, doc["processing_options"]["language"], model)
                with self.conn:
                    self.conn.execute("INSERT OR IGNORE INTO model_cache VALUES (?,?,?)", (chunk_key, dumps(result), now()))
            page = results[chunk["page"]]
            page["english_text"] += ("\n\n" if page["english_text"] and result["english_text"] else "") + result["english_text"]
            page["warnings"] = list(dict.fromkeys(page["warnings"] + result["warnings"]))
            page["chunks"].append({"start": chunk["start"], "end": chunk["end"]})
            if progress:
                progress(index, len(chunks))
        with self.conn:
            self.conn.execute("""INSERT OR IGNORE INTO full_text_translations
                (document_id,identity,source_revision,language,model,version,reading,pages_json,created_at)
                VALUES (?,?,?,?,?,?,?,?,?)""", (doc_id, key, doc["revision"], doc["processing_options"]["language"],
                model, full_text.VERSION, reading, dumps(list(results.values())), now()))
        saved = self.full_english(doc_id, model, reading)
        if saved is None:
            raise ValueError("The source reading changed during translation. The completed translation was retained for its original reading; reload the document.")
        return saved

    def translate(self, doc_id, model=DEFAULT_MODEL):
        doc = self.get(doc_id)
        if doc["state"] != "confirmed":
            raise ValueError("Confirm the original-language record before requesting translation.")
        prior = self.english(doc_id)
        if prior and prior["source_revision"] == doc["revision"] and prior["model"] == model:
            return prior
        source = dict(self.conn.execute("SELECT * FROM notices WHERE document_id=?", (doc_id,)).fetchone())
        language = doc["processing_options"]["language"]
        key = translate.identity(source, language, model)
        cached = self.conn.execute("SELECT raw_json FROM model_cache WHERE cache_key=?", (key,)).fetchone()
        raw = translate.validate_proposal(json.loads(cached[0]), source) if cached else translate.propose(source, language, model)
        effective = dict(source)
        effective.update(raw)
        with self.conn:
            if not cached:
                self.conn.execute("INSERT OR IGNORE INTO model_cache VALUES (?,?,?)", (key, dumps(raw), now()))
            self.conn.execute("""INSERT INTO english_translations
                (document_id,source_revision,model,version,proposal_json,effective_json,state)
                VALUES (?,?,?,?,?,?,'needs_review')""", (doc_id, doc["revision"], model, translate.VERSION, dumps(raw), dumps(effective)))
        return self.english(doc_id)

    def confirm_english(self, translation_id, revision, values, reviewer, reason):
        if not reason.strip():
            raise ValueError("English review reason is required, including any identifier rendering decision.")
        row = self.conn.execute("SELECT * FROM english_translations WHERE id=?", (translation_id,)).fetchone()
        if not row:
            raise ValueError("Translation not found.")
        doc = self.get(row["document_id"])
        if doc["state"] != "confirmed" or doc["revision"] != row["source_revision"]:
            raise ValueError("Original record changed; request a translation of the current confirmed revision.")
        source = dict(self.conn.execute("SELECT * FROM notices WHERE document_id=?", (doc["id"],)).fetchone())
        result = translate.reviewed_values(source, values, reviewer)
        result["confirmed_at"] = now()
        with self.conn:
            # The source and translation revision are checked inside the same write transaction.
            changed = self.conn.execute("""UPDATE english_translations SET effective_json=?,state='confirmed',
                revision=revision+1,reviewer=?,reason=?,reviewed_at=?
                WHERE id=? AND revision=? AND state='needs_review' AND EXISTS
                (SELECT 1 FROM documents WHERE id=? AND state='confirmed' AND revision=?)""",
                (dumps(result), reviewer, reason, now(), translation_id, revision, doc["id"], doc["revision"])).rowcount
            if not changed:
                raise ValueError("Stale translation or source revision. Reload it.")
        return self.english(doc["id"])

    def judge_english(self, doc_id, model=DEFAULT_JUDGE_MODEL):
        doc = self.get(doc_id)
        translation = self.english(doc_id)
        if not translation or doc["state"] != "confirmed" or translation["source_revision"] != doc["revision"]:
            raise ValueError("A current English translation of a confirmed source is required.")
        language = doc["processing_options"]["language"]
        alternate = (doc["reader"] or {}).get("alternate_text_pages", [])
        values = {field: translation["effective"].get(field) for field in LABELS}
        key = judge.identity(doc["pages"], values, language, model, alternate)
        cached = self.conn.execute("SELECT raw_json FROM model_cache WHERE cache_key=?", (key,)).fetchone()
        report = judge.validate_report(json.loads(cached[0])) if cached else judge.assess(doc["pages"], values, language, model, alternate)
        with self.conn:
            if not cached:
                self.conn.execute("INSERT OR IGNORE INTO model_cache VALUES (?,?,?)", (key, dumps(report), now()))
            self.conn.execute("""INSERT INTO translation_judge_reports
                (translation_id,translation_revision,model,report_json,created_at) VALUES (?,?,?,?,?)""",
                (translation["id"], translation["revision"], model, dumps(report), now()))
        return {"translation_id": translation["id"], "translation_revision": translation["revision"],
                "advisory_only": True, "report": report}

    def export(self, destination, profile="original"):
        destination = Path(destination)
        if destination.resolve() == self.path.resolve():
            raise ValueError("Export destination cannot be the database.")
        records = [dict(r) for r in self.conn.execute("SELECT * FROM notices ORDER BY confirmed_at,document_id")]
        payload = {"schema_version": "1.0", "exported_at": now(), "records": records}
        if profile == "english-reviewed":
            english_records = []
            for record in records:
                translation = self.english(record["document_id"])
                if not translation or translation["state"] != "confirmed" or translation["source_revision"] != record["revision"]:
                    raise ValueError("Every confirmed source record needs a current reviewed English translation. Export blocked; previous file preserved.")
                translated = dict(translation["effective"])
                translated["source_language"] = self.get(record["document_id"])["processing_options"]["language"]
                translated["source_revision"] = translation["source_revision"]
                english_records.append(translated)
            payload["records"] = english_records
            payload["transformation"] = {"profile": "english-reviewed-experimental", "translation": True,
                "warning": "Experimental English translation; originals and source identifiers remain in the source database. Review identifier renderings explicitly."}
            if not dumps(payload).isascii():
                raise ValueError("English-only export contains unsupported non-ASCII characters.")
        elif profile != "original":
            raise ValueError("Export profile must be original or english-reviewed.")
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
