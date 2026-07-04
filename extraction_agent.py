"""Sarvam Chat invoice understanding agent."""

from __future__ import annotations

import html
import json
import os
import re
from typing import Any

try:
    from .models import DEFAULT_LINE_ITEM, default_invoice
    from .utils import get_sarvam_api_key
except ImportError:  # pragma: no cover - supports direct execution
    from models import DEFAULT_LINE_ITEM, default_invoice
    from utils import get_sarvam_api_key


CHAT_URL = os.getenv("SARVAM_CHAT_URL", "https://api.sarvam.ai/v1/chat/completions").strip()
CHAT_MODEL = os.getenv("SARVAM_CHAT_MODEL", "sarvam-105b").strip()

SYSTEM_PROMPT = """You are an enterprise Accounts Payable invoice extraction agent.
Return ONLY one valid JSON object.
Do not include markdown.
Do not explain.
Do not include reasoning.
First character must be { and last character must be }."""

USER_PROMPT = """Read Hindi, English, or Hinglish OCR text from an invoice.
Translate Hindi invoice fields into English.
If OCR text is in Hindi or Hinglish, translate field names and values into English and preserve the original meaning.
Use semantic understanding of seller, buyer, invoice number, date, GSTIN, PAN, totals, and line items.
For vendor and buyer names, use readable English transliteration of Hindi/Devanagari names rather than leaving them blank.
Do not use the invoice title, header, or document type as the vendor name.
Normalize invoice metadata.
Preserve numbers exactly.
Convert date formats into YYYY-MM-DD if possible.
Extract vendor, buyer, GSTIN, PAN, line items, taxes, totals, payment terms, and bank details.
Do not hallucinate missing values; use empty strings for missing fields.
If a field is present in the invoice but cannot be confidently parsed, set needs_human_review=true.

Return JSON matching this schema:
{
  "invoice_number": "",
  "invoice_date": "",
  "vendor_name": "",
  "vendor_address": "",
  "buyer_name": "",
  "buyer_address": "",
  "gstin": "",
  "pan": "",
  "line_items": [
    {
      "description": "",
      "quantity": "",
      "unit_price": "",
      "tax_rate": "",
      "amount": ""
    }
  ],
  "subtotal": "",
  "cgst": "",
  "sgst": "",
  "igst": "",
  "total_amount": "",
  "amount_in_words": "",
  "payment_terms": "",
  "bank_details": "",
  "confidence_score": 0.0,
  "needs_human_review": false
}

OCR text:
{ocr_text}

OCR metadata:
{ocr_metadata}
"""


STRUCTURED_PROMPT = """You are an enterprise Accounts Payable extraction assistant.
Fill the following invoice fields exactly as JSON keys. Return ONLY one valid JSON object matching the schema below (no markdown, no explanation):
{
    "invoice_number": "",
    "invoice_date": "",
    "vendor_name": "",
    "vendor_address": "",
    "buyer_name": "",
    "buyer_address": "",
    "gstin": "",
    "pan": "",
    "line_items": [
        {"description": "", "quantity": "", "unit_price": "", "tax_rate": "", "amount": ""}
    ],
    "subtotal": "",
    "cgst": "",
    "sgst": "",
    "igst": "",
    "total_amount": "",
    "amount_in_words": "",
    "payment_terms": "",
    "bank_details": "",
    "confidence_score": 0.0,
    "needs_human_review": false
}

Use the provided OCR text and any OCR JSON outputs. If a field cannot be confidently parsed, set it to an empty string and set `needs_human_review` to true. Preserve numeric values exactly. Translate Hindi values into English transliteration where appropriate. Do not hallucinate values not present in the OCR outputs.

OCR text:
{ocr_text}

OCR metadata:
{ocr_metadata}
"""

TRANSLATION_PROMPT = """Read Hindi, English, or Hinglish OCR text from an invoice.
For every invoice field you can identify, return a JSON object with a single key `field_translations`.
Each item should preserve the exact original OCR snippet as `ocr_text` and provide a clean English translation as `translation`.
Do not invent values not present in the OCR text.
Return only valid JSON with this structure:
{
  "field_translations": [
    {
      "field_name": "vendor_name",
      "ocr_text": "विक्रेता: शर्मा ट्रेडर्स",
      "translation": "Seller: Sharma Traders"
    }
  ]
}

OCR text:
{ocr_text}

OCR metadata:
{ocr_metadata}
"""


class InvoiceExtractionError(RuntimeError):
    """Raised when Sarvam Chat extraction cannot complete."""

LAST_SARVAM_CHAT_RESPONSE = ""


def _headers(api_key: str) -> dict[str, str]:
    return {
        "api-subscription-key": api_key,
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }


def _extract_json_object(text: str) -> str:
    """Return the first balanced JSON object found in the text."""
    source = str(text or "")
    start = source.find("{")
    if start < 0:
        return source

    depth = 0
    for idx, char in enumerate(source[start:], start):
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return source[start : idx + 1]
    return source[start:]


