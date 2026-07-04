"""Validation agent for extracted invoice metadata."""

from __future__ import annotations

import re
from typing import Any

try:
    from .models import CONFIDENCE_THRESHOLD, MANDATORY_FIELDS
except ImportError:  # pragma: no cover - supports streamlit direct execution
    from models import CONFIDENCE_THRESHOLD, MANDATORY_FIELDS


def parse_amount(value: str | int | float | None) -> float | None:
    """Parse an Indian currency or numeric string into a float."""
    if value is None or value == "":
        return None
    if isinstance(value, int | float):
        return float(value)
    cleaned = re.sub(r"[^0-9.\-]", "", str(value).replace(",", ""))
    if cleaned in {"", ".", "-", "-."}:
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


def is_valid_gstin(value: str) -> bool:
    """Validate a GSTIN-like identifier."""
    if not value:
        return True
    return bool(re.fullmatch(r"[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z]", value.strip().upper()))


def is_valid_pan(value: str) -> bool:
    """Validate an Indian PAN-like identifier."""
    if not value:
        return True
    return bool(re.fullmatch(r"[A-Z]{5}[0-9]{4}[A-Z]", value.strip().upper()))


def _sum_line_items(line_items: list[dict[str, Any]]) -> float | None:
    amounts = [parse_amount(item.get("amount")) for item in line_items if isinstance(item, dict)]
    amounts = [amount for amount in amounts if amount is not None]
    if not amounts:
        return None
    return sum(amounts)


def validate_invoice(invoice: dict[str, Any]) -> dict[str, Any]:
    """Validate mandatory fields, IDs, line items, confidence, and totals."""
    messages: list[str] = []
    missing_fields = [field for field in MANDATORY_FIELDS if not str(invoice.get(field, "")).strip()]
    numeric_checks: list[str] = []

    if missing_fields:
        messages.append("Mandatory invoice fields are missing.")

    gstin = str(invoice.get("gstin", "")).strip()
    pan = str(invoice.get("pan", "")).strip()
    if gstin and not is_valid_gstin(gstin):
        messages.append("GSTIN format appears invalid.")
    if pan and not is_valid_pan(pan):
        messages.append("PAN format appears invalid.")

    line_items = invoice.get("line_items") if isinstance(invoice.get("line_items"), list) else []
    if not line_items:
        messages.append("No line items were detected.")

    total = parse_amount(invoice.get("total_amount"))
    subtotal = parse_amount(invoice.get("subtotal"))
    taxes = [parse_amount(invoice.get(field)) or 0 for field in ("cgst", "sgst", "igst")]
    tax_total = sum(taxes)

    numeric_ok = True
    if total is not None and subtotal is not None:
        expected_total = subtotal + tax_total
        if abs(expected_total - total) <= max(1.0, total * 0.01):
            numeric_checks.append("Subtotal plus tax approximately matches total.")
        else:
            numeric_ok = False
            numeric_checks.append("Subtotal plus tax does not match total.")
    else:
        numeric_ok = False
        numeric_checks.append("Totals could not be fully verified.")

    line_total = _sum_line_items(line_items)
    if line_total is not None and total is not None:
        comparable_total = subtotal if subtotal is not None else total - tax_total
        if abs(line_total - comparable_total) <= max(1.0, comparable_total * 0.01):
            numeric_checks.append("Line item amounts approximately match subtotal.")
        else:
            numeric_ok = False
            numeric_checks.append("Line item amounts do not match subtotal.")

    try:
        confidence = float(invoice.get("confidence_score", 0) or 0)
    except (TypeError, ValueError):
        confidence = 0.0
    if confidence < CONFIDENCE_THRESHOLD:
        messages.append("Confidence score is below the review threshold.")

    fail = not invoice.get("vendor_name") or total is None
    warning = bool(messages or missing_fields or not numeric_ok or invoice.get("needs_human_review"))
    status = "FAIL" if fail else "WARNING" if warning else "PASS"

    return {
        "status": status,
        "messages": messages or ["Invoice passed validation checks."],
        "missing_fields": missing_fields,
        "numeric_checks": numeric_checks,
        "needs_human_review": status in {"WARNING", "FAIL"},
    }

