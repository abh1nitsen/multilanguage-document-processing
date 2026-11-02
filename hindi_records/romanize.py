"""Experimental local Roman-script export. Never alters the original record."""
from functools import lru_cache
from importlib.metadata import version
import unicodedata

LANGUAGE_CODES = {"hi": "hin", "kn": "kan", "ur": "urd", "ar": "ara"}
PUNCTUATION = str.maketrans({"।": ".", "॥": ".", "،": ",", "؛": ";", "؟": "?",
    "“": '"', "”": '"', "‘": "'", "’": "'", "–": "-", "—": "-", "…": "...", "\u00a0": " "})


@lru_cache(maxsize=1)
def engine():
    try:
        from uroman import Uroman
    except ImportError:
        raise ValueError("Experimental ASCII export requires: pip install -e '.[romanization]'") from None
    return Uroman()


def romanize_ascii(text, language):
    if language not in LANGUAGE_CODES:
        raise ValueError("Unsupported romanization language.")
    if text.isascii():
        return text
    result = engine().romanize_string(text, lcode=LANGUAGE_CODES[language]).translate(PUNCTUATION)
    result = "".join(c for c in unicodedata.normalize("NFKD", result) if not unicodedata.combining(c))
    if not result.isascii():
        raise ValueError("Romanization left unsupported non-ASCII characters. Original data was preserved; review before export.")
    if text.strip() and not result.strip():
        raise ValueError("Romanization produced empty text; export blocked.")
    return result


def transform_record(record, language):
    result = {key: romanize_ascii(value, language) if isinstance(value, str) else value
              for key, value in record.items()}
    result["source_language"] = language
    return result


def transformation_metadata():
    engine()
    return {"profile": "ascii-romanized-experimental", "engine": "uroman", "engine_version": version("uroman"),
            "translation": False, "lossy": True,
            "warning": "Experimental romanization; not English translation. Keep original UUID links. Never use romanized names or references as identity keys."}
