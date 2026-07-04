"""Shared constants and schemas for the Invoice OCR Agent."""

from __future__ import annotations

from copy import deepcopy
from typing import Any


SUPPORTED_FILE_TYPES = {"png", "jpg", "jpeg", "pdf"}
MANDATORY_FIELDS = ["invoice_number", "invoice_date", "vendor_name", "total_amount"]
CONFIDENCE_THRESHOLD = 0.72

FIELD_LABELS = {
    "invoice_number": "Invoice Number",
    "invoice_date": "Invoice Date",
    "vendor_name": "Vendor",
    "vendor_address": "Vendor Address",
    "buyer_name": "Buyer",
    "buyer_address": "Buyer Address",
    "gstin": "GSTIN",
    "pan": "PAN",
    "subtotal": "Subtotal",
    "cgst": "CGST",
    "sgst": "SGST",
    "igst": "IGST",
    "total_amount": "Total Amount",
    "amount_in_words": "Amount in Words",
    "payment_terms": "Payment Terms",
    "bank_details": "Bank Details",
    "confidence_score": "Confidence",
    "needs_human_review": "Human Review Required",
}

DEFAULT_LINE_ITEM = {
    "description": "",
    "quantity": "",
    "unit_price": "",
    "tax_rate": "",
    "amount": "",
}

DEFAULT_INVOICE_SCHEMA: dict[str, Any] = {
    "invoice_number": "",
    "invoice_date": "",
    "vendor_name": "",
    "vendor_address": "",
    "buyer_name": "",
    "buyer_address": "",
    "gstin": "",
    "pan": "",
    "line_items": [],
    "subtotal": "",
    "cgst": "",
    "sgst": "",
    "igst": "",
    "total_amount": "",
    "amount_in_words": "",
    "payment_terms": "",
    "bank_details": "",
    "confidence_score": 0.0,
    "needs_human_review": False,
}

MOCK_OCR_TEXT = """[MOCK OCR OUTPUT]
चालान संख्या: INV-1024
दिनांक: 15/07/2026
विक्रेता: शर्मा ट्रेडर्स
पता: करोल बाग, नई दिल्ली
खरीदार: अग्रवाल स्टोर्स
वस्तु: कॉटन कपड़ा, मात्रा 10 मीटर, दर 250, राशि 2500
CGST 9%: 225
SGST 9%: 225
कुल राशि: 2950 रुपये
भुगतान शर्तें: 15 दिनों में भुगतान
"""


def default_invoice() -> dict[str, Any]:
    """Return a fresh invoice schema instance."""
    return deepcopy(DEFAULT_INVOICE_SCHEMA)

