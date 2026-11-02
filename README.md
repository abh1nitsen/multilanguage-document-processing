# Multilanguage Document Processing

Read multilingual circulars, extract evidence-backed records, review uncertain fields, and persist confirmed data in SQLite. This is a working development demo, not a production-qualified document service.

**The capability we are proving:** reliable, schema-constrained extraction from real multilingual circulars, with traceable evidence, explicit abstention, human correction, and durable database persistence.

Initial users are staff tracking circulars across institutions. Examples include school instructions, municipal service disruptions, scholarship deadlines, and administrative policy changes. The extraction contract is shared across domains; it does not encode one institution's templates.

## What runs locally

- UTF-8 text reading and PDF text extraction.
- PDF rasterization and pretrained Tesseract OCR through Node/WebAssembly.
- Language configuration: Hindi (`hi`), Kannada (`kn`), Urdu (`ur`), Arabic (`ar`), each with English OCR enabled for mixed-script identifiers.
- Date normalization, evidence substring checks, OCR/text-layer disagreement checks, review rules, duplicate detection, SQLite transactions, and JSON exports.
- An optional experimental English translation adapter, with separate review and ASCII-only export.

Only the explicit Groq extractor, optional LLM judge, and requested English translation transmit text to an external service. A key does not enable them automatically. The Hindi labelled-field baseline makes no model calls. Configured languages have not all been quality-qualified.

## Quick start

Prerequisites: Python 3.12+ (tested on 3.12), Node.js for OCR. From this repository's directory:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[demo]"
python tools/install_ocr.py
python -m streamlit run app.py --server.address 127.0.0.1
```

Open http://127.0.0.1:8501. Stop with Ctrl+C. Restart with the same command; records remain on disk. First OCR use downloads the selected language and English models. Later use reads the local cache. Extraction is CPU-based; no GPU is required.

The OCR installer uses npm when available; otherwise it downloads an integrity-checked npm package from the official registry into ignored local storage. `npm ci --ignore-scripts` is the equivalent command when npm is already on PATH. The fallback installer requires Python 3.12+ and a Node version compatible with npm 12.

`requirements-lock.txt` captures the verified Python development environment. For matching versions, install it first with `python -m pip install -r requirements-lock.txt`, then install the project with `python -m pip install -e . --no-deps`.

For a real circular, choose its language and **Groq** in the sidebar. Set `GROQ_API_KEY` privately in the launching process. Alternatively, the Windows launcher accepts a private directory containing `groq_key.txt`:

```powershell
.\scripts\start-demo.ps1 -SecretsDir 'C:\private\secrets'
```

The launcher never copies keys into the repository. `.env.example` is a reference only; the application does not automatically load `.env`. Hugging Face credentials are not used by this version because the current OCR can run locally without authenticated HF inference.

## Demo workflow

1. Upload an actual circular as PDF, PNG, JPEG, or UTF-8 text. The default upload limit is 15 MiB; PDFs are limited to 30 pages.
2. Inspect the original document beside extracted fields and open field evidence. For damaged PDF text layers, select OCR **always** before upload. Auto OCR handles pages with no text layer; it cannot detect every corrupt text layer.
3. Review missing values, invalid dates, conflicting readings, and unsupported evidence. Correct a field with a reviewer and reason. Optional fields can be explicitly cleared; issuer and subject are required.
4. Optionally ask the separate LLM judge. Its report is advisory and tied to a document revision. It cannot confirm, correct, or write a business record.
5. Confirm after checking the source. Only confirmed records enter the `notices` table and exports. Reopening removes a record from current confirmed exports while keeping its corrections and review events.
6. Download original-script JSON. If an English-only target is needed, request an English translation, review it against the original, explicitly decide any ASCII reference rendering, and confirm the English record before exporting. The optional judge can assess English meaning against original source readings; it cannot approve translations.

For a completely offline text regression walkthrough, choose Hindi + baseline and upload `examples/school.txt`. These examples are authored synthetic tests, not production evidence.

## CLI

```powershell
document-processing ingest examples/school.txt
document-processing list
document-processing show DOCUMENT_ID
document-processing ingest data/real/circular.pdf --language hi --provider groq --ocr always
document-processing correct DOCUMENT_ID --revision 1 --field issued_date --value 2021-10-07 --reason "Checked original PDF" --reviewer reviewer-name
document-processing judge DOCUMENT_ID
document-processing confirm DOCUMENT_ID --revision 2 --reviewer reviewer-name
document-processing export data/exports/confirmed.json
document-processing translate DOCUMENT_ID
document-processing judge DOCUMENT_ID --english
document-processing export data/exports/english.json --profile english-reviewed
```

Use the actual ID and latest revision from `show`; sample revision numbers are illustrative. Global `--db PATH` precedes the command. `retry ID` handles failed work. After verifying an interrupted process has stopped, use `recover ID --worker-stopped`, then `retry ID`. `reprocess ID --revision N` reruns extraction while retaining corrections; `--refresh-reading` explicitly repeats local reading/OCR. Duplicate bytes return the existing document with its original settings, even if uploaded under another name or language. Use a separate evaluation database for approach comparisons.

## Data contract and storage

`data/notices.sqlite` is the default source of truth. Override with CLI `--db` or demo `CIRCULAR_DB`. It retains original source bytes, page readings, raw model proposals, normalized values, correction history, judge reports, and confirmed records. No background worker is required; the launching process must stay running during a job. A stopped demo remains offline until restarted.

Confirmed fields are `document_id`, `issuer`, `subject`, `reference_number`, `issued_date`, `deadline`, `event_date`, `location`, `required_action`, `confirmed_by`, `confirmed_at`, and `revision`. Dates are ISO `YYYY-MM-DD`; numeric source dates use documented DD/MM/YYYY. Relative dates and unrecognized month names require review. Original scripts are retained. English translations preserve meaning, not pronunciation. Original proposals remain immutable; reviewed English renderings live in a separate table linked to the original document and revision. Dates are copied locally and source identifiers are never machine-translated. A reviewer must explicitly provide an ASCII reference rendering when needed. English export blocks missing, unreviewed, stale or non-ASCII renderings; it never silently drops unmatched records.

## Verification

```powershell
python -m unittest discover -s tests -v
python tools/evaluate.py
```

See [validation results](docs/VALIDATION.md), [architecture and guarantees](docs/ARCHITECTURE.md), and [the fixed capability and release gates](docs/CHARTER.md). Eight synthetic text cases are regression checks only. Real-document qualification uses actual circulars and separately prepared annotations; the development sample is not a held-out benchmark.

## Limitations

This milestone is a local, single-operator demo. It has no application login, distributed worker, scheduled monitoring, production service isolation, calibrated confidence, autonomous approval, or remote database connector. It uses one circular per file and a bounded common field schema; multiple events, attachments, arbitrary target schemas, handwriting, and very long circulars need further work. Groq extraction/judging limits combined readings to 24,000 characters. OCR grounding proves agreement with a reading, not correctness against the original pixels. Human visual review remains necessary.

Kannada, Urdu, Arabic, and English translation require native-speaker assessment before production use. The current judge can share errors with the extractor or translator and cannot inspect source images. Do not use its report as ground truth. Translation remains experimental: fluency does not establish faithfulness.

Keys, uploaded files, databases, logs, language caches, and exports are excluded from Git. Use only authorized data. Public source documents are fetched locally from publisher URLs, not redistributed in this repository. See NOTICE for component attribution.
