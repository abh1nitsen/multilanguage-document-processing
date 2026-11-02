"""Configured languages are not a claim of evaluated extraction accuracy."""
LANGUAGES = {
    "hi": {"name": "Hindi", "ocr": "hin", "direction": "ltr"},
    "kn": {"name": "Kannada", "ocr": "kan", "direction": "ltr"},
    "ur": {"name": "Urdu", "ocr": "urd", "direction": "rtl"},
    "ar": {"name": "Arabic", "ocr": "ara", "direction": "rtl"},
}


def language_config(code):
    if code not in LANGUAGES:
        raise ValueError("Choose a configured language: hi, kn, ur, ar.")
    return LANGUAGES[code]
