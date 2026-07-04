# AI Invoice Digitization Agent

Standalone Streamlit app for converting Hindi handwritten or printed invoices into English, validated, ERP-ready metadata using Sarvam Document Digitization and Sarvam Chat.

## Run

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
streamlit run app.py
```

## Configuration

Set your Sarvam API key before using live OCR:

```bash
export SARVAM_API_KEY="your_key_here"
```

Optional settings:

```bash
export SARVAM_DOC_LANGUAGE="hi-IN"
export SARVAM_DOC_OUTPUT_FORMAT="md"
export SARVAM_CHAT_MODEL="sarvam-70b"
```

Mock OCR mode is available from the sidebar for demos without an API key.