def _normalize_json_text(value: str) -> str:
    text = str(value or "").strip()
    text = html.unescape(text)
    text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s*```$", "", text)
    text = text.replace("“", '"').replace("”", '"').replace("‘", "'").replace("’", "'")
    text = _extract_json_object(text)
    text = re.sub(r",(\s*[}\]])", r"\1", text)
    text = re.sub(r"\bTrue\b", "true", text)
    text = re.sub(r"\bFalse\b", "false", text)
    text = re.sub(r"\bNone\b", "null", text)
    return text


def _extract_field(patterns: list[str], text: str) -> str:
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE | re.MULTILINE)
        if match:
            return match.group(1).strip()
    return ""


def _is_label_only_value(value: Any) -> bool:
    text = str(value or "").strip()
    if not text:
        return True
    if re.fullmatch(r"(?i)(?:नाम|का\s*नाम|address|पता|seller|vendor|buyer|customer|shri|श्री|श्रीमती|bill|invoice)\s*[:\-]?", text):
        return True
    if re.search(r"\b(का\s*नाम|नाम|address|पता|seller|vendor|buyer|customer)\b", text, flags=re.IGNORECASE) and len(text.split()) <= 4:
        return True
    return False


def _extract_address_block(lines: list[str], start_index: int, max_lines: int = 3) -> str:
    address_parts: list[str] = []
    for candidate in lines[start_index : start_index + max_lines]:
        if not candidate:
            continue
        if re.search(r"\b(GSTIN|PAN|DATE|TOTAL|AMOUNT|INVOICE|BILL|SUBTOTAL|CGST|SGST|IGST|Payment Terms|Bank|मोबाइल|Mobile|GST|Tax)\b", candidate, flags=re.IGNORECASE):
            break
        if re.search(r"^(?:विक्रेता|खरीदार|Seller|Buyer|Vendor|Customer|Supplier)\b", candidate, flags=re.IGNORECASE):
            continue
        address_parts.append(candidate)
    return " ".join(address_parts).strip()


def _extract_address_from_anchor(text: str, anchor: str | None = None) -> str:
    lines = [line.strip() for line in str(text or "").splitlines() if line.strip()]
    if anchor:
        normalized_anchor = re.sub(r"\s+", " ", anchor).strip()
        for index, line in enumerate(lines):
            if normalized_anchor in re.sub(r"\s+", " ", line):
                return _extract_address_block(lines, index + 1)
    return ""


def _merge_invoice_fallback(primary: dict[str, Any], fallback: dict[str, Any]) -> dict[str, Any]:
    if _is_label_only_value(primary.get("vendor_name")) and fallback.get("vendor_name"):
        primary["vendor_name"] = fallback["vendor_name"]
    if _is_label_only_value(primary.get("buyer_name")) and fallback.get("buyer_name"):
        primary["buyer_name"] = fallback["buyer_name"]
    if not primary.get("vendor_address") and fallback.get("vendor_address"):
        primary["vendor_address"] = fallback["vendor_address"]
    if not primary.get("buyer_address") and fallback.get("buyer_address"):
        primary["buyer_address"] = fallback["buyer_address"]

    primary_line_items = primary.get("line_items")
    if not isinstance(primary_line_items, list) or not any(_is_valid_line_item(item) and not _is_suspicious_line_item(item) for item in primary_line_items):
        primary["line_items"] = fallback.get("line_items", [])

    if not str(primary.get("invoice_number") or "").strip() and fallback.get("invoice_number"):
        primary["invoice_number"] = fallback["invoice_number"]
    if not str(primary.get("invoice_date") or "").strip() and fallback.get("invoice_date"):
        primary["invoice_date"] = fallback["invoice_date"]
    if not str(primary.get("total_amount") or "").strip() and fallback.get("total_amount"):
        primary["total_amount"] = fallback["total_amount"]
    return primary


def _normalize_key(key: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(key or "").lower())


def _normalize_dict_keys(data: dict[str, Any]) -> dict[str, Any]:
    return {_normalize_key(key): value for key, value in data.items()}


def _looks_like_date(value: Any) -> bool:
    text = str(value or "").strip()
    return bool(re.search(r"\b(?:[0-3]?\d[\/\-][0-1]?\d[\/\-](?:\d{2}|\d{4})|\d{4}[\/\-][0-1]?\d[\/\-][0-3]?\d)\b", text))


def _looks_like_amount(value: Any) -> bool:
    text = str(value or "").strip().replace("₹", "").replace("Rs.", "").replace("Rs", "").replace(",", "")
    return bool(re.match(r"^[0-9]+(?:\.[0-9]+)?$", text))


def _looks_like_gstin(value: Any) -> bool:
    return bool(re.match(r"^[0-9A-Z]{15}$", str(value or "").strip()))


def _looks_like_pan(value: Any) -> bool:
    return bool(re.match(r"^[A-Z]{5}[0-9]{4}[A-Z]$", str(value or "").strip()))


def _looks_like_invoice_number(value: Any) -> bool:
    text = str(value or "").strip()
    if not text:
        return False
    if re.search(r"\b(inv|invoice|bill|bn|no)\b", text, flags=re.IGNORECASE):
        return True
    return bool(re.search(r"[A-Za-z]{1,3}[-/]?\d{2,8}", text))


def _looks_like_name(value: Any) -> bool:
    text = str(value or "").strip()
    if not text:
        return False
    if _is_invoice_heading_text(text):
        return False
    if _looks_like_amount(text) or _looks_like_date(text) or _looks_like_gstin(text) or _looks_like_pan(text):
        return False
    return bool(re.search(r"[A-Za-z\u0900-\u097F]", text))


def _is_probably_address(value: Any) -> bool:
    text = str(value or "").strip()
    if not text:
        return False
    return bool(re.search(r"\b(Address|पता|Street|Road|Colony|Sector|Area|Block|Flat|Building|Bazar|Market|Delhi|Mumbai|Bangalore|Kolkata)\b", text, flags=re.IGNORECASE))


def _is_invoice_heading_text(value: str) -> bool:
    text = str(value or "").strip()
    if not text:
        return False
    if re.search(r"^(TAX\s+INVOICE|INVOICE|GST\s*INVOICE|RECEIPT|TAX\s*INVOICE|INVOICE\s*NO|BILL\s*OF\s*SUPPLY|चालान|बीजक|बिल)\b", text, flags=re.IGNORECASE):
        return True
    if re.search(r"GSTIN|PAN|DATE|TOTAL|AMOUNT|SUBTOTAL|QUANTITY|RATE|VALUE|DESCRIPTION|ITEM", text, flags=re.IGNORECASE):
        return True
    return False


def _clean_name_text(value: str) -> str:
    return re.sub(r"[\s\-\|\/]+$", "", str(value or "").strip())


def _normalize_hindi_terms(text: str) -> str:
    replacements = {
        "विक्रेता": "Seller",
        "खरीदार": "Buyer",
        "उपयोग": "Subtotal",
        "कुल देय राशि": "Total Amount",
        "कुल राशि": "Total Amount",
        "मात्रा": "Quantity",
        "दर": "Unit Price",
        "राशि": "Amount",
        "GSTIN": "GSTIN",
        "PAN": "PAN",
        "दिनांक": "Date",
        "वस्तु": "Item",
        "भुगतान शर्तें": "Payment Terms",
        "पता": "Address",
    }
    normalized = str(text or "")
    for hindi, english in replacements.items():
        normalized = re.sub(re.escape(hindi), english, normalized, flags=re.IGNORECASE)
    return normalized


def _extract_first_possible_vendor(text: str) -> str:
    lines = [line.strip() for line in str(text or "").splitlines() if line.strip()]
    normalized_lines = [line for line in lines if line]
    for i, line in enumerate(normalized_lines):
        if _is_invoice_heading_text(line):
            continue
        if re.search(r"GSTIN|PAN|DATE|TOTAL|AMOUNT|SUBTOTAL|QUANTITY|RATE|VALUE|DESCRIPTION|ITEM|INVOICE|BILL|Total Amount|Payment Terms|Address", line, flags=re.IGNORECASE):
            continue
        if re.search(r"[:\-]", line) and not re.search(r"(विक्रेता|Seller|Supplier|खरीदार|Buyer|Customer)", line, flags=re.IGNORECASE):
            continue
        if len(line) > 120:
            continue
        if re.search(r"(विक्रेता|Seller|Supplier|Vendor|खरीदार|Buyer|Customer)", line, flags=re.IGNORECASE):
            return _clean_name_text(re.sub(r"(विक्रेता|Seller|Supplier|Vendor)\s*[:\-]?\s*", "", line, flags=re.IGNORECASE))
        if i < 2 and _is_name_candidate(line):
            return _clean_name_text(line)

    for line in normalized_lines:
        if _is_name_candidate(line):
            return _clean_name_text(line)
    return ""


def _find_invoice_object(parsed: Any) -> dict[str, Any]:
    if isinstance(parsed, dict):
        normalized_keys = _normalize_dict_keys(parsed)
        if len(normalized_keys) == 1:
            sole_value = next(iter(normalized_keys.values()))
            if isinstance(sole_value, dict):
                return _find_invoice_object(sole_value)
        return parsed
    return {}


def _parse_raw_text_fallback(ocr_text: str) -> dict[str, Any]:
    invoice = default_invoice()
    text = _normalize_hindi_terms(str(ocr_text or ""))
    invoice["invoice_number"] = _extract_field(
        [r"बीजक\s*क्रमांक\s*[:\-]\s*([A-Za-z0-9/\\-]+)", r"invoice\s*number\s*[:\-]\s*([A-Za-z0-9/\\-]+)", r"bill\s*number\s*[:\-]\s*([A-Za-z0-9/\\-]+)"]
        , text)
    invoice["invoice_date"] = _extract_field(
        [r"दिनांक\s*[:\-]\s*([0-9]{1,2}/[0-9]{1,2}/[0-9]{2,4})", r"date\s*[:\-]\s*([0-9]{1,2}/[0-9]{1,2}/[0-9]{2,4})"], text)
    invoice["gstin"] = _extract_field(
        [r"GSTIN\s*[:\-]\s*([0-9A-Z]{15})"], text)
    invoice["pan"] = _extract_field(
        [r"PAN\s*[:\-]\s*([A-Z]{5}[0-9]{4}[A-Z])"], text)
    invoice["total_amount"] = _extract_field(
        [r"कुल\s*देय\s*राशि\s*[:\u0930u20B9\s]*([0-9,]+\.?[0-9]*)", r"कुल\s*राशि\s*.*?₹\s*([0-9,]+\.?[0-9]*)", r"Total\s*Amount\s*[:\s]*([0-9,]+\.?[0-9]*)", r"कुल\s*राशि\s*[:\-]?\s*([0-9,]+\.?[0-9]*)"], text)
    invoice["subtotal"] = _extract_field(
        [r"उप-योग\s*[:\s]*([0-9,]+\.?[0-9]*)", r"कुल\s*कर\s*योग्य\s*राशि\s*.*?([0-9,]+\.?[0-9]*)"], text)
    invoice["vendor_name"] = _extract_field(
        [
            r"विक्रेता\s*[:\-]\s*([^\n]+)",
            r"Seller\s*[:\-]\s*([^\n]+)",
            r"विक्रेता\s*का\s*नाम\s*[:\-]\s*([^\n]+)",
            r"Supplier\s*[:\-]\s*([^\n]+)",
            r"^(श्री\s*[^\n]+)",
        ],
        text,
    )
    invoice["buyer_name"] = _extract_field(
        [
            r"खरीदार\s*[:\-]\s*([^\n]+)",
            r"Buyer\s*[:\-]\s*([^\n]+)",
            r"Buyer\s*Name\s*[:\-]\s*([^\n]+)",
            r"Customer\s*[:\-]\s*([^\n]+)",
            r"Purchaser\s*[:\-]\s*([^\n]+)",
        ],
        text,
    )
    invoice["vendor_address"] = _extract_field(
        [
            r"पता\s*[:\-]\s*([^\n]+)",
            r"Vendor\s*Address\s*[:\-](.*)",
            r"Seller\s*Address\s*[:\-](.*)",
        ],
        text,
    )
    invoice["buyer_address"] = _extract_field(
        [
            r"खरीदार\s*पता\s*[:\-]\s*([^\n]+)",
            r"Buyer\s*Address\s*[:\-](.*)",
            r"Customer\s*Address\s*[:\-](.*)",
        ],
        text,
    )
    if not invoice["vendor_name"]:
        invoice["vendor_name"] = _extract_first_possible_vendor(text)
    if not invoice["buyer_name"]:
        invoice["buyer_name"] = _extract_field(
            [
                r"खरीदार\s*[:\-]\s*([^\n]+)",
                r"Buyer\s*[:\-]\s*([^\n]+)",
                r"Customer\s*[:\-]\s*([^\n]+)",
                r"Purchaser\s*[:\-]\s*([^\n]+)",
            ],
            text,
        )
    if not invoice["vendor_address"] and invoice["vendor_name"]:
        invoice["vendor_address"] = _extract_address_from_anchor(text, invoice["vendor_name"])
    if not invoice["vendor_address"]:
        invoice["vendor_address"] = _extract_field(
            [
                r"पता\s*[:\-]\s*([^\n]+)",
                r"Address\s*[:\-]\s*([^\n]+)",
                r"Vendor\s*Address\s*[:\-]\s*([^\n]+)",
                r"Seller\s*Address\s*[:\-]\s*([^\n]+)",
            ],
            text,
        )
    if not invoice["buyer_address"] and invoice["buyer_name"]:
        invoice["buyer_address"] = _extract_address_from_anchor(text, invoice["buyer_name"])
    if not invoice["buyer_address"]:
        invoice["buyer_address"] = _extract_field(
            [
                r"खरीदार\s*पता\s*[:\-]\s*([^\n]+)",
                r"Buyer\s*Address\s*[:\-]\s*([^\n]+)",
                r"Customer\s*Address\s*[:\-]\s*([^\n]+)",
            ],
            text,
        )
    invoice["line_items"] = _parse_line_items_from_text(text)
    invoice["confidence_score"] = 0.0
    invoice["needs_human_review"] = True
    return invoice


def _is_line_item_header(line: str) -> bool:
    return bool(
        re.search(
            r"\b(description|विवरण|items|qty|quantity|मात्रा|rate|unit price|unit_price|price|amount|value|tax|igst|cgst|sgst|subtotal)\b",
            str(line or ""),
            flags=re.IGNORECASE,
        )
    )


def _looks_like_numeric(value: str) -> bool:
    # Remove currency symbols and non-digit/decimal characters for detection
    normalized = re.sub(r"[^0-9\.]", "", str(value or "").strip())
    return bool(re.match(r"^[0-9]+(?:\.[0-9]+)?$", normalized))


def _parse_line_items_from_text(text: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    normalized_text = _normalize_hindi_terms(str(text or ""))
    # If the OCR returned raw HTML table fragments, try parsing tables directly
    if "<tr" in normalized_text.lower() or "<td" in normalized_text.lower() or "<th" in normalized_text.lower():
        try:
            return _parse_line_items_from_html(normalized_text)
        except Exception:
            # fall back to text parsing
            pass
    lines = [line.strip() for line in normalized_text.splitlines() if line.strip()]

    for line in lines:
        # skip summary/totals/metadata lines
        if re.search(r"\b(CGST|SGST|IGST|TOTAL|SUBTOTAL|AMOUNT IN WORDS|AMOUNT IN WORD|Payment Terms|Bank Details|GSTIN|PAN|Account No|खाता संख्या|मोबाइल|Mobile|Address)\b", line, flags=re.IGNORECASE):
            continue
        # ignore CSS or style-like lines produced by HTML extraction
        if re.match(r"^\s*(?:padding|margin|width|min-height|line-height|text-indent|border|font-size|display)\s*:\s*", line, flags=re.IGNORECASE):
            continue
        # skip header rows, but allow explicit item: lines to pass through
        if _is_line_item_header(line) and not re.match(r'^(?:वस्तु|item|description)\s*[:\-]?', line, flags=re.IGNORECASE):
            continue
        # skip address, note, and payment-related rows
        if re.search(r"\b(Address|पता|Payment Terms|भुगतान|Note|नोट|Account|खाता|Mobile|मोबाइल|GSTIN|PAN)\b", line, flags=re.IGNORECASE) and not re.search(r"[0-9,]+(?:\.[0-9]+)?", line):
            continue

        item_match = re.search(r"^(?:वस्तु|item|description)\s*[:\-]?", line, flags=re.IGNORECASE)
        amount_match = re.search(r"राशि\s*[:\-]?\s*([0-9,]+(?:\.[0-9]+)?)|amount\s*[:\-]?\s*([0-9,]+(?:\.[0-9]+)?)", line, flags=re.IGNORECASE)
        # Skip lines that are clearly invoice-level metadata
        if re.search(r"\b(invoice|inv|bill|date|दिनांक|चालान|बीजक)\b", line, flags=re.IGNORECASE):
            continue

        if item_match:
            # Simplified token parsing for item lines: split by commas
            content = re.sub(r"^(?:वस्तु|item|description)\s*[:\-]?\s*", "", line, flags=re.IGNORECASE)
            tokens = [t.strip() for t in content.split(",") if t.strip()]
            description = tokens[0] if tokens else content.strip()
            quantity = ""
            unit_price = ""
            amount = ""
            for tok in tokens[1:]:
                # detect amount
                if re.search(r"\b(राशि|amount)\b", tok, flags=re.IGNORECASE):
                    m = re.search(r"([0-9,]+(?:\.[0-9]+)?)", tok)
                    if m:
                        amount = re.sub(r"[^0-9\.]", "", m.group(1))
                        continue
                # detect unit price
                if re.search(r"\b(दर|unit price|rate)\b", tok, flags=re.IGNORECASE):
                    m = re.search(r"([0-9,]+(?:\.[0-9]+)?)", tok)
                    if m:
                        unit_price = re.sub(r"[^0-9\.]", "", m.group(1))
                        continue
                # detect quantity
                if re.search(r"\b(मात्रा|quantity|qty)\b", tok, flags=re.IGNORECASE):
                    m = re.search(r"([0-9,]+(?:\.[0-9]+)?)", tok)
                    if m:
                        quantity = re.sub(r"[^0-9\.]", "", m.group(1))
                        continue
                # fallback: if token contains only a number, assume it's amount or unit price depending on what's empty
                m = re.search(r"^([0-9,]+(?:\.[0-9]+)?)$", tok.replace(" ", ""))
                if m:
                    num = re.sub(r"[^0-9\.]", "", m.group(1))
                    if not amount:
                        amount = num
                    elif not unit_price:
                        unit_price = num
            rows.append(
                {
                    "description": description,
                    "quantity": quantity.strip(),
                    "unit_price": unit_price.strip(),
                    "tax_rate": "",
                    "amount": amount,
                }
            )
            continue

        parts = [part.strip() for part in re.split(r"\s{2,}|\t", line) if part.strip()]
        if len(parts) < 2:
            parts = [part.strip() for part in re.split(r"\s+", line) if part.strip()]

        numeric_indices = [idx for idx, part in enumerate(parts) if _looks_like_numeric(part)]
        if numeric_indices and len(parts) >= 2:
            last_idx = numeric_indices[-1]
            amount = parts[last_idx]
            quantity = ""
            unit_price = ""
            tax_rate = ""

            if len(numeric_indices) >= 2:
                unit_price = parts[numeric_indices[-2]]
            if len(numeric_indices) >= 3:
                quantity = parts[numeric_indices[-3]]

            description_end = numeric_indices[-3] if len(numeric_indices) >= 3 else numeric_indices[-2] if len(numeric_indices) >= 2 else last_idx
            description = " ".join(parts[:description_end]).strip()
            if not description and len(parts) > last_idx + 1:
                description = " ".join(parts[: last_idx]).strip()

            if description and _looks_like_numeric(amount):
                # normalize amount to digits only
                amount_clean = re.sub(r"[^0-9\.]", "", str(amount))
                rows.append(
                    {
                        "description": description,
                        "quantity": quantity,
                        "unit_price": unit_price,
                        "tax_rate": tax_rate,
                        "amount": amount_clean,
                    }
                )
                continue

        match = re.search(
            r"(.+?)\s+([0-9,]+(?:\.[0-9]+)?)\s*राशि",
            line,
            flags=re.IGNORECASE,
        )
        if match:
            rows.append(
                {
                    "description": match.group(1).strip(),
                    "quantity": "",
                    "unit_price": "",
                    "tax_rate": "",
                    "amount": match.group(2).strip(),
                }
            )
            continue

    return rows


def _strip_tags(html_text: str) -> str:
    text = re.sub(r"(?is)<(script|style).*?>.*?</\1>", "", str(html_text))
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", text)
    return html.unescape(text).strip()


def _parse_line_items_from_html(html_text: str) -> list[dict[str, Any]]:
    """Parse HTML tables and return line item dicts.

    Heuristic: each <tr> represents a row; map numeric columns to quantity/unit_price/amount
    by position (last numeric -> amount, previous -> unit_price, previous -> quantity).
    """
    rows: list[dict[str, Any]] = []
    table_rows = re.findall(r"(?is)<tr[^>]*>(.*?)</tr>", html_text)
    for tr in table_rows:
        # extract th/td cells
        cells = re.findall(r"(?is)<t[dh][^>]*>(.*?)</t[dh]>", tr)
        if not cells:
            # try splitting by <td> fallback
            parts = re.split(r"(?is)</td>\s*<td[^>]*>", tr)
            cells = [re.sub(r"<[^>]+>", "", p).strip() for p in parts if p.strip()]
        cleaned_cells = [_strip_tags(cell) for cell in cells]
        if not cleaned_cells:
            continue

        # find numeric-like cells
        numeric_indices = [i for i, c in enumerate(cleaned_cells) if _looks_like_numeric(c)]
        if not numeric_indices:
            # skip pure header rows
            continue

        last = numeric_indices[-1]
        amount = re.sub(r"[^0-9\.]", "", cleaned_cells[last])
        if not amount:
            continue

        unit_price = ""
        quantity = ""
        if len(numeric_indices) >= 2:
            unit_price = re.sub(r"[^0-9\.]", "", cleaned_cells[numeric_indices[-2]])
        if len(numeric_indices) >= 3:
            quantity = re.sub(r"[^0-9\.]", "", cleaned_cells[numeric_indices[-3]])

        # description is everything up to the first numeric cell
        description = " ".join(
            [p for p in cleaned_cells[: (numeric_indices[-3] if len(numeric_indices) >= 3 else numeric_indices[0])] if p]
        ).strip()
        if not description:
            description = cleaned_cells[0]

        if re.search(r"\b(Address|पता|Payment Terms|भुगतान|Note|नोट|Account|खाता|Mobile|मोबाइल|GSTIN|PAN|श्री|श्री\s*गणेश|Note)\b", description, flags=re.IGNORECASE):
            continue

        rows.append(
            {
                "description": description,
                "quantity": quantity,
                "unit_price": unit_price,
                "tax_rate": "",
                "amount": amount,
            }
        )
    return rows


def _is_suspicious_vendor_name(value: Any) -> bool:
    text = str(value or "").strip()
    if not text:
        return False
    if _is_invoice_heading_text(text):
        return True
    if re.search(r"invoice|bill|tax invoice|gst invoice|invoice no|चालान|बीजक", text, flags=re.IGNORECASE):
        return True
    if len(text.split()) > 8:
        return True
    return False


def _is_name_candidate(value: Any) -> bool:
    text = str(value or "").strip()
    if not text:
        return False
    if _is_invoice_heading_text(text):
        return False
    if re.search(r"GSTIN|PAN|DATE|TOTAL|AMOUNT|SUBTOTAL|QUANTITY|RATE|VALUE|DESCRIPTION|ITEM", text, flags=re.IGNORECASE):
        return False
    if len(text.split()) > 10:
        return False
    if re.search(r"^[0-9\W]+$", text):
        return False
    return True


def _looks_like_company_name(value: Any) -> bool:
    text = str(value or "").strip()
    if not text:
        return False
    if _is_invoice_heading_text(text):
        return False
    if re.search(r"\b(Traders|Trader|Stores|Store|Services|Enterprises|Pvt|Private|LLP|Company|Co\.|Corporation|Industries|Sons|Brothers|Group)\b", text, flags=re.IGNORECASE):
        return True
    if re.search(r"^[A-Za-z\s]+(Traders|Stores|Services|Enterprises|Company|Industries|Group)$", text, flags=re.IGNORECASE):
        return True
    if re.search(r"^[\p{L}0-9\s]+(ट्रेडर्स|स्टोर्स|स्टोर|सप्लायर्स|एंटरप्राइज़|प्राइवेट|लिमिटेड|कंपनी|उद्योग|ग्रुप)\b", text, flags=re.IGNORECASE):
        return True
    return False


def _is_valid_line_item(item: Any) -> bool:
    if not isinstance(item, dict):
        return False
    description = str(item.get("description") or "").strip()
    amount = str(item.get("amount") or "").strip()
    return bool(description or amount)


def _looks_like_item_description(text: str) -> bool:
    candidate = str(text or "").strip()
    if not candidate:
        return False
    if re.search(r"\b(Address|पता|Payment Terms|भुगतान|Note|नोट|Account|खाता|Mobile|मोबाइल|GSTIN|PAN|Bank|बैंक|श्री|Customer|Buyer|Seller|Vendor|Supplier)\b", candidate, flags=re.IGNORECASE):
        return False
    if re.search(r"\b(invoice|bill|tax invoice|चालान|बीजक|total|subtotal|amount|amount in words|राशि)\b", candidate, flags=re.IGNORECASE):
        return False
    return True


def _is_suspicious_line_item(item: dict[str, Any]) -> bool:
    """Heuristics to detect rows that are actually headers, notes, or vendor blocks."""
    desc = str(item.get("description") or "").strip()
    if not desc:
        return True
    lower = desc.lower()
    # HTML fragments or tags
    if "<" in desc or ">" in desc:
        return True
    # vendor/buyer labels, payment terms, notes
    if re.search(r"\b(shri|श्री|विक्रेता|seller|vendor|भुगतान|payment terms|note|नोट|address|पता|mobile|मोबाइल|account|खाता|gstin|pan)\b", lower, flags=re.IGNORECASE):
        return True
    # very short descriptions or css-like tokens
    if len(desc) < 3:
        return True
    if not _looks_like_item_description(desc):
        return True
    return False


def _safe_parse_invoice(raw_text: str) -> dict[str, Any]:
    invoice = default_invoice()
    try:
        parsed = json.loads(_normalize_json_text(raw_text))
    except Exception:
        invoice["needs_human_review"] = True
        invoice["confidence_score"] = 0.0
        return invoice

    parsed = _find_invoice_object(parsed)
    if not isinstance(parsed, dict):
        invoice["needs_human_review"] = True
        return invoice

    normalized = _normalize_dict_keys(parsed)

    field_aliases = {
        "invoice_number": ["invoice_number", "bill_number", "invoice_no", "bill_no", "invoice number", "bill number", "बीजक क्रमांक", "चालान संख्या", "invoice no", "bill no"],
        "invoice_date": ["invoice_date", "bill_date", "date", "दिनांक", "invoice date", "bill date", "bill_date"],
        "vendor_name": ["vendor_name", "seller_name", "supplier_name", "vendor", "seller", "provider", "vendorname", "sellername", "suppliername", "विक्रेता", "seller"],
        "vendor_address": ["vendor_address", "seller_address", "supplier_address", "vendoraddress", "selleraddress", "supplieraddress", "विक्रेता पता"],
        "buyer_name": ["buyer_name", "customer_name", "purchaser_name", "buyer", "customer", "खरीदार", "customername", "buyername"],
        "buyer_address": ["buyer_address", "customer_address", "buyeraddress", "customeraddress", "खरीदार पता"],
        "gstin": ["gstin", "gst_number", "gstnumber", "gstin"],
        "pan": ["pan", "pan_number", "pannumber"],
        "line_items": ["line_items", "items", "invoice_lines", "products", "lines", "lineitems"],
        "subtotal": ["subtotal", "sub_total", "taxable_amount", "उपयोग", "उप योग", "taxableamount"],
        "cgst": ["cgst", "cgst_amount"],
        "sgst": ["sgst", "sgst_amount"],
        "igst": ["igst", "igst_amount"],
        "total_amount": ["total_amount", "total", "grand_total", "invoice_total", "कुल देय राशि", "कुल राशि", "totalamount"],
        "amount_in_words": ["amount_in_words", "amount_words", "amountinwords"],
        "payment_terms": ["payment_terms", "payment_terms", "paymentterms"],
        "bank_details": ["bank_details", "bank_information", "bank_info", "bankdetails"],
        "confidence_score": ["confidence_score", "confidence", "confidence_score"],
        "needs_human_review": ["needs_human_review", "review_needed", "needs_review"],
    }

    for target_key, aliases in field_aliases.items():
        for alias in aliases:
            if _normalize_key(alias) in normalized:
                invoice[target_key] = normalized[_normalize_key(alias)]
                break

    # fallback for keys with slightly different names or nested values
    for target_key, aliases in field_aliases.items():
        if invoice[target_key]:
            continue
        for key, value in parsed.items():
            if _normalize_key(key) in {_normalize_key(alias) for alias in aliases}:
                invoice[target_key] = value
                break

    invoice = _sanitize_invoice_fields(invoice)

    line_items = invoice.get("line_items")
    if isinstance(line_items, str) and line_items.strip():
        if line_items.strip().startswith("[") or line_items.strip().startswith("{"):
            try:
                parsed_items = json.loads(_normalize_json_text(line_items))
                if isinstance(parsed_items, list):
                    line_items = parsed_items
                elif isinstance(parsed_items, dict):
                    line_items = [parsed_items]
            except Exception:
                pass
        if isinstance(line_items, str):
            invoice["line_items"] = _parse_line_items_from_text(line_items)
        else:
            invoice["line_items"] = line_items
    elif isinstance(line_items, list):
        normalized_items = []
        for item in line_items:
            if isinstance(item, dict):
                clean_item = DEFAULT_LINE_ITEM.copy()
                item_normalized = _normalize_dict_keys(item)
                for key in clean_item:
                    normalized_key = _normalize_key(key)
                    if normalized_key in item_normalized:
                        clean_item[key] = item_normalized[normalized_key]
                    elif key in item:
                        clean_item[key] = item.get(key, "")
                normalized_items.append(clean_item)
            elif isinstance(item, str):
                normalized_items.extend(_parse_line_items_from_text(item))
        invoice["line_items"] = normalized_items
    else:
        invoice["line_items"] = []

    # Filter out suspicious rows that look like headers, vendor blocks, or HTML fragments
    original_items = invoice.get("line_items", []) or []
    filtered = []
    for it in original_items:
        if not _is_valid_line_item(it):
            continue
        if _is_suspicious_line_item(it):
            continue
        filtered.append(it)

    if filtered:
        invoice["line_items"] = filtered
    else:
        # If filtering removed everything, build a cleaned version but skip vendor/header-like fragments
        cleaned = []
        for it in original_items:
            desc = _strip_tags(it.get("description", ""))
            qty = str(it.get("quantity", "") or "").replace(",", "").strip()
            price = str(it.get("unit_price", "") or "").replace(",", "").strip()
            amt = str(it.get("amount", "") or "").replace(",", "").strip()
            if not desc and not amt:
                continue
            # Skip rows that look like vendor/buyer addresses, payment notes, or table headers
            if re.search(r"\b(address|पता|भुगतान|payment|note|नोट|श्री|विक्रेता|खरीदार)\b", desc, flags=re.IGNORECASE):
                continue
            cleaned.append({
                "description": desc,
                "quantity": qty,
                "unit_price": price,
                "tax_rate": "",
                "amount": amt,
            })
        invoice["line_items"] = cleaned

    try:
        invoice["confidence_score"] = float(invoice.get("confidence_score") or 0)
    except (TypeError, ValueError):
        invoice["confidence_score"] = 0.0
    invoice["needs_human_review"] = bool(invoice.get("needs_human_review"))
    return invoice


def _sanitize_invoice_fields(invoice: dict[str, Any]) -> dict[str, Any]:
    if invoice.get("vendor_name") and not _looks_like_name(invoice["vendor_name"]):
        invoice["vendor_name"] = ""
    if invoice.get("buyer_name") and not _looks_like_name(invoice["buyer_name"]):
        invoice["buyer_name"] = ""
    if invoice.get("vendor_address") and not _is_probably_address(invoice["vendor_address"]):
        invoice["vendor_address"] = invoice["vendor_address"] if invoice["vendor_address"] and _looks_like_name(invoice["vendor_address"]) else invoice["vendor_address"]
    if invoice.get("buyer_address") and not _is_probably_address(invoice["buyer_address"]):
        invoice["buyer_address"] = invoice["buyer_address"] if invoice["buyer_address"] and _looks_like_name(invoice["buyer_address"]) else invoice["buyer_address"]

    invoice_number = str(invoice.get("invoice_number") or "").strip()
    invoice_date = str(invoice.get("invoice_date") or "").strip()
    total_amount = str(invoice.get("total_amount") or "").strip()

    if invoice_number and not _looks_like_invoice_number(invoice_number) and _looks_like_date(invoice_number) and not invoice_date:
        invoice["invoice_date"] = invoice_number
        invoice["invoice_number"] = ""
    if invoice_date and not _looks_like_date(invoice_date) and _looks_like_invoice_number(invoice_date) and not invoice_number:
        invoice["invoice_number"] = invoice_date
        invoice["invoice_date"] = ""
    if not total_amount and invoice_number and _looks_like_amount(invoice_number):
        invoice["total_amount"] = invoice_number
        invoice["invoice_number"] = ""

    if invoice.get("invoice_number") and _looks_like_amount(invoice.get("invoice_number")) and _looks_like_invoice_number(invoice_date):
        invoice["invoice_number"], invoice["invoice_date"] = invoice["invoice_date"], invoice["invoice_number"]

    return invoice


def _mock_text_fallback(ocr_text: str) -> dict[str, Any]:
    """Small deterministic fallback for demo continuity when Chat is unavailable."""
    invoice = default_invoice()
    if "INV-1024" in ocr_text:
        invoice.update(
            {
                "invoice_number": "INV-1024",
                "invoice_date": "2026-07-15",
                "vendor_name": "Sharma Traders",
                "vendor_address": "Karol Bagh, New Delhi",
                "buyer_name": "Agarwal Stores",
                "line_items": [
                    {
                        "description": "Cotton cloth",
                        "quantity": "10 meters",
                        "unit_price": "250",
                        "tax_rate": "",
                        "amount": "2500",
                    }
                ],
                "subtotal": "2500",
                "cgst": "225",
                "sgst": "225",
                "total_amount": "2950",
                "payment_terms": "Payment within 15 days",
                "confidence_score": 0.62,
                "needs_human_review": True,
            }
        )
    else:
        invoice["needs_human_review"] = True
    return invoice


def _call_sarvam_chat(ocr_text: str, prompt: str, ocr_result: dict[str, Any] | None = None) -> str:
    try:
        api_key = get_sarvam_api_key()
    except Exception as exc:
        raise InvoiceExtractionError(str(exc)) from exc

    metadata = json.dumps(ocr_result or {}, ensure_ascii=False)[:4000]
    payload = {
        "model": CHAT_MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt.format(ocr_text=ocr_text[:12000], ocr_metadata=metadata)},
        ],
        "temperature": 0.05,
        "max_tokens": 1800,
        "reasoning_effort": "low",
    }

    try:
        import requests

        response = requests.post(CHAT_URL, headers=_headers(api_key), json=payload, timeout=75)
    except Exception as exc:
        raise InvoiceExtractionError(f"Sarvam Chat request failed: {exc}") from exc

    if response.status_code >= 400:
        raise InvoiceExtractionError(f"Sarvam Chat failed with HTTP {response.status_code}: {response.text}")

    try:
        data = response.json()
        content = data["choices"][0]["message"].get("content", "")
    except Exception as exc:
        raise InvoiceExtractionError("Sarvam Chat returned an unexpected response shape.") from exc

    if not str(content).strip():
        raise InvoiceExtractionError("Sarvam Chat returned an empty extraction.")

    global LAST_SARVAM_CHAT_RESPONSE
    LAST_SARVAM_CHAT_RESPONSE = str(content)
    return str(content)


def get_last_sarvam_chat_response() -> str:
    return LAST_SARVAM_CHAT_RESPONSE


def extract_invoice_metadata(ocr_text: str, ocr_result: dict[str, Any] | None = None) -> dict[str, Any]:
    """Extract English invoice metadata from OCR text."""
    if not str(ocr_text or "").strip():
        invoice = default_invoice()
        invoice["needs_human_review"] = True
        return invoice

    invoice = default_invoice()
    fallback = _parse_raw_text_fallback(ocr_text)
    try:
        # Use the structured prompt so the model fills the exact fields each time
        raw = _call_sarvam_chat(ocr_text, STRUCTURED_PROMPT, ocr_result)
        invoice = _safe_parse_invoice(raw)
        invoice = _merge_invoice_fallback(invoice, fallback)
    except Exception:
        invoice = fallback

    key_fields = ["invoice_number", "invoice_date", "vendor_name", "total_amount"]
    if any(not str(invoice.get(field, "")).strip() for field in key_fields):
        invoice["needs_human_review"] = True
    return invoice


def extract_invoice_field_translations(ocr_text: str, ocr_result: dict[str, Any] | None = None) -> list[dict[str, str]]:
    """Extract OCR field translations from OCR text using Sarvam Chat."""
    if not str(ocr_text or "").strip():
        return []

    try:
        raw = _call_sarvam_chat(ocr_text, TRANSLATION_PROMPT, ocr_result)
        return _safe_parse_translation_table(raw)
    except Exception:
        return []


def _safe_parse_translation_table(raw_text: str) -> list[dict[str, str]]:
    try:
        parsed = json.loads(_normalize_json_text(raw_text))
    except Exception:
        return []

    results: list[dict[str, str]] = []
    if isinstance(parsed, dict):
        field_translations = parsed.get("field_translations")
        if isinstance(field_translations, list):
            for item in field_translations:
                if isinstance(item, dict):
                    results.append(
                        {
                            "field_name": str(item.get("field_name", "")).strip(),
                            "ocr_text": str(item.get("ocr_text", "")).strip(),
                            "translation": str(item.get("translation", "")).strip(),
                        }
                    )
    elif isinstance(parsed, list):
        for item in parsed:
            if isinstance(item, dict):
                results.append(
                    {
                        "field_name": str(item.get("field_name", "")).strip(),
                        "ocr_text": str(item.get("ocr_text", "")).strip(),
                        "translation": str(item.get("translation", "")).strip(),
                    }
                )
    return results


def extract_invoice_metadata_json(ocr_text: str, ocr_result: dict[str, Any] | None = None) -> str:
    """Return extracted invoice metadata as a valid JSON string."""
    return json.dumps(extract_invoice_metadata(ocr_text, ocr_result), ensure_ascii=False, indent=2)
