"""Local, deterministic checks after extraction; no provider calls."""
import re
import unicodedata


def locate_quote(quote, source):
    """Find verbatim text with whitespace-only tolerance; never repair spelling."""
    start = source.find(quote)
    if start >= 0:
        return start, start + len(quote)
    words = quote.split()
    if not words:
        return None
    match = re.search(r"\s+".join(re.escape(word) for word in words), source)
    return match.span() if match else None


def comparable(text):
    normalized = unicodedata.normalize("NFC", text)
    return re.sub(r"\s+", "", normalized)


def crosscheck_readings(proposal, reader):
    alternate = reader.get("alternate_text_pages", [])
    if not alternate:
        return proposal
    for key in ["reference_number", "issued_date", "deadline", "event_date"]:
        field = proposal["fields"][key]
        for candidate in field["candidates"]:
            page = candidate["evidence"]["page"] - 1
            if page < len(alternate) and alternate[page].strip() and comparable(candidate["raw"]) not in comparable(alternate[page]):
                field["issues"].append("OCR and PDF text-layer readings disagree. Inspect the original and explicitly correct or clear this field.")
                field["value"] = None
                break
    return proposal
