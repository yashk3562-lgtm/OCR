"""Shared helper functions for the Invoice OCR Agent."""

from __future__ import annotations

import os
import re
from typing import Any


def get_sarvam_api_key() -> str:
    """Resolve the Sarvam API key from Streamlit secrets or the environment."""
    try:
        import streamlit as st

        value = st.secrets.get("SARVAM_API_KEY", "")
        if value:
            return str(value).strip()
    except Exception:
        pass

    value = os.getenv("SARVAM_API_KEY", "").strip()
    if value:
        return value

    raise RuntimeError("Missing SARVAM_API_KEY. Add it to Streamlit secrets or the environment.")


def safe_get(data: dict[str, Any], key: str, default: str = "") -> str:
    """Return a string field from a dictionary with a stable default."""
    value = data.get(key, default) if isinstance(data, dict) else default
    return default if value is None else str(value)


def clean_text(value: str) -> str:
    """Normalize whitespace in OCR or model output."""
    return re.sub(r"\s+", " ", str(value or "")).strip()


def format_currency(value: str | int | float | None) -> str:
    """Format a numeric value as INR when possible, otherwise return raw text."""
    if value is None or value == "":
        return ""
    try:
        number = float(str(value).replace(",", "").replace("₹", "").strip())
    except ValueError:
        return str(value)
    return f"INR {number:,.2f}"

