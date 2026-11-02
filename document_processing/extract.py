"""Conservative labelled-field baseline; OCR is the pretrained AI component."""
import re
import unicodedata
from datetime import date

VERSION = "labelled-notices-v1"
LABELS = {
    "issuer": ["जारीकर्ता", "कार्यालय", "संस्था", "विभाग"],
    "subject": ["विषय"],
    "reference_number": ["पत्र क्रमांक", "संदर्भ संख्या", "क्रमांक", "पत्रांक"],
    "issued_date": ["जारी दिनांक", "दिनांक"],
    "deadline": ["अंतिम तिथि", "अंतिम तारीख", "आवेदन की अंतिम तिथि"],
    "event_date": ["कार्यक्रम दिनांक", "कार्यक्रम तिथि", "बैठक तिथि"],
    "location": ["स्थान", "कार्यक्रम स्थल"],
    "required_action": ["आवश्यक कार्य", "निर्देश", "कार्यवाही", "आवेदन प्रक्रिया"],
}
REQUIRED = {"issuer", "subject"}
DATE_FIELDS = {"issued_date", "deadline", "event_date"}
DIGITS = str.maketrans("०१२३४५६७८९", "0123456789")
MONTHS = {"जनवरी": 1, "फरवरी": 2, "फ़रवरी": 2, "मार्च": 3, "अप्रैल": 4,
          "मई": 5, "जून": 6, "जुलाई": 7, "अगस्त": 8, "सितंबर": 9,
          "सितम्बर": 9, "अक्टूबर": 10, "नवंबर": 11, "नवम्बर": 11,
          "दिसंबर": 12, "दिसम्बर": 12}


def normalize_date(raw):
    """Numeric dates use documented Indian DD/MM/YYYY, never guessed MDY."""
    text = unicodedata.normalize("NFC", raw).strip()
    text = "".join(str(unicodedata.decimal(c)) if c.isdecimal() else c for c in text)
    iso = re.fullmatch(r"(\d{4})-(\d{1,2})-(\d{1,2})", text)
    numeric = re.fullmatch(r"(\d{1,2})[./-](\d{1,2})[./-](\d{4})", text)
    named = re.fullmatch(r"(\d{1,2})\s+(\S+)\s+(\d{4})", text)
    try:
        if iso:
            year, month, day = map(int, iso.groups())
        elif numeric:
            day, month, year = map(int, numeric.groups())
        elif named and named[2] in MONTHS:
            day, month, year = int(named[1]), MONTHS[named[2]], int(named[3])
        else:
            raise ValueError("Use a full date: DD/MM/YYYY, YYYY-MM-DD, or २ नवंबर २०२६.")
        return date(year, month, day).isoformat()
    except ValueError as exc:
        raise ValueError("Invalid or incomplete date. " + str(exc)) from exc


def validate_value(field, value):
    if field not in LABELS:
        raise ValueError("Unknown field: " + field)
    if value is None:
        if field in REQUIRED:
            raise ValueError(field + " is required.")
        return None
    if not isinstance(value, str) or not value.strip() or len(value) > 2000:
        raise ValueError("Enter non-empty text of at most 2000 characters.")
    value = unicodedata.normalize("NFC", value.strip())
    return normalize_date(value) if field in DATE_FIELDS else value


def extract(pages):
    proposals = {key: [] for key in LABELS}
    hindi_count = sum(len(re.findall(r"[\u0900-\u097f]", p)) for p in pages)
    for page_number, page in enumerate(pages, 1):
        offset = 0
        for line_number, line in enumerate(page.splitlines(keepends=True), 1):
            for field, aliases in LABELS.items():
                pattern = r"^\s*(?:" + "|".join(map(re.escape, aliases)) + r")\s*[:：]\s*(.*?)\s*$"
                match = re.match(pattern, line)
                if match and match[1].strip():
                    start, end = match.span(1)
                    raw = match[1]
                    proposals[field].append({"raw": raw, "evidence": {
                        "page": page_number, "line": line_number,
                        "start": offset + start, "end": offset + end, "quote": raw}})
            offset += len(line)
    fields = {}
    for field, candidates in proposals.items():
        value, issues = None, []
        if not candidates:
            if field in REQUIRED:
                issues.append("Required field was not found; enter it after reviewing the source.")
        elif len(candidates) != 1:
            issues.append("Multiple readings found; select the correct value manually.")
        else:
            try:
                value = validate_value(field, candidates[0]["raw"])
            except ValueError as exc:
                issues.append(str(exc))
        fields[field] = {"value": value, "candidates": candidates, "issues": issues}
    recognized = sum(bool(v) for v in proposals.values())
    admitted = hindi_count >= 10 and recognized >= 2 and bool(proposals["subject"])
    reasons = [] if admitted else ["Expected a Hindi notice with a labelled विषय and at least one other supported field."]
    return {"extractor_version": VERSION, "admitted": admitted,
            "rejection_reasons": reasons, "fields": fields}


def validate_record(values):
    result = {field: validate_value(field, values.get(field)) for field in LABELS}
    if result["issued_date"]:
        for field in ["deadline", "event_date"]:
            if result[field] and result[field] < result["issued_date"]:
                raise ValueError(field + " precedes issued_date; correct the dates before confirming.")
    return result
