import base64
import os
import uuid
from typing import Any, Dict, List, Optional

import fitz
import uvicorn
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from pydantic import BaseModel

import fonts  # noqa: F401
import parser
import splitter
from storage import StorageNotFoundError, get_storage

app = FastAPI()

_allow_origins = os.environ.get("PDF_CORS_ALLOW_ORIGINS", "*").split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in _allow_origins if o.strip()],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _store():
    # Lazy per-call lookup so tests can swap backend via env vars.
    return get_storage()


def _key(file_id: str) -> str:
    return f"{file_id}.pdf"


class Modification(BaseModel):
    page: int
    id: str
    content: str = ""
    bbox: List[float]
    style: Dict[str, Any] = {}
    script: str = "latin"
    type: str = "text"  # text | image | redaction
    image_data: Optional[str] = None
    mask_background: bool = True


class SaveRequest(BaseModel):
    file_id: str
    modifications: List[Modification]


class SplitRequest(BaseModel):
    file_id: str
    direction: str = "vertical"
    ratio: float = 0.5
    exclude_pages: List[int] = []
    rtl: bool = False
    page_range: Optional[List[int]] = None
    deskew: bool = True
    deskew_threshold: float = 0.1
    dpi: int = 300


@app.get("/")
def read_root():
    return {"message": "PDF Editor Backend is running"}


@app.post("/upload")
async def upload_pdf(file: UploadFile = File(...)):
    if file.content_type != "application/pdf":
        raise HTTPException(status_code=400, detail="Invalid file type. Only PDF allowed.")

    file_bytes = await file.read()
    file_id = str(uuid.uuid4())

    try:
        _store().save_bytes(_key(file_id), file_bytes, content_type="application/pdf")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Storage write failed: {e}")

    try:
        data = parser.parse_pdf(file_bytes)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    return {"file_id": file_id, "filename": file.filename, "pages": data["pages"]}


@app.get("/pdf/{file_id}/page/{page_num}/image")
def get_page_image(file_id: str, page_num: int):
    try:
        file_bytes = _store().load_bytes(_key(file_id))
    except StorageNotFoundError:
        raise HTTPException(status_code=404, detail="File not found")

    doc = fitz.open(stream=file_bytes, filetype="pdf")
    try:
        if page_num < 0 or page_num >= len(doc):
            raise HTTPException(status_code=404, detail="Page not found")

        page = doc[page_num]
        pix = page.get_pixmap(dpi=150)
        img_data = pix.tobytes("png")
    finally:
        doc.close()

    return Response(content=img_data, media_type="image/png")


@app.post("/save")
def save_pdf(request: SaveRequest):
    try:
        file_bytes = _store().load_bytes(_key(request.file_id))
    except StorageNotFoundError:
        raise HTTPException(status_code=404, detail="File not found")

    doc = fitz.open(stream=file_bytes, filetype="pdf")
    try:
        for mod in request.modifications:
            if mod.page < 0 or mod.page >= len(doc):
                continue

            page = doc[mod.page]
            rect = fitz.Rect(mod.bbox)

            should_redact = mod.type == "redaction" or (
                mod.mask_background and mod.type in ("text", "image")
            )
            if should_redact and mod.bbox and len(mod.bbox) == 4:
                page.add_redact_annot(rect, fill=(1, 1, 1))
                page.apply_redactions()

            if mod.type == "text":
                fontsize = mod.style.get("fontSize", 12)
                if isinstance(fontsize, str) and "px" in fontsize:
                    fontsize = float(fontsize.replace("px", ""))

                script = mod.script if mod.script else "latin"
                fontname = "helv"

                try:
                    if script != "latin":
                        script_font_map = {
                            "devanagari": "noto-sans-devanagari",
                            "tamil": "noto-sans-tamil",
                            "telugu": "noto-sans-telugu",
                            "bengali": "noto-sans-bengali",
                            "gujarati": "noto-sans-gujarati",
                            "kannada": "noto-sans-kannada",
                            "malayalam": "noto-sans-malayalam",
                            "gurmukhi": "noto-sans-gurmukhi",
                            "odia": "noto-sans-oriya",
                        }
                        chosen = script_font_map.get(script, fontname)
                        try:
                            page.insert_textbox(
                                rect, mod.content,
                                fontsize=fontsize, fontname=chosen, color=(0, 0, 0),
                            )
                        except Exception:
                            page.insert_textbox(
                                rect, mod.content,
                                fontsize=fontsize, fontname="helv", color=(0, 0, 0),
                            )
                    else:
                        font_family = mod.style.get("fontFamily", "Helvetica")
                        if "Times" in font_family:
                            fontname = "tiro"
                        elif "Courier" in font_family:
                            fontname = "cour"
                        page.insert_textbox(
                            rect, mod.content,
                            fontsize=fontsize, fontname=fontname, color=(0, 0, 0),
                        )
                except Exception as e:
                    print(f"Error inserting text: {e}")
                    try:
                        page.insert_textbox(
                            rect, mod.content,
                            fontsize=fontsize, fontname="helv", color=(0, 0, 0),
                        )
                    except Exception as e2:
                        print(f"Error with fallback font: {e2}")

            elif mod.type == "image" and mod.image_data:
                try:
                    _, encoded = mod.image_data.split(",", 1)
                    img_bytes = base64.b64decode(encoded)
                    page.insert_image(rect, stream=img_bytes)
                except Exception as e:
                    print(f"Error inserting image: {e}")

        out_bytes = doc.write()
    finally:
        doc.close()

    return Response(
        content=out_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="edited_{request.file_id}.pdf"'},
    )


@app.post("/split")
def split_pdf_endpoint(request: SplitRequest):
    try:
        file_bytes = _store().load_bytes(_key(request.file_id))
    except StorageNotFoundError:
        raise HTTPException(status_code=404, detail="File not found")

    page_range: Optional[tuple] = None
    if request.page_range and len(request.page_range) == 2:
        page_range = (request.page_range[0], request.page_range[1])

    try:
        result_bytes = splitter.split_pdf(
            file_bytes=file_bytes,
            direction=request.direction,
            ratio=request.ratio,
            exclude_pages=request.exclude_pages,
            rtl=request.rtl,
            page_range=page_range,
            deskew=request.deskew,
            deskew_threshold=request.deskew_threshold,
            dpi=request.dpi,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Split failed: {e}")

    return Response(
        content=result_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="split_{request.file_id}.pdf"'},
    )


if __name__ == "__main__":
    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=True)
