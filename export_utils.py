"""ERP export helpers for invoice metadata."""

from __future__ import annotations

import csv
import io
import json
from typing import Any


def to_json_bytes(invoice: dict[str, Any]) -> bytes:
    """Serialize an invoice dictionary as UTF-8 JSON bytes."""
    return json.dumps(invoice, ensure_ascii=False, indent=2).encode("utf-8")


def flatten_line_items(invoice: dict[str, Any]) -> list[dict[str, Any]]:
    """Flatten invoice line items with selected header fields for CSV export."""
    line_items = invoice.get("line_items") if isinstance(invoice.get("line_items"), list) else []
    rows: list[dict[str, Any]] = []
    for index, item in enumerate(line_items, start=1):
        row = {
            "invoice_number": invoice.get("invoice_number", ""),
            "vendor_name": invoice.get("vendor_name", ""),
            "line_number": index,
            "description": item.get("description", ""),
            "quantity": item.get("quantity", ""),
            "unit_price": item.get("unit_price", ""),
            "tax_rate": item.get("tax_rate", ""),
            "amount": item.get("amount", ""),
        }
        rows.append(row)
    return rows


def to_csv_bytes(invoice: dict[str, Any]) -> bytes:
    """Serialize invoice line items as CSV bytes."""
    rows = flatten_line_items(invoice)
    fields = [
        "invoice_number",
        "vendor_name",
        "line_number",
        "description",
        "quantity",
        "unit_price",
        "tax_rate",
        "amount",
    ]
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=fields)
    writer.writeheader()
    writer.writerows(rows)
    return buffer.getvalue().encode("utf-8")


def build_erp_payload(invoice: dict[str, Any], validation: dict[str, Any]) -> dict[str, Any]:
    """Build a mock ERP-ready invoice payload with routing metadata."""
    needs_review = bool(validation.get("needs_human_review") or invoice.get("needs_human_review"))
    return {
        "source": "sarvam_invoice_agent",
        "document_type": "invoice",
        "invoice_header": {
            "invoice_number": invoice.get("invoice_number", ""),
            "invoice_date": invoice.get("invoice_date", ""),
            "vendor_name": invoice.get("vendor_name", ""),
            "vendor_address": invoice.get("vendor_address", ""),
            "buyer_name": invoice.get("buyer_name", ""),
            "buyer_address": invoice.get("buyer_address", ""),
            "gstin": invoice.get("gstin", ""),
            "pan": invoice.get("pan", ""),
            "subtotal": invoice.get("subtotal", ""),
            "total_amount": invoice.get("total_amount", ""),
            "amount_in_words": invoice.get("amount_in_words", ""),
            "payment_terms": invoice.get("payment_terms", ""),
            "bank_details": invoice.get("bank_details", ""),
        },
        "line_items": invoice.get("line_items", []),
        "tax_summary": {
            "cgst": invoice.get("cgst", ""),
            "sgst": invoice.get("sgst", ""),
            "igst": invoice.get("igst", ""),
        },
        "validation": validation,
        "routing": {
            "status": "needs_human_review" if needs_review else "ready_for_erp",
            "queue": "ap_exception_review" if needs_review else "ap_processing",
        },
    }

