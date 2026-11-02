# Fixed capability and production qualification

## Capability

Reliably convert real multilingual circulars of different domains into a validated record schema, with source-linked proposals, honest abstention, reviewable corrections, and durable persistence. Language, reader/OCR, extractor, validator, judge, romanizer, and persistence adapter remain replaceable modules. Additional techniques must improve this workflow and be evaluated against a simpler baseline.

The core scope is extraction and persistence, not a general chatbot or an automatic interpreter of legal obligations. Hindi is the first qualification track. Kannada, Urdu, and Arabic are mandatory subsequent tracks. Romanization is a separate experimental output track; it must never replace original-script records or be represented as English translation.

## Milestones

1. Runnable local workflow, offline baseline, configurable languages, model adapter, evidence validation, SQLite, corrections, judge integration, demo, and reproducible tests.
2. Real Hindi development corpus across at least three domains and five issuers. Annotate from original pages; analyze text-layer and OCR errors separately. Improve field recall without weakening evidence rules.
3. Freeze prompts and normalization rules. Evaluate an issuer-separated held-out Hindi set. Repeat independently for Kannada, Urdu, and Arabic with native-language reviewers.
4. Qualify each language and input-quality slice. Harden deployment for a named operating environment. Qualify romanization separately only if the target database needs it.

## Proposed release gates, fixed before a held-out run

These are acceptance targets, not achieved results or auto-approval thresholds.

- At least 50 actual held-out circulars per language, from at least five issuers and three domains, with clean digital and scanned slices reported separately. Increase this sample when error bounds are too wide for the intended workflow.
- Native-language annotations checked by two reviewers, adjudicated disagreements, clear absent/ambiguous labels, source regions or passages, and issuer separation from development data.
- Non-null field precision at least 98% and recall at least 90%, with field/slice counts and confidence intervals reported. Null outputs do not count as successful extraction of present facts. Lower-performing fields remain explicitly unsupported or review-only.
- Every accepted machine proposal has mechanically valid evidence links. Measure semantic support against original pages separately; substring matching alone is insufficient.
- No invalid date or unresolved flagged conflict can be confirmed without an explicit review decision. No invented missing date or repaired OCR identifier is silently accepted.
- Duplicate/retry, restart, stale edit, database persistence, correction preservation, and failed-export acceptance tests pass. A clean installation reproduces the supported demo.
- Judge error-detection recall and false-alarm rate measured on real outputs and deliberate corruptions. Compare extractor alone, local validators, and local validators plus judge. Native-language reviewer decisions remain the reference.
- Measure real latency, token use, and cost per document before choosing a model/routing policy. No automatic model fallback that changes privacy or cost assumptions.
- Romanization: native review of names, organizations, abbreviations, numerals, punctuation, and mixed-script references; ASCII checks; collision analysis; round-trip identifiers preserved. It remains experimental until separate targets are agreed and met.

No current milestone satisfies these production gates. Do not relabel synthetic regression accuracy or one adapted development document as production accuracy.
