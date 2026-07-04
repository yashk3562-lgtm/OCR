"""Streamlit UI for the Sarvam Invoice OCR Agent."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd
import streamlit as st

try:
    from .export_utils import build_erp_payload, to_csv_bytes, to_json_bytes
    from .extraction_agent import extract_invoice_metadata, extract_invoice_field_translations
    from .models import FIELD_LABELS, MOCK_OCR_TEXT, SUPPORTED_FILE_TYPES
    from .sarvam_ocr import extract_text, run_sarvam_ocr
    from .utils import format_currency
    from .validation_agent import validate_invoice
except ImportError:  # pragma: no cover - supports `streamlit run invoice_agent/app.py`
    from export_utils import build_erp_payload, to_csv_bytes, to_json_bytes
    from extraction_agent import extract_invoice_metadata, extract_invoice_field_translations
    from models import FIELD_LABELS, MOCK_OCR_TEXT, SUPPORTED_FILE_TYPES
    from sarvam_ocr import extract_text, run_sarvam_ocr
    from utils import format_currency
    from validation_agent import validate_invoice


st.set_page_config(
    page_title="AI Invoice Digitization Agent",
    page_icon="",
    layout="wide",
)


def _inject_styles() -> None:
    st.markdown(
        """
        <style>
        .main .block-container { padding-top: 2rem; }
        .app-header {
            border-bottom: 1px solid #d8dee8;
            padding-bottom: 1rem;
            margin-bottom: 1.2rem;
        }
        .app-header h1 {
            font-size: 2rem;
            margin: 0;
            letter-spacing: 0;
            color: #172033;
        }
        .app-header p { margin: .35rem 0 0; color: #526071; }
        .metric-grid {
            display: grid;
            grid-template-columns: repeat(4, minmax(0, 1fr));
            gap: .75rem;
        }
        .metric-card {
            border: 1px solid #d8dee8;
            border-radius: 8px;
            padding: .8rem .9rem;
            background: #ffffff;
            min-height: 88px;
        }
        .metric-label {
            color: #647083;
            font-size: .78rem;
            text-transform: uppercase;
            letter-spacing: .03em;
        }
        .metric-value {
            color: #172033;
            font-size: 1.02rem;
            font-weight: 650;
            margin-top: .35rem;
            overflow-wrap: anywhere;
        }
        .status-pass { color: #0f7b3b; font-weight: 700; }
        .status-warning { color: #a76100; font-weight: 700; }
        .status-fail { color: #b42318; font-weight: 700; }
        .footer-note {
            color: #667085;
            font-size: .82rem;
            border-top: 1px solid #e4e7ec;
            padding-top: .9rem;
            margin-top: 1.4rem;
        }
        @media (max-width: 900px) {
            .metric-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
        }
        @media (max-width: 520px) {
            .metric-grid { grid-template-columns: 1fr; }
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def _init_state() -> None:
    defaults = {
        "ocr_result": None,
        "ocr_text": "",
        "invoice": None,
        "validation": None,
        "erp_payload": None,
        "processed_filename": "",
        "extraction_raw": "",
        "field_translations": [],
    }
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value


def _status_class(status: str) -> str:
    return {
        "PASS": "status-pass",
        "WARNING": "status-warning",
        "FAIL": "status-fail",
    }.get(status, "status-warning")


def _metric(label: str, value: Any) -> str:
    display = str(value) if value not in (None, "") else "Not captured"
    return f"""
    <div class="metric-card">
        <div class="metric-label">{label}</div>
        <div class="metric-value">{display}</div>
    </div>
    """


def _render_header() -> None:
    st.markdown(
        """
        <div class="app-header">
            <h1>AI Invoice Digitization Agent</h1>
            <p><strong>Sarvam OCR + Sarvam Chat -> ERP-ready invoice metadata</strong></p>
            <p>Convert Hindi handwritten or printed invoices into validated English metadata for Accounts Payable automation.</p>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _render_preview(uploaded_file: Any) -> None:
    st.subheader("Invoice preview")
    if uploaded_file is None:
        st.info("Upload PNG, JPG, JPEG, or PDF invoice documents.")
        return

    extension = Path(uploaded_file.name).suffix.lower().lstrip(".")
    if extension in {"png", "jpg", "jpeg"}:
        st.image(uploaded_file.getvalue(), use_container_width=True)
    elif extension == "pdf":
        size_kb = len(uploaded_file.getvalue()) / 1024
        st.info(f"PDF uploaded: {uploaded_file.name}")
        st.caption(f"{size_kb:,.1f} KB. First-page rendering is not enabled in this lightweight preview.")
    else:
        st.warning("Unsupported preview format.")


def _process_invoice(uploaded_file: Any, use_mock_ocr: bool) -> None:
    if uploaded_file is None and not use_mock_ocr:
        st.warning("Upload an invoice or enable mock OCR output.")
        return

    filename = uploaded_file.name if uploaded_file is not None else "mock_invoice.txt"
    extension = Path(filename).suffix.lower().lstrip(".")
    if uploaded_file is not None and extension not in SUPPORTED_FILE_TYPES:
        st.error("Unsupported file type. Use PNG, JPG, JPEG, or PDF.")
        return

    try:
        with st.status("Processing invoice", expanded=True) as status:
            st.write("Uploading invoice...")
            file_bytes = uploaded_file.getvalue() if uploaded_file is not None else MOCK_OCR_TEXT.encode("utf-8")

            st.write("Running Sarvam OCR...")
            if use_mock_ocr:
                ocr_result = {
                    "mode": "mock",
                    "filename": filename,
                    "text": MOCK_OCR_TEXT,
                    "metadata": {"provider": "mock", "language_hints": ["Hindi", "English"]},
                }
            else:
                ocr_result = run_sarvam_ocr(file_bytes, filename)

            ocr_text = extract_text(ocr_result)
            if not ocr_text:
                raise RuntimeError("OCR did not return readable text.")

            st.write("Understanding invoice...")
            st.write("Extracting fields...")
            invoice = extract_invoice_metadata(ocr_text, ocr_result)
            try:
                from .extraction_agent import get_last_sarvam_chat_response
            except ImportError:
                from extraction_agent import get_last_sarvam_chat_response
            st.session_state.extraction_raw = get_last_sarvam_chat_response()
            st.session_state.field_translations = extract_invoice_field_translations(ocr_text, ocr_result)

            st.write("Validating invoice...")
            validation = validate_invoice(invoice)
            invoice["needs_human_review"] = bool(invoice.get("needs_human_review") or validation["needs_human_review"])

            st.write("Generating ERP payload...")
            erp_payload = build_erp_payload(invoice, validation)

            st.session_state.ocr_result = ocr_result
            st.session_state.ocr_text = ocr_text
            st.session_state.invoice = invoice
            st.session_state.validation = validation
            st.session_state.erp_payload = erp_payload
            st.session_state.processed_filename = filename
            status.update(label="Invoice processed", state="complete", expanded=False)
    except Exception as exc:
        st.error(f"Invoice processing could not be completed: {exc}")


def _render_overview(invoice: dict[str, Any], validation: dict[str, Any]) -> None:
    confidence = invoice.get("confidence_score", 0)
    try:
        confidence_display = f"{float(confidence):.0%}" if float(confidence) <= 1 else f"{float(confidence):.1f}"
    except (TypeError, ValueError):
        confidence_display = "Not captured"

    status = validation.get("status", "WARNING")
    st.markdown(
        f"Validation status: <span class='{_status_class(status)}'>{status}</span>",
        unsafe_allow_html=True,
    )

    overview_items = [
        ("Vendor", invoice.get("vendor_name")),
        ("Invoice Number", invoice.get("invoice_number")),
        ("Invoice Date", invoice.get("invoice_date")),
        ("GSTIN", invoice.get("gstin")),
        ("PAN", invoice.get("pan")),
        ("Buyer", invoice.get("buyer_name")),
        ("Total Amount", format_currency(invoice.get("total_amount"))),
        ("Confidence", confidence_display),
        ("Human Review Required", "Yes" if invoice.get("needs_human_review") else "No"),
    ]

    cols = st.columns(3)
    for idx, (label, value) in enumerate(overview_items):
        with cols[idx % 3]:
            st.subheader(label)
            st.write(value or "Not captured")

    st.divider()
    detail_cols = st.columns(2)
    with detail_cols[0]:
        st.write("Vendor address")
        st.info(invoice.get("vendor_address") or "Not captured")
    with detail_cols[1]:
        st.write("Buyer address")
        st.info(invoice.get("buyer_address") or "Not captured")


def _render_ocr_tab(ocr_text: str, ocr_result: dict[str, Any]) -> None:
    metadata = ocr_result.get("metadata", {}) if isinstance(ocr_result, dict) else {}
    if metadata:
        st.subheader("OCR metadata")
        rows = [
            {
                "Field": key,
                "Value": json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else value,
            }
            for key, value in metadata.items()
        ]
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

    language_hints = metadata.get("language_hints") if isinstance(metadata, dict) else None
    if language_hints:
        st.caption(f"Language hints: {', '.join(map(str, language_hints))}")


def _render_line_items(invoice: dict[str, Any]) -> None:
    line_items = invoice.get("line_items") if isinstance(invoice.get("line_items"), list) else []
    if not line_items:
        st.warning("No line items were detected. This record should be reviewed before ERP posting.")
        return

    st.dataframe(
        pd.DataFrame(line_items, columns=["description", "quantity", "unit_price", "tax_rate", "amount"]).rename(
            columns={
                "description": "Description",
                "quantity": "Quantity",
                "unit_price": "Unit Price",
                "tax_rate": "Tax Rate",
                "amount": "Amount",
            }
        ),
        use_container_width=True,
        hide_index=True,
    )


def _render_validation(validation: dict[str, Any], erp_payload: dict[str, Any]) -> None:
    status = validation.get("status", "WARNING")
    st.markdown(
        f"Result: <span class='{_status_class(status)}'>{status}</span>",
        unsafe_allow_html=True,
    )
    st.write("Messages")
    for message in validation.get("messages", []):
        st.write(f"- {message}")

    missing = validation.get("missing_fields", [])
    if missing:
        labels = [FIELD_LABELS.get(field, field) for field in missing]
        st.warning(", ".join(labels))
    else:
        st.success("No mandatory fields missing.")

    st.write("Numeric checks")
    for check in validation.get("numeric_checks", []):
        st.write(f"- {check}")

    routing = erp_payload.get("routing", {})
    st.info(f"Routing: {routing.get('status', '')} -> {routing.get('queue', '')}")


def _render_downloads(invoice: dict[str, Any], erp_payload: dict[str, Any]) -> None:
    col1, col2, col3 = st.columns(3)
    with col1:
        st.download_button(
            "Download Invoice JSON",
            data=to_json_bytes(invoice),
            file_name="invoice_metadata.json",
            mime="application/json",
            use_container_width=True,
        )
    with col2:
        st.download_button(
            "Download Line Items CSV",
            data=to_csv_bytes(invoice),
            file_name="invoice_line_items.csv",
            mime="text/csv",
            use_container_width=True,
        )
    with col3:
        st.download_button(
            "Download ERP Payload JSON",
            data=to_json_bytes(erp_payload),
            file_name="erp_payload.json",
            mime="application/json",
            use_container_width=True,
        )


def main() -> None:
    _inject_styles()
    _init_state()
    _render_header()

    with st.sidebar:
        st.subheader("Processing controls")
        use_mock_ocr = st.checkbox("Use mock OCR output", value=True)
        st.caption("Planned extensions: duplicate detection, GST validation, fraud checks, PO matching, three-way matching, vendor risk, payment recommendation, and exception routing.")

    left, right = st.columns([0.9, 1.1], gap="large")
    with left:
        st.subheader("Upload invoice")
        uploaded_file = st.file_uploader(
            "Supported formats: PNG, JPG, JPEG, PDF",
            type=sorted(SUPPORTED_FILE_TYPES),
        )
        run_button = st.button("Run AI Extraction", type="primary", use_container_width=True)
        if run_button:
            _process_invoice(uploaded_file, use_mock_ocr)

    with right:
        _render_preview(uploaded_file)

    invoice = st.session_state.invoice
    validation = st.session_state.validation
    erp_payload = st.session_state.erp_payload

    if invoice and validation and erp_payload:
        st.divider()
        tabs = st.tabs(["Overview", "OCR Output", "Line Items", "Validation", "ERP Payload", "Downloads"])
        with tabs[0]:
            _render_overview(invoice, validation)
        with tabs[1]:
            _render_ocr_tab(st.session_state.ocr_text, st.session_state.ocr_result or {})
        with tabs[2]:
            _render_line_items(invoice)
        with tabs[3]:
            _render_validation(validation, erp_payload)
        with tabs[4]:
            st.json(erp_payload)
        with tabs[5]:
            _render_downloads(invoice, erp_payload)

    st.markdown(
        "<div class='footer-note'>Built as a separate Invoice OCR Agent for AP automation. Existing loan collections files are unchanged.</div>",
        unsafe_allow_html=True,
    )


if __name__ == "__main__":
    main()
