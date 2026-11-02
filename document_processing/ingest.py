import json
import subprocess
from pathlib import Path
from .languages import language_config

MAX_BYTES = 15 * 1024 * 1024
MAX_PAGES = 30
MAX_CHARACTERS = 150_000


def ocr_image(path, cache, language="hi"):
    config = language_config(language)
    worker = Path(__file__).with_name("ocr.mjs")
    try:
        completed = subprocess.run(["node", str(worker), str(path), str(cache), config["ocr"]],
            capture_output=True, encoding="utf-8", timeout=120, check=False)
    except FileNotFoundError as exc:
        raise ValueError("Image OCR requires Node.js and npm ci in the project directory.") from exc
    except subprocess.TimeoutExpired as exc:
        raise ValueError("OCR timed out after 120 seconds. Retry with a smaller, clearer image.") from exc
    if completed.returncode:
        raise ValueError("OCR failed. Run npm ci; first use needs internet to download configured language models. Check image validity.")
    try:
        data = json.loads(completed.stdout)
        text, score = data["text"], data["recognition_score"]
        if not isinstance(text, str) or not isinstance(score, (int, float)):
            raise ValueError("Invalid OCR response.")
    except (json.JSONDecodeError, KeyError) as exc:
        raise ValueError("OCR returned an invalid response; no proposal was saved.") from exc
    return text, {"reader": data["engine"], "recognition_score": score}


def read_source(path, cache, language="hi", ocr_mode="auto"):
    config = language_config(language)
    if ocr_mode not in {"auto", "always", "never"}:
        raise ValueError("OCR mode must be auto, always or never.")
    path = Path(path).resolve()
    size = path.stat().st_size
    if size == 0 or size > MAX_BYTES:
        raise ValueError("Provide a non-empty file no larger than 15 MiB.")
    suffix = path.suffix.lower()
    metadata = {"reader": "utf8", "recognition_score": None}
    if suffix == ".txt":
        pages = [path.read_text(encoding="utf-8-sig")]
    elif suffix == ".pdf":
        from pypdf import PdfReader
        try:
            reader = PdfReader(path)
            if reader.is_encrypted:
                raise ValueError("Encrypted PDFs are unsupported. Export an unlocked copy.")
            if len(reader.pages) > MAX_PAGES:
                raise ValueError("Split PDFs into documents of at most 30 pages.")
            pages = [p.extract_text() or "" for p in reader.pages]
        except ValueError:
            raise
        except Exception as exc:
            raise ValueError("PDF could not be read. Try a text or image export.") from exc
        metadata["reader"] = "pypdf-text-layer"
        if ocr_mode == "always" or any(not p.strip() for p in pages):
            if ocr_mode == "never":
                raise ValueError("A PDF page has no text layer. Enable local OCR.")
            import pypdfium2 as pdfium
            import tempfile
            scores = []
            alternate_pages = list(pages)
            with pdfium.PdfDocument(path) as pdf:
                for index in range(len(pages)):
                    if ocr_mode != "always" and pages[index].strip():
                        continue
                    with tempfile.TemporaryDirectory() as tmp:
                        image_path = Path(tmp) / "page.png"
                        page = pdf[index]
                        width, height = page.get_size()
                        scale = min(2.5, (20_000_000 / (width * height)) ** 0.5)
                        bitmap = page.render(scale=scale)
                        image = bitmap.to_pil()
                        image.save(image_path)
                        image.close()
                        bitmap.close()
                        page.close()
                        pages[index], info = ocr_image(image_path, cache, language)
                        scores.append(info["recognition_score"])
            metadata = {"reader": "pdfium+tesseract.js", "recognition_score": None,
                        "ocr_page_scores": scores, "ocr_mode": ocr_mode,
                        "alternate_text_pages": alternate_pages}
    elif suffix in {".png", ".jpg", ".jpeg"}:
        if ocr_mode == "never":
            raise ValueError("Image files require local OCR. Enable OCR.")
        text, metadata = ocr_image(path, cache, language)
        pages = [text]
    else:
        raise ValueError("Supported formats: UTF-8 .txt, text-layer .pdf, .png, .jpg, .jpeg.")
    if not any(p.strip() for p in pages):
        raise ValueError("No readable text found. Supply a clearer document.")
    if sum(map(len, pages)) > MAX_CHARACTERS:
        raise ValueError("Extracted text exceeds 150,000 characters. Split the document.")
    return pages, metadata
