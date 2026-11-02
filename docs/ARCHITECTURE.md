# Modules and contracts

| Module | Responsibility | External calls |
|---|---|---|
| `languages.py` | ISO language configuration, OCR codes, text direction | None |
| `ingest.py` + `ocr.mjs` | Local text/PDF reading, PDF rasterization, pretrained OCR | Initial OCR model download only |
| `extract.py` | Conservative labelled Hindi baseline, normalization and typed field rules | None |
| `model.py` | Groq transport and strict proposal contract; verbatim evidence validation | Explicit extractor calls |
| `validation.py` | Compare OCR and PDF text-layer readings for dates/references | None |
| `judge.py` | Independent advisory review contract and verdict validation | Explicit judge calls |
| `translate.py` | Experimental English proposal, source invariants and ASCII review rules | Explicit translation calls |
| `store.py` | SQLite source of truth, jobs, checkpoints, caches, revisions and export | Orchestrates explicit adapters |
| `cli.py`, `app.py` | Command-line and local visual review workflows | Through service methods only |

The parser returns page strings plus reader metadata. Primary and alternate text-layer readings remain distinct. Model candidates contain a raw quotation, page number, and reading identity. The grounding step checks exact source presence and computes offsets itself. Deterministic normalization never alters the raw proposal. Human corrections are append-only and override proposals. Confirmation is a separate transactional action. A judge report is not a correction or approval.

Current schema is the common circular contract; the target connector can map it to an existing DB schema later. New fields require a versioned contract, corresponding storage migration, annotations, and evaluation. English translation is a separate optional stage after original-language confirmation. `english_translations` keeps immutable proposals and reviewed values linked to the source revision. `translation_judge_reports` retains advisory assessments of each translation snapshot. Changing the source invalidates prior English export eligibility. The reviewer explicitly handles non-ASCII identifiers; the model does not rewrite them.

## Persistence and failure behavior

`documents` owns source bytes, fingerprints, durable job states, and readings. `processing_options` pins document language/provider/model/OCR mode. `model_cache` retains raw valid JSON responses keyed by reading, language, model, prompt and schema versions; deterministic revalidation can reuse the response. `corrections`, `review_events`, and `judge_reports` retain decisions and snapshots. `notices` is the current confirmed business table. Exports are derived outputs.

States: received → processing → needs_review → confirmed, with failed and rejected branches. Reprocessing preserves correction history, removes current confirmation eligibility, and requires fresh review. Checkpointed readings avoid repeated OCR after extractor failure. Explicit refresh-reading repeats OCR; there is no per-page crash checkpoint yet.

SHA-256 uniqueness prevents byte-identical duplicates within one database, not semantic duplicate circulars with changed scans. Transactional compare-and-swap revisions reject stale edits. Atomic file replacement preserves the previous export if replacement fails. SQLite operations use a 10-second lock timeout. One active local operator is the supported deployment model; this is not a distributed queue or an exactly-once inference guarantee.

Groq calls have 45-second request timeouts and at most three attempts for transient HTTP errors/rate limits, with bounded backoff. Connection timeouts are not silently retried because billing may already have occurred. Explicit retries can repeat a billed inference if the process died before the result was cached. Documents are not sent elsewhere as a fallback.

## Privacy and deployment boundary

The demo binds to 127.0.0.1 and has no authentication. Do not expose it to a network as a production service. SQLite and uploads are unencrypted at application level; OS access controls and device encryption are the operator's responsibility. Keys are read from the process environment; no key is stored in records. The supplied private key directory is not embedded in repository code.

Uploaded text, model output and judge reports are untrusted data. They cannot execute tools, SQL or shell commands. SQL values are parameterized. Source HTML is escaped before display. Local OCR may download public language models on first use, but it does not upload the document. Groq receives complete selected page readings only after explicitly choosing its extractor or judge.
