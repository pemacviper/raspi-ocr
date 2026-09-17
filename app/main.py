from __future__ import annotations

import shutil
import subprocess
import tempfile
import time
import uuid
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask

app = FastAPI(title="raspi-ocr", version="2.1.0")

MIN_TEXT_CHARS = 100
MIN_CHARS_PER_PAGE = 50
GOOD_TEXT_CHARS = 200
JOB_TTL_SECONDS = 60 * 60
JOB_DIR = Path("/tmp/raspi-ocr-jobs")
JOB_DIR.mkdir(parents=True, exist_ok=True)


def run(cmd: list[str], timeout: int = 600) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        check=True,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def pdf_text(pdf: Path) -> str:
    result = run(["pdftotext", "-layout", str(pdf), "-"])
    return result.stdout or ""


def pdf_pages(pdf: Path) -> int:
    try:
        result = run(["pdfinfo", str(pdf)])
        for line in result.stdout.splitlines():
            if line.lower().startswith("pages:"):
                return int(line.split(":", 1)[1].strip())
    except Exception:
        pass
    return 1


def usable_text(text: str, pages: int) -> bool:
    compact = "".join(text.split())
    chars = len(compact)
    return chars >= MIN_TEXT_CHARS and chars >= max(1, pages) * MIN_CHARS_PER_PAGE


def cleanup_old_jobs() -> None:
    now = time.time()
    for p in JOB_DIR.glob("*.pdf"):
        try:
            if now - p.stat().st_mtime > JOB_TTL_SECONDS:
                p.unlink(missing_ok=True)
        except OSError:
            pass


@app.get("/health")
def health():
    cleanup_old_jobs()
    return {"status": "ok", "service": "ocr", "version": "2.1.0"}


@app.post("/ocr")
async def ocr(file: UploadFile = File(...)):
    cleanup_old_jobs()

    if file.content_type not in (None, "", "application/pdf"):
        raise HTTPException(status_code=415, detail="Es werden nur PDF-Dateien unterstützt.")

    with tempfile.TemporaryDirectory(prefix="raspi-ocr-") as tmp:
        tmpdir = Path(tmp)
        source = tmpdir / "input.pdf"

        with source.open("wb") as f:
            while chunk := await file.read(1024 * 1024):
                f.write(chunk)

        try:
            pages = pdf_pages(source)
            existing_text = pdf_text(source)

            # Ein PDF mit brauchbarer Textschicht ist bereits durchsuchbar.
            # Es wird nicht erneut OCRt und nicht verändert.
            if usable_text(existing_text, pages):
                chars = len("".join(existing_text.split()))
                return {
                    "text": existing_text,
                    "ocr_durchgefuehrt": False,
                    "ocr_qualitaet": "gut" if chars >= GOOD_TEXT_CHARS else "ausreichend",
                    "seiten": pages,
                    "source": "existing_text_layer",
                    "ocr_pdf_available": False,
                    "ocr_job_id": None,
                }

            output = tmpdir / "ocr.pdf"

            run(
                [
                    "ocrmypdf",
                    "--force-ocr",
                    "--deskew",
                    "--rotate-pages",
                    "--clean",
                    "--optimize", "1",
                    "-l", "deu+eng",
                    str(source),
                    str(output),
                ],
                timeout=600,
            )

            text = pdf_text(output)
            chars = len("".join(text.split()))
            job_id = uuid.uuid4().hex
            job_file = JOB_DIR / f"{job_id}.pdf"
            shutil.copy2(output, job_file)

            return {
                "text": text,
                "ocr_durchgefuehrt": True,
                "ocr_qualitaet": "gut" if chars >= GOOD_TEXT_CHARS else "schwach",
                "seiten": pages,
                "source": "ocrmypdf",
                "ocr_pdf_available": True,
                "ocr_job_id": job_id,
            }

        except subprocess.TimeoutExpired:
            raise HTTPException(status_code=504, detail="OCRmyPDF timeout.")
        except subprocess.CalledProcessError as e:
            detail = (e.stderr or e.stdout or str(e)).strip()
            raise HTTPException(status_code=500, detail=f"OCRmyPDF failed: {detail}")
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))


@app.get("/ocr-pdf/{job_id}")
def get_ocr_pdf(job_id: str):
    cleanup_old_jobs()

    if not job_id or any(c not in "0123456789abcdef" for c in job_id.lower()):
        raise HTTPException(status_code=400, detail="Ungültige OCR Job-ID.")

    path = JOB_DIR / f"{job_id}.pdf"
    if not path.exists():
        raise HTTPException(status_code=404, detail="OCR-PDF nicht gefunden oder bereits abgeholt.")

    return FileResponse(
        path,
        media_type="application/pdf",
        filename="ocr.pdf",
        background=BackgroundTask(path.unlink, missing_ok=True),
    )
