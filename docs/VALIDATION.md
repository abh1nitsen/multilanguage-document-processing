# Validation record

Development environment: Windows, Python 3.12.10, Node 24.22.1. Initial development date: 2026-11-02.

## Synthetic regressions

The initial labelled baseline passed eight hand-authored text cases: school, municipal, scholarship, missing/invalid dates, duplicate labels, irrelevant prose, unsupported free-form notice and adversarial instructions. Initial results: 8/8 admission decisions, 26/26 non-null gold fields, 14/14 nulls and 29/29 evidence offsets. These small synthetic checks measure regression behavior only. A realistic free-form notice is deliberately rejected by the labelled baseline; this illustrates its limited coverage.

The software suite passed 21 tests, including an actual Streamlit confirmation-form interaction, persistence, byte duplicates, restart, correction history, stale edits, invalid dates, chronology, unsupported/empty inputs, OCR failure and retry, interrupted-job recovery, failed exports, model schema violations, ungrounded evidence, language configuration, judge caching/authority boundaries, and experimental ASCII export checks for all four scripts.

## Actual circular development check

Source: CBSE, “सीडब्ल्यूएसएन अभ्यर्थियों और उन्हें दी जाने वाली सुविधाओं के संबंध में स्पष्टीकरण”, dated 07.10.2021:
https://www.cbse.gov.in/cbsenew/documents/Clarification_reg_CWSN_HINDI.pdf

The original was downloaded into ignored local storage, rendered, and visually inspected. Local OCR, Groq extraction using `openai/gpt-oss-120b`, and a separate Groq judge using `qwen/qwen3.8-27b` completed with authenticated API access.

Important observed failures: OCR read `07.10.2021` as `07.40.2024` and corrupted the reference identifier. The date parser withheld the invalid date. The first judge called the corrupted reference supported because it matched OCR. Subsequent development added dual readings and local OCR/text-layer disagreement checks; questionable reference values were withheld. Required-action extraction also failed exact evidence validation. Safe abstention prevented confirmation of those unresolved fields, but recall remains inadequate.

This document was used to debug the system. It is a development case, not held-out evidence, and its judge verdicts are not gold labels. Kannada, Urdu, Arabic, larger Hindi corpora, production latency/cost, judge reliability and romanization quality remain unqualified.

The updated dual-reading judge marked the issue date uncertain. A visual development review of the original page corrected three fields (reference, issue date, required action), confirmed one record, and produced both original-script and experimental ASCII JSON. Closing and reopening the SQLite connection preserved the corrected 2021-10-07 issue date. The ASCII export contained only ASCII characters while the source record retained Hindi. This demonstrates the review workflow; it does not demonstrate adequate automatic extraction recall.

## Current verification boundaries

Configured: four language profiles; strict Groq extraction; independent optional judge; local OCR and database workflow. Verified end-to-end on a real Hindi PDF: reading, evidence validation, saved review state and judge report. Native-language evaluation and production gates are still outstanding. HF inference is deliberately not integrated because there is no evaluated quality need for it yet.
