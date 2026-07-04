# AI Invoice Digitization Agent

Standalone Streamlit app for converting Hindi handwritten or printed invoices into English, validated, ERP-ready metadata using Sarvam Document Digitization and Sarvam Chat.

## Features

- Upload PDF, PNG, JPG or JPEG invoices
- Extract invoice metadata, line items, GSTIN, PAN, totals, and addresses
- Translate Hindi/Hinglish text into English
- Validate invoice fields and generate ERP payloads
- Supports mock OCR mode for demos without a Sarvam API key

## Architecture

```text
User
  |
  v
Streamlit UI (app.py)
  |
  +--> Sarvam OCR wrapper (sarvam_ocr.py)
  |      |
  |      +--> Sarvam Document Intelligence
  |      +--> OCR text and metadata
  |
  +--> Extraction agent (extraction_agent.py)
  |      |
  |      +--> Sarvam Chat structured extraction
  |      +--> Regex fallback parser
  |      +--> Optional field translations
  |
  +--> Validation agent (validation_agent.py)
  |      |
  |      +--> Required field checks
  |      +--> Amount and review checks
  |
  +--> Export utilities (export_utils.py)
         |
         +--> Invoice JSON
         +--> Line item CSV
         +--> ERP payload JSON
```

### Components

- `app.py`: Streamlit entry point. Handles upload, preview, processing controls, result tabs, and downloads.
- `sarvam_ocr.py`: Runs Sarvam Document Intelligence for live OCR, parses OCR ZIP outputs, and supports text extraction from multiple response shapes.
- `extraction_agent.py`: Converts OCR text into normalized invoice metadata using Sarvam Chat, with local fallback parsing when the model call fails or returns incomplete fields.
- `validation_agent.py`: Applies AP validation checks and marks invoices that need human review.
- `export_utils.py`: Builds ERP-ready payloads and downloadable JSON/CSV files.
- `models.py`: Stores shared schema defaults, field labels, supported file types, and mock OCR text.
- `utils.py`: Contains shared helpers for secrets, text cleanup, and INR formatting.

### Processing Flow

1. User uploads an invoice or enables mock OCR in the Streamlit sidebar.
2. The OCR layer returns readable invoice text and provider metadata.
3. The extraction layer creates normalized English invoice metadata.
4. Validation checks required fields, numeric consistency, and review status.
5. The app displays overview, line items, validation output, ERP payload, and downloads.

## Setup

```bash
cd /Users/yash.khandelwal/Desktop/invoice-ocr-agent
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Run

```bash
source .venv/bin/activate
streamlit run app.py --server.port 8501
```

Then open:

- http://localhost:8501

## Configuration

Set the Sarvam API key to use live OCR and chat extraction:

```bash
export SARVAM_API_KEY="your_key_here"
```

Optional environment variables:

```bash
export SARVAM_DOC_LANGUAGE="hi-IN"
export SARVAM_DOC_OUTPUT_FORMAT="md"
export SARVAM_CHAT_MODEL="sarvam-105b"
```

## Notes

- The app uses the `sarvamai` SDK for document digitization and Sarvam Chat for structured extraction.
- If you do not have a valid API key, enable `Use mock OCR output` in the sidebar for demo behavior.

## Repository

Source pushed to GitHub: https://github.com/yashk3562-lgtm/OCR.git
