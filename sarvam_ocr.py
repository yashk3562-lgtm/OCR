"""Sarvam OCR / document digitization wrapper."""

from __future__ import annotations

import html
import io
import json
import os
import re
import tempfile
import time
import zipfile
from typing import Any

try:
    from sarvamai import SarvamAI
except ImportError:  # pragma: no cover
    SarvamAI = None

try:
    from .models import MOCK_OCR_TEXT, SUPPORTED_FILE_TYPES
    from .utils import get_sarvam_api_key
except ImportError:  # pragma: no cover - supports direct execution
    from models import MOCK_OCR_TEXT, SUPPORTED_FILE_TYPES
    from utils import get_sarvam_api_key


DEFAULT_TIMEOUT = 60
POLL_INTERVAL_SECONDS = 3
MAX_POLL_ATTEMPTS = 80
SARVAM_API_BASE_URL = os.getenv("SARVAM_API_BASE_URL", "https://api.sarvam.ai").rstrip("/")
DOC_DIGITIZATION_BASE_PATH = "/doc-digitization/job/v1"


class SarvamOCRError(RuntimeError):
    """Raised when OCR processing cannot complete."""


def _file_extension(filename: str) -> str:
    return filename.rsplit(".", 1)[-1].lower() if "." in filename else ""


def _sarvam_headers(api_key: str) -> dict[str, str]:
    return {
        "api-subscription-key": api_key,
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }


def _endpoint(path: str) -> str:
    return f"{SARVAM_API_BASE_URL}{path}"


def _mime_type(filename: str) -> str:
    extension = _file_extension(filename)
    if extension == "pdf":
        return "application/pdf"
    if extension in {"jpg", "jpeg"}:
        return "image/jpeg"
    if extension == "png":
        return "image/png"
    return "application/octet-stream"


def _mock_result(filename: str) -> dict[str, Any]:
    return {
        "mode": "mock",
        "filename": filename,
        "text": MOCK_OCR_TEXT,
        "metadata": {
            "provider": "mock",
            "language_hints": ["Hindi", "English"],
            "notice": "Mock OCR output. Replace with Sarvam Document Digitization when endpoint details are configured.",
        },
        "pages": [{"page_number": 1, "text": MOCK_OCR_TEXT}],
    }


def run_sarvam_ocr(file_bytes: bytes, filename: str) -> dict[str, Any]:
    """Run Sarvam Document Digitization via sarvamai client and return parsed OCR content."""
    extension = _file_extension(filename)
    if extension not in SUPPORTED_FILE_TYPES:
        raise SarvamOCRError(f"Unsupported file type: {extension or 'unknown'}")
    if not file_bytes:
        raise SarvamOCRError("Uploaded file is empty.")

    api_key = get_sarvam_api_key()
    language = os.getenv("SARVAM_DOC_LANGUAGE", "hi-IN").strip() or "hi-IN"
    # Sarvam Document Digitization expects either 'html' or 'md' for output_format
    output_format = os.getenv("SARVAM_DOC_OUTPUT_FORMAT", "html").strip() or "html"
    if output_format.lower() in {"markdown", "md"}:
        output_format = "md"
    elif output_format.lower() not in {"html", "md"}:
        output_format = "html"

    if SarvamAI is None:
        raise SarvamOCRError("sarvamai package is not installed.")

    try:
        client = SarvamAI(api_subscription_key=api_key)
        job = client.document_intelligence.create_job(
            language=language,
            output_format=output_format,
        )

        tmp_path = None
        output_path = None
        with tempfile.NamedTemporaryFile(delete=False, suffix=f".{extension}") as tmp_file:
            tmp_file.write(file_bytes)
            tmp_path = tmp_file.name

        try:
            job.upload_file(tmp_path)
            job.start()
            status = job.wait_until_complete()
            if status.job_state not in {"Completed", "PartiallyCompleted"}:
                raise SarvamOCRError(f"Sarvam document intelligence job failed: {status.job_state}")

            with tempfile.NamedTemporaryFile(delete=False, suffix=".zip") as output_file:
                output_path = output_file.name

            job.download_output(output_path)
            with open(output_path, "rb") as f:
                output_zip = f.read()
        finally:
            if tmp_path:
                try:
                    os.unlink(tmp_path)
                except Exception:
                    pass
            if output_path:
                try:
                    os.unlink(output_path)
                except Exception:
                    pass

        result = _parse_zip_result(output_zip)
        result.setdefault("mode", "sarvam_document_intelligence")
        result.setdefault("job", {"job_id": job.job_id, "job_state": status.job_state})
        result.setdefault("metadata", {
            "provider": "sarvam",
            "model": "SarvamAI document intelligence",
            "language": language,
            "output_format": output_format,
        })
        return result
    except Exception as exc:
        raise SarvamOCRError(f"Sarvam OCR request failed: {exc}") from exc


