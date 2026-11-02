"""Local review demo. Run: streamlit run app.py --server.address 127.0.0.1"""
import html
import os
import tempfile
from pathlib import Path

import streamlit as st

from document_processing.extract import LABELS, REQUIRED
from document_processing.languages import LANGUAGES
from document_processing.model import DEFAULT_MODEL
from document_processing.judge import DEFAULT_JUDGE_MODEL
from document_processing.translate import TEXT_FIELDS
from document_processing.store import Store, dumps

st.set_page_config(page_title="Multilanguage Document Processing", page_icon="📄", layout="wide")
st.title("Multilanguage Document Processing")
st.caption("Read a circular. Check the evidence. Save a trusted record.")
st.info("Development demo · Hindi validation first · Kannada, Urdu and Arabic configured, not yet quality-qualified")
db = os.getenv("CIRCULAR_DB", "data/notices.sqlite")
store = Store(db)
try:
    with st.sidebar:
        st.subheader("Read a document")
        language = st.selectbox("Document language", list(LANGUAGES), format_func=lambda k: LANGUAGES[k]["name"])
        provider = st.selectbox("Extractor", ["baseline", "groq"], format_func=lambda x: "Groq · evidence extraction" if x == "groq" else "Offline · labelled Hindi baseline")
        if provider == "groq":
            st.caption("Sends extracted text to Groq. The original file stays local. Requires GROQ_API_KEY.")
        model = st.text_input("Groq model", DEFAULT_MODEL) if provider == "groq" else DEFAULT_MODEL
        ocr_mode = st.selectbox("Local OCR", ["auto", "always", "never"],
            help="Auto reads PDF text layers and OCRs scanned pages. Always also replaces faulty PDF text layers with OCR.")
        uploaded = st.file_uploader("Circular", type=["txt", "pdf", "png", "jpg", "jpeg"])
        if st.button("Read circular", type="primary", disabled=uploaded is None):
            temp_path = None
            try:
                with tempfile.NamedTemporaryFile(suffix=Path(uploaded.name).suffix, delete=False) as f:
                    temp_path = Path(f.name)
                    f.write(uploaded.getvalue())
                with st.spinner("Reading and checking source evidence…"):
                    result = store.ingest(temp_path, language, provider, model, ocr_mode)
                st.session_state["selected_id"] = result["document"]["id"]
                if result["duplicate"]:
                    st.warning("This file already exists. Opened its saved record and original processing settings.")
                else:
                    with store.conn:
                        store.conn.execute("UPDATE documents SET source_name=? WHERE id=?", (uploaded.name, result["document"]["id"]))
                st.rerun()
            except (ValueError, OSError) as exc:
                st.error(str(exc))
            finally:
                if temp_path:
                    temp_path.unlink(missing_ok=True)
        st.divider()
        st.caption("SQLite is the source of truth. Exports contain confirmed records only.")
        st.code(str(Path(db).resolve()), language=None)
    records = store.list()
    if not records:
        st.subheader("Your review queue is empty")
        st.write("Upload an actual circular to begin. For an offline regression walkthrough, choose the Hindi baseline and upload examples/school.txt.")
        st.stop()
    options = [r["id"] for r in records]
    names = {r["id"]: f'{r["source_name"]} · {r["state"]}' for r in records}
    selected = st.session_state.get("selected_id", options[-1])
    selected = st.selectbox("Saved documents", options, index=options.index(selected) if selected in options else 0,
                            format_func=lambda x: names[x])
    st.session_state["selected_id"] = selected
    doc = store.get(selected)
    st.subheader(doc["source_name"])
    st.write(f'**Status:** {doc["state"].replace("_", " ")} · **Revision:** {doc["revision"]}')
    settings = doc["processing_options"]
    st.caption(f'Saved settings: {LANGUAGES[settings["language"]]["name"]} · {settings["provider"]} · OCR {settings["ocr_mode"]}')
    if doc["error"]:
        st.error(doc["error"])
    if doc["state"] == "failed" and st.button("Retry reading"):
        with st.spinner("Retrying…"):
            store.process(selected)
        st.rerun()
    if doc["state"] == "rejected":
        st.warning("This document was not admitted as a supported circular. It has not entered confirmed records.")
        st.write((doc["proposal"] or {}).get("rejection_reasons", []))
    left, right = st.columns([1, 1])
    with left:
        st.subheader("Original document")
        original = store.conn.execute("SELECT source_bytes FROM documents WHERE id=?", (selected,)).fetchone()[0]
        if Path(doc["source_name"]).suffix.lower() in {".png", ".jpg", ".jpeg"}:
            st.image(original)
        elif Path(doc["source_name"]).suffix.lower() == ".pdf":
            import pypdfium2 as pdfium
            try:
                with pdfium.PdfDocument(original) as pdf:
                    page_index = st.number_input("Original PDF page", 1, len(pdf), 1, key="preview_" + selected) - 1
                    page = pdf[page_index]
                    bitmap = page.render(scale=1.3)
                    image = bitmap.to_pil()
                    st.image(image)
                    image.close()
                    bitmap.close()
                    page.close()
            except Exception:
                st.warning("Original preview is unavailable. Download the source to inspect it.")
        st.download_button("Download original source", original, file_name=doc["source_name"])
        st.subheader("Source reading")
        direction = LANGUAGES[doc["processing_options"]["language"]]["direction"]
        for page_no, text in enumerate(doc["pages"] or [], 1):
            with st.expander(f"Page {page_no}", expanded=page_no == 1):
                st.markdown(f'<div dir="{direction}" style="white-space:pre-wrap;max-height:520px;overflow:auto;line-height:1.9">{html.escape(text)}</div>', unsafe_allow_html=True)
        for page_no, text in enumerate((doc["reader"] or {}).get("alternate_text_pages", []), 1):
            with st.expander(f"PDF text layer · page {page_no}"):
                st.text(text)
        if doc["reader"] and doc["reader"].get("recognition_score") is not None:
            st.caption("OCR recognition score is not a calibrated probability of field correctness.")
    with right:
        st.subheader("Review fields")
        fields = (doc["proposal"] or {}).get("fields", {})
        corrected_fields = {c["field"] for c in doc["corrections"]}
        for key, field in fields.items():
            st.markdown(f'**{key.replace("_", " ").title()}**' + (" · required" if key in REQUIRED else ""))
            st.text(doc["effective"].get(key) or "Not established")
            for issue in field["issues"]:
                if key not in corrected_fields:
                    st.caption("Review: " + issue)
            if key in corrected_fields:
                st.caption("Resolved by a saved source-review correction. Original proposal retained in history.")
            with st.expander("Source evidence", expanded=False):
                if not field["candidates"]:
                    st.write("No supporting passage found.")
                for candidate in field["candidates"]:
                    st.write(f'Page {candidate["evidence"]["page"]} · {candidate["evidence"].get("reading", "primary")} · line {candidate["evidence"]["line"]}')
                    st.text(candidate["raw"])
        if doc["state"] == "needs_review":
            with st.form("correction"):
                st.markdown("**Correct one field**")
                field = st.selectbox("Field", list(LABELS))
                value = st.text_area("Correct value (dates: YYYY-MM-DD)")
                clear = st.checkbox("Mark optional field as unknown")
                reason = st.text_input("Why was this corrected?")
                reviewer = st.text_input("Reviewer", key="correction_reviewer")
                if st.form_submit_button("Save correction"):
                    try:
                        store.correct(selected, field, None if clear else value, reason, reviewer, doc["revision"])
                        st.rerun()
                    except ValueError as exc:
                        st.error(str(exc))
            with st.form("confirm"):
                reviewer = st.text_input("Confirming reviewer")
                checked = st.checkbox("I checked the source and all effective fields, including unknowns.")
                if st.form_submit_button("Confirm record"):
                    try:
                        if not checked:
                            raise ValueError("Check the source-review acknowledgement before confirming.")
                        store.confirm(selected, reviewer, doc["revision"])
                        st.rerun()
                    except ValueError as exc:
                        st.error(str(exc))
        elif doc["state"] == "confirmed":
            st.success("Saved to the confirmed notices table. Included in exports.")
            if st.button("Reopen for correction"):
                store.reopen(selected, doc["revision"])
                st.rerun()
    st.divider()
    if doc["state"] in {"needs_review", "confirmed"}:
        st.subheader("Optional independent review")
        st.caption("The LLM judge sends the source reading and effective values to Groq. Its verdicts are advisory and can be wrong.")
        judge_model = st.text_input("Judge model", DEFAULT_JUDGE_MODEL)
        if st.button("Ask the LLM judge"):
            try:
                with st.spinner("Checking proposed values against the source…"):
                    store.judge(selected, judge_model)
                st.rerun()
            except ValueError as exc:
                st.error(str(exc))
        if doc["judge_reports"]:
            report = doc["judge_reports"][0]
            if report["revision"] != doc["revision"]:
                st.warning("This judge report applies to an earlier revision. Run a new review for current values.")
            st.caption(f'Revision {report["revision"]} · {report["model"]}')
            import json
            assessments = json.loads(report["report_json"])
            for field, assessment in assessments.items():
                with st.expander(f'{field.replace("_", " ").title()} · {assessment["verdict"]}'):
                    st.text(assessment["reason"])
    confirmed = [dict(r) for r in store.conn.execute("SELECT * FROM notices ORDER BY confirmed_at,document_id")]
    st.download_button("Download confirmed records · JSON", dumps({"schema_version": "1.0", "records": confirmed}),
                       file_name="confirmed-notices.json", mime="application/json")
    with st.expander("Experimental English translation and export"):
        st.caption("Translate confirmed text into English using an explicit Groq call. Originals remain unchanged. Review meaning and identifier renderings before English-only export.")
        translation_model = st.text_input("Translation model", DEFAULT_MODEL)
        if st.button("Propose English translation", disabled=doc["state"] != "confirmed"):
            try:
                with st.spinner("Translating the confirmed record…"):
                    store.translate(selected, translation_model)
                st.rerun()
            except ValueError as exc:
                st.error(str(exc))
        translation = store.english(selected)
        if translation:
            english_judge_model = st.text_input("English judge model", DEFAULT_JUDGE_MODEL)
            if st.button("Ask the judge to check English meaning against the original"):
                try:
                    with st.spinner("Reviewing translation faithfulness…"):
                        report = store.judge_english(selected, english_judge_model)
                    st.session_state["translation_judge_" + selected] = report
                except ValueError as exc:
                    st.error(str(exc))
            if st.session_state.get("translation_judge_" + selected):
                report = st.session_state["translation_judge_" + selected]
                if report["translation_id"] != translation["id"] or report["translation_revision"] != translation["revision"]:
                    st.warning("English judge report applies to an earlier translation snapshot.")
                st.json(report)
            if translation["source_revision"] != doc["revision"]:
                st.warning("This translation belongs to an earlier source revision. Request a current translation.")
            elif translation["state"] == "needs_review":
                with st.form("english_review_" + str(translation["id"])):
                    st.write("Review the English rendering against the original-language confirmed fields above.")
                    proposed = {}
                    for field in TEXT_FIELDS + ["reference_number"]:
                        source_value = doc["effective"].get(field)
                        if source_value is None:
                            proposed[field] = None
                            st.caption(field + ": absent in source; remains null")
                        else:
                            proposed[field] = st.text_area("English " + field.replace("_", " "),
                                value=translation["effective"].get(field) or "")
                    st.caption("Dates are preserved locally. Reference identifiers are not machine-translated: any ASCII rendering requires an explicit source-review decision.")
                    english_reviewer = st.text_input("English reviewer identifier (ASCII)")
                    english_reason = st.text_input("English review reason, including identifier decisions")
                    checked_translation = st.checkbox("I checked the English meaning, negation, dates and identifier rendering.")
                    if st.form_submit_button("Confirm English rendering"):
                        try:
                            if not checked_translation:
                                raise ValueError("Check the English-review acknowledgement first.")
                            store.confirm_english(translation["id"], translation["revision"], proposed, english_reviewer, english_reason)
                            st.rerun()
                        except ValueError as exc:
                            st.error(str(exc))
            else:
                st.success("English rendering reviewed for this source revision.")
                st.json(translation["effective"])
        if st.button("Prepare reviewed English-only export"):
            try:
                path = Path(db).parent / "exports" / "english-reviewed.json"
                store.export(path, "english-reviewed")
                st.session_state["english_export"] = path.read_text(encoding="utf-8")
            except ValueError as exc:
                st.error(str(exc))
        if st.session_state.get("english_export"):
            st.caption("Last prepared snapshot. Prepare again after changing confirmed records or translations.")
            st.download_button("Download reviewed English JSON", st.session_state["english_export"],
                               file_name="english-reviewed.json", mime="application/json")
    with st.expander("Processing and correction history"):
        st.json({"settings": doc["processing_options"], "reader": doc["reader"], "corrections": doc["corrections"], "review_events": doc["review_events"]})
finally:
    store.close()
