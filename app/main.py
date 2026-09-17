import subprocess
import tempfile
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile


app = FastAPI(
    title="Raspberry Pi OCR Service",
    description="Internal OCR service for n8n document workflows",
    version="2.0.0",
)

MIN_TEXT_CHARS = 100
MIN_CHARS_PER_PAGE = 50
GOOD_CHARS_PER_PAGE = 200


def run_command(
    command: list[str],
    timeout: int = 600,
) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(
            f"Command timed out after {timeout} seconds"
        ) from exc


def extract_text(pdf_path: Path) -> str:
    result = run_command(
        [
            "pdftotext",
            "-layout",
            str(pdf_path),
            "-",
        ]
    )

    if result.returncode != 0:
        return ""

    return result.stdout.strip()


def count_pages(pdf_path: Path) -> int:
    result = run_command(
        [
            "pdfinfo",
            str(pdf_path),
        ]
    )

    if result.returncode != 0:
        return 0

    for line in result.stdout.splitlines():
        if line.startswith("Pages:"):
            try:
                return int(line.split(":", 1)[1].strip())
            except ValueError:
                return 0

    return 0


def text_is_usable(text: str, pages: int) -> bool:
    clean_text = text.strip()

    if not clean_text:
        return False

    effective_pages = max(pages, 1)
    chars_per_page = len(clean_text) / effective_pages

    return (
        len(clean_text) >= MIN_TEXT_CHARS
        and chars_per_page >= MIN_CHARS_PER_PAGE
    )


def determine_quality(text: str, pages: int) -> str:
    clean_text = text.strip()

    if not clean_text:
        return "schwach"

    effective_pages = max(pages, 1)
    chars_per_page = len(clean_text) / effective_pages

    if chars_per_page >= GOOD_CHARS_PER_PAGE:
        return "gut"

    return "schwach"


@app.get("/")
def root():
    return {
        "service": "ocr",
        "version": "2.0.0",
        "status": "ok",
    }


@app.get("/health")
def health():
    return {
        "status": "ok",
        "service": "ocr",
        "version": "2.0.0",
    }


@app.post("/ocr")
async def ocr(file: UploadFile = File(...)):
    filename = file.filename or "document.pdf"

    if not filename.lower().endswith(".pdf"):
        raise HTTPException(
            status_code=400,
            detail="Only PDF files are supported.",
        )

    with tempfile.TemporaryDirectory(prefix="ocr-") as temp_dir:
        temp_path = Path(temp_dir)
        input_pdf = temp_path / "input.pdf"
        output_pdf = temp_path / "output.pdf"

        try:
            content = await file.read()
        except Exception as exc:
            raise HTTPException(
                status_code=500,
                detail=f"Could not read uploaded PDF: {exc}",
            ) from exc

        if not content:
            raise HTTPException(
                status_code=400,
                detail="Uploaded PDF is empty.",
            )

        try:
            input_pdf.write_bytes(content)
        except Exception as exc:
            raise HTTPException(
                status_code=500,
                detail=f"Could not store uploaded PDF: {exc}",
            ) from exc

        pages = count_pages(input_pdf)
        existing_text = extract_text(input_pdf)

        if text_is_usable(existing_text, pages):
            return {
                "text": existing_text,
                "ocr_durchgefuehrt": False,
                "ocr_qualitaet": determine_quality(existing_text, pages),
                "seiten": pages,
                "zeichen": len(existing_text),
                "quelle": "existing_text_layer",
            }

        try:
            ocr_result = run_command(
                [
                    "ocrmypdf",
                    "--force-ocr",
                    "--deskew",
                    "--rotate-pages",
                    "--clean",
                    "--optimize",
                    "1",
                    "-l",
                    "deu+eng",
                    str(input_pdf),
                    str(output_pdf),
                ],
                timeout=600,
            )
        except RuntimeError as exc:
            raise HTTPException(
                status_code=504,
                detail=str(exc),
            ) from exc

        if ocr_result.returncode != 0:
            error_message = ocr_result.stderr[-2000:].strip()
            raise HTTPException(
                status_code=500,
                detail=f"OCRmyPDF failed: {error_message}",
            )

        ocr_text = extract_text(output_pdf)

        if not ocr_text:
            raise HTTPException(
                status_code=500,
                detail="OCR completed but no text could be extracted.",
            )

        pages_after_ocr = count_pages(output_pdf)

        return {
            "text": ocr_text,
            "ocr_durchgefuehrt": True,
            "ocr_qualitaet": determine_quality(
                ocr_text,
                pages_after_ocr,
            ),
            "seiten": pages_after_ocr,
            "zeichen": len(ocr_text),
            "quelle": "ocrmypdf_tesseract",
        }