def _raise_for_response(response: Any, action: str) -> None:
    if response.status_code < 400:
        return
    try:
        payload = response.json()
    except ValueError:
        payload = response.text
    raise SarvamOCRError(f"Could not {action}. HTTP {response.status_code}: {payload}")


def _first_file_url(urls: Any, filename: str | None = None) -> dict[str, Any]:
    if not isinstance(urls, dict) or not urls:
        raise SarvamOCRError("Sarvam did not return a signed file URL.")
    details = urls.get(filename) if filename else None
    if not isinstance(details, dict):
        details = next((value for value in urls.values() if isinstance(value, dict)), None)
    if not details or not details.get("file_url"):
        raise SarvamOCRError("Sarvam signed URL response did not include file_url.")
    return details


def _clean_html_text(text: str) -> str:
    text = html.unescape(str(text or ""))
    # remove style/script blocks and HTML comments
    text = re.sub(r"(?is)<style.*?>.*?</style>", "", text)
    text = re.sub(r"(?is)<script.*?>.*?</script>", "", text)
    text = re.sub(r"<!--.*?-->", "", text)
    # Preserve common block separators as line breaks
    text = re.sub(r"(?i)<\s*(br|p|div|tr|li|th|td|h[1-6])\b[^>]*>", "\n", text)
    text = re.sub(r"(?i)</\s*(p|div|tr|li|th|td|h[1-6])\s*>", "\n", text)
    # remove remaining tags
    text = re.sub(r"<[^>]+>", "", text)
    # normalize line endings and collapse multiple blank lines
    text = re.sub(r"\r\n|\r", "\n", text)
    text = re.sub(r"\n{2,}", "\n\n", text)

    # remove CSS-like declaration lines that may appear as extracted text
    cleaned_lines: list[str] = []
    for line in text.splitlines():
        if re.match(r"^\s*(?:padding|margin|width|min-height|line-height|text-indent|border|font-size|display)\s*:\s*", line, flags=re.IGNORECASE):
            continue
        cleaned_lines.append(line)

    return "\n".join(cleaned_lines).strip()


def _parse_zip_result(zip_bytes: bytes) -> dict[str, Any]:
    markdown_parts: list[str] = []
    html_parts: list[str] = []
    json_payloads: list[Any] = []
    files: dict[str, Any] = {}

    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as archive:
        for name in archive.namelist():
            if name.endswith("/"):
                continue
            raw = archive.read(name)
            lower_name = name.lower()
            if lower_name.endswith(".json"):
                try:
                    parsed = json.loads(raw.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError):
                    parsed = raw.decode("utf-8", errors="replace")
                json_payloads.append(parsed)
                files[name] = parsed
            elif lower_name.endswith(".md"):
                text = raw.decode("utf-8", errors="replace")
                markdown_parts.append(_clean_html_text(text))
                files[name] = text
            elif lower_name.endswith(".html") or lower_name.endswith(".htm"):
                text = raw.decode("utf-8", errors="replace")
                html_parts.append(text)
                files[name] = text
            else:
                files[name] = {"bytes": len(raw)}

    text = "\n\n".join(markdown_parts or [_clean_html_text(html_text) for html_text in html_parts]).strip()
    return {
        "text": text,
        "markdown": "\n\n".join(markdown_parts).strip(),
        "html": "\n\n".join(html_parts).strip(),
        "json_outputs": json_payloads,
        "files": files,
    }


def poll_job(job_id: str) -> dict[str, Any]:
    """Poll a Sarvam Document Digitization job until completion."""
    api_key = get_sarvam_api_key()
    try:
        import requests

        for _ in range(MAX_POLL_ATTEMPTS):
            response = requests.get(
                _endpoint(f"{DOC_DIGITIZATION_BASE_PATH}/{job_id}/status"),
                headers=_sarvam_headers(api_key),
                timeout=DEFAULT_TIMEOUT,
            )
            _raise_for_response(response, "poll Sarvam Document Digitization job")
            data = response.json()
            status = str(data.get("job_state", data.get("status", ""))).lower()
            if status in {"completed", "partiallycompleted", "failed", "error"}:
                return data
            time.sleep(POLL_INTERVAL_SECONDS)
    except SarvamOCRError:
        raise
    except Exception as exc:
        raise SarvamOCRError(f"Sarvam OCR polling failed: {exc}") from exc

    raise SarvamOCRError("Sarvam OCR timed out while waiting for completion.")


