# raspi-ocr

Lightweight OCR microservice for document-processing workflows on a Raspberry Pi.

The service is intended to run as a Docker container and can be called by n8n or other applications through a simple HTTP API.

## Features

- FastAPI HTTP API
- PDF text-layer detection
- Existing PDF text extraction with `pdftotext`
- OCR only when required
- OCRmyPDF and Tesseract OCR
- German and English OCR
- Automatic page rotation and deskewing
- OCR quality indication
- Docker / Portainer deployment
- Health endpoint

## API

### Health check

`GET /health`

Example response:

```json
{
  "status": "ok",
  "service": "ocr",
  "version": "2.0.0"
}
```

### OCR

`POST /ocr`

Send the PDF as `multipart/form-data` using the field name `file`.

If the PDF already contains a usable text layer, the existing text is returned without running OCR. Otherwise OCRmyPDF and Tesseract are used.

Example response:

```json
{
  "text": "Extracted document text...",
  "ocr_durchgefuehrt": false,
  "ocr_qualitaet": "gut",
  "seiten": 2,
  "zeichen": 2456,
  "quelle": "existing_text_layer"
}
```

## n8n

When n8n and this service share the same Docker network, the OCR endpoint can be called using:

`http://ocr:8000/ocr`

No host port needs to be published.

## Portainer

The included `docker-compose.yml` is intended for deployment as a Portainer Git repository stack. It expects an existing external Docker network named `proxy`.

## Security

The service does not require or store API keys or credentials. Do not commit `.env` files, API keys, credentials, scanned documents, or private test PDFs.

## Privacy

Uploaded PDFs are processed in a temporary directory and deleted after the request completes. The service does not persist uploaded documents.
