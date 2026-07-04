# AI Invoice Digitization Agent

Standalone Streamlit app for converting Hindi handwritten or printed invoices into English, validated, ERP-ready metadata using Sarvam Document Digitization and Sarvam Chat.

## Features

- Upload PDF, PNG, JPG or JPEG invoices
- Extract invoice metadata, line items, GSTIN, PAN, totals, and addresses
- Translate Hindi/Hinglish text into English
- Validate invoice fields and generate ERP payloads
- Supports mock OCR mode for demos without a Sarvam API key

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