def download_result(job_id: str) -> dict[str, Any]:
    """Download and parse the completed Sarvam OCR result ZIP."""
    status_result = poll_job(job_id)
    status = str(status_result.get("job_state", status_result.get("status", ""))).lower()
    if status in {"failed", "error"}:
        raise SarvamOCRError(f"Sarvam OCR job failed: {status_result}")

    api_key = get_sarvam_api_key()
    try:
        import requests

        link_response = requests.post(
            _endpoint(f"{DOC_DIGITIZATION_BASE_PATH}/{job_id}/download-files"),
            headers=_sarvam_headers(api_key),
            timeout=DEFAULT_TIMEOUT,
        )
        _raise_for_response(link_response, "get Sarvam download URL")
        link_data = link_response.json()
        download_details = _first_file_url(link_data.get("download_urls"))

        file_response = requests.get(download_details["file_url"], timeout=DEFAULT_TIMEOUT)
        _raise_for_response(file_response, "download Sarvam OCR ZIP")

        parsed = _parse_zip_result(file_response.content)
        parsed.update(
            {
                "mode": "sarvam_document_digitization",
                "job_id": job_id,
                "status": status_result,
                "download": {
                    "job_state": link_data.get("job_state"),
                    "storage_container_type": link_data.get("storage_container_type"),
                },
                "metadata": {
                    "provider": "sarvam",
                    "model": "Sarvam Vision",
                    "language": os.getenv("SARVAM_DOC_LANGUAGE", "hi-IN"),
                    "output_format": os.getenv("SARVAM_DOC_OUTPUT_FORMAT", "md"),
                    "job_state": status_result.get("job_state"),
                    "pages_processed": _page_metric(status_result, "pages_processed"),
                    "pages_succeeded": _page_metric(status_result, "pages_succeeded"),
                    "pages_failed": _page_metric(status_result, "pages_failed"),
                },
            }
        )
        return parsed
    except Exception as exc:
        if isinstance(exc, SarvamOCRError):
            raise
        raise SarvamOCRError(f"Sarvam OCR result download failed: {exc}") from exc


def _page_metric(status_result: dict[str, Any], key: str) -> int:
    details = status_result.get("job_details")
    if not isinstance(details, list):
        return 0
    return sum(int(item.get(key, 0) or 0) for item in details if isinstance(item, dict))


def _extract_strings_from_json(value: Any) -> list[str]:
    strings: list[str] = []
    if isinstance(value, str):
        strings.append(value)
    elif isinstance(value, dict):
        for key, item in value.items():
            if isinstance(item, str) and key.lower() in {"text", "raw_text", "markdown", "content", "ocr_text", "recognized_text", "value"}:
                strings.append(item)
            else:
                strings.extend(_extract_strings_from_json(item))
    elif isinstance(value, list):
        for item in value:
            strings.extend(_extract_strings_from_json(item))
    return strings


def _extract_text_from_json_outputs(ocr_result: dict[str, Any]) -> str:
    texts: list[str] = []
    json_outputs = ocr_result.get("json_outputs")
    if isinstance(json_outputs, list):
        for item in json_outputs:
            texts.extend(_extract_strings_from_json(item))
    files = ocr_result.get("files")
    if isinstance(files, dict):
        for file_value in files.values():
            if isinstance(file_value, dict):
                texts.extend(_extract_strings_from_json(file_value))
    if texts:
        cleaned = [_clean_html_text(text) for text in texts if text and text.strip()]
        return "\n".join({text.strip() for text in cleaned if text})
    return ""


def extract_text(ocr_result: dict[str, Any]) -> str:
    """Extract the best available text field from an OCR result."""
    if not isinstance(ocr_result, dict):
        return ""

    for key in ("text", "raw_text", "markdown", "content"):
        value = ocr_result.get(key)
        if isinstance(value, str) and value.strip():
            return _clean_html_text(value)

    pages = ocr_result.get("pages")
    if isinstance(pages, list):
        page_text = []
        for page in pages:
            if isinstance(page, dict):
                text = page.get("text") or page.get("markdown") or ""
                if text:
                    page_text.append(str(text))
        if page_text:
            return "\n\n".join(page_text).strip()

    fallback_text = _extract_text_from_json_outputs(ocr_result)
    if fallback_text:
        return fallback_text.strip()

    blocks = ocr_result.get("blocks") or ocr_result.get("layout")
    if isinstance(blocks, list):
        parts = [str(block.get("text", "")) for block in blocks if isinstance(block, dict) and block.get("text")]
        return "\n".join(parts).strip()

    return ""
