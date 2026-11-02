"""Hand-authored gold values; synthetic regression results, not real-world accuracy."""
import json
from pathlib import Path
from document_processing.extract import extract

root = Path(__file__).resolve().parents[1]
cases = json.loads((root / "evaluation/gold.json").read_text(encoding="utf-8"))
correct_admission = tp = predicted = expected = evidence_ok = evidence_total = null_correct = null_total = 0
for case in cases:
    text = (root / case["file"]).read_text(encoding="utf-8") if "file" in case else case["text"]
    result = extract([text])
    correct_admission += result["admitted"] == case["admitted"]
    for field, gold in case.get("expected", {}).items():
        output = result["fields"][field]
        guess = output["value"]
        predicted += guess is not None
        expected += gold is not None
        tp += guess is not None and guess == gold
        if gold is None:
            null_total += 1
            null_correct += guess is None
        for candidate in output["candidates"]:
            e = candidate["evidence"]
            evidence_total += 1
            evidence_ok += text[e["start"]:e["end"]] == e["quote"]
report = {"dataset": "8 authored synthetic text cases; no real-world accuracy claim",
          "admission_correct": correct_admission, "admission_total": len(cases),
          "non_null_field_precision": tp / predicted if predicted else 0,
          "non_null_field_recall": tp / expected if expected else 0,
          "correct_non_null_fields": tp, "gold_non_null_fields": expected,
          "correct_nulls": null_correct, "gold_nulls": null_total,
          "evidence_offsets_correct": evidence_ok, "evidence_offsets_total": evidence_total}
print(json.dumps(report, indent=2))
raise SystemExit(0 if correct_admission == len(cases) and tp == expected == predicted and evidence_ok == evidence_total and null_correct == null_total else 1)
