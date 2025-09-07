# main.py
import os
from io import BytesIO
from pathlib import Path
from datetime import datetime
from typing import List, Optional

from fastapi import FastAPI, Request, UploadFile, File, Form, HTTPException
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.middleware.cors import CORSMiddleware

from dotenv import load_dotenv
from google import genai
from google.genai import types
from PIL import Image
import glob

import json
import re
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types
from nano_banana_prompt_agent.agent import root_agent  # your Agent(...) from the question
import os
from dotenv import load_dotenv

# ---------------- Paths & Env ----------------
BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
TEMPLATES_DIR = BASE_DIR / "templates"
OUTPUT_DIR = BASE_DIR / "generated"

GOOGLE_STYLES_DIR = BASE_DIR / "GoogleStyles"
MANGA_STYLES_DIR = BASE_DIR / "MangaStyles"
DEFAULT_CHARACTER_PATH = BASE_DIR / "Default_Character" / "fornite_banana.png"

OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# Load .env
load_dotenv(dotenv_path=BASE_DIR / ".env")

API_KEY = os.environ.get("GEMINI_API_KEY")
if not API_KEY:
    raise RuntimeError("GEMINI_API_KEY not set.")

MODEL_NAME = "gemini-2.5-flash-image-preview"

# ---------------- App ----------------
app = FastAPI(title="Gemini Image Generator (Google + Manga)")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # tighten for prod
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

# ---------------- Google Client ----------------
client = genai.Client(api_key=API_KEY)

# ---------------- Prompts ----------------
DEFAULT_PROMPT = """
Create a infographic in the isometric, colorful, and illustrative style of the provided images
that convince you why choosing to go for research, provide many reasons if you can. If you add numbers, it should be correctly added and sequentially added.
Don't add any watermarks or anything that says Google Developer Clubs.
""".strip()

MANGA_DEFAULT_PROMPT = """
Create a single manga page (right-to-left layout) in classic manga style: 4 panels, crisp ink lineart, grayscale screentones, soft gradients, high contrast, subtle paper texture.
Use ONLY ENGLISH text (Latin letters); no Japanese characters. Hand-lettered speech balloons and small caption boxes. No watermark.
CAST & SETTING: Two student researchers in lab coats: (1) confident girl with long hair; (2) calm boy with glasses.
Campus lawn + lab building. Include diegetic UI overlays that suggest search/map/translate/drive (no exact logos).
Typography: short, legible English text in balloons; 1–2 words for SFX. Ratio ~1300x1846.
""".strip()


# ---------------- Utilities ----------------
def _img_to_part(img: Image.Image, fmt: str = "PNG") -> types.Part:
    """Convert PIL image to a types.Part with inline_data (data + mime_type)."""
    buff = BytesIO()
    img.save(buff, format=fmt)
    return types.Part(inline_data=types.Blob(
        mime_type=f"image/{fmt.lower()}",
        data=buff.getvalue()
    ))


def _load_preset_parts_from_dir(folder: Path, patterns=("*.png", "*.jpg", "*.jpeg")) -> List[types.Part]:
    """Load all images in a folder as types.Part; sorted by name for determinism."""
    parts: List[types.Part] = []
    if not folder.exists():
        print(f"[warn] missing folder: {folder}")
        return parts
    files = []
    for pat in patterns:
        files.extend(glob.glob(str(folder / pat)))
    for p in sorted(files):
        try:
            img = Image.open(p).convert("RGB")
            parts.append(_img_to_part(img, fmt="PNG"))
        except Exception as e:
            print(f"[warn] failed to open {p}: {e}")
    return parts


def _load_uploads(uploaded: Optional[List[UploadFile]]) -> List[types.Part]:
    """Safely read uploaded images; skip empty fields; return as Parts."""
    if not uploaded:
        return []
    parts: List[types.Part] = []
    for f in uploaded:
        if not f or not getattr(f, "filename", None):
            continue
        data = f.file.read()
        if not data:
            continue
        try:
            img = Image.open(BytesIO(data)).convert("RGB")
            parts.append(_img_to_part(img, fmt="PNG"))
        except Exception as e:
            raise HTTPException(
                status_code=400,
                detail=f"Could not read uploaded image '{getattr(f, 'filename', '') or '(unknown)'}': {e}"
            )
    return parts


def _extract_image_bytes(gen_response) -> bytes:
    try:
        parts = getattr(gen_response.candidates[0].content, "parts", [])
        for part in parts:
            if getattr(part, "inline_data", None):
                return part.inline_data.data
    except Exception as e:
        print("[warn] response parsing error:", e)
    return b""


def _save_png(image_bytes: bytes) -> Path:
    ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S%f")
    out_path = OUTPUT_DIR / f"generated_{ts}.png"
    img = Image.open(BytesIO(image_bytes))
    img.save(out_path)
    return out_path


def extract_json_block(text: str) -> str | None:
    """
    Try to recover a JSON object from a model response that may include
    Markdown fences, prose, or other wrappers.
    """
    # 1) Fast path: direct JSON
    try:
        json.loads(text)
        return text
    except Exception:
        pass

    # 2) Strip Markdown code fences ```json ... ``` or ``` ... ```
    fence_match = re.search(r"```(?:json)?\s*(.*?)\s*```", text, re.DOTALL | re.IGNORECASE)
    if fence_match:
        candidate = fence_match.group(1).strip()
        try:
            json.loads(candidate)
            return candidate
        except Exception:
            text = candidate  # fall through

    # 3) Fallback: extract the first balanced { ... } object
    start = text.find("{")
    while start != -1:
        depth = 0
        in_str = False
        escape = False
        for i, ch in enumerate(text[start:], start=start):
            if in_str:
                if escape:
                    escape = False
                elif ch == "\\":
                    escape = True
                elif ch == '"':
                    in_str = False
            else:
                if ch == '"':
                    in_str = True
                elif ch == "{":
                    depth += 1
                elif ch == "}":
                    depth -= 1
                    if depth == 0:
                        candidate = text[start:i + 1]
                        try:
                            json.loads(candidate)
                            return candidate
                        except Exception:
                            break
        start = text.find("{", start + 1)
    return None


def rewrite_prompt(raw_prompt: str) -> str:
    """
    Run the root_agent pipeline synchronously to rewrite a user prompt.
    Returns the rewritten prompt, or the original raw prompt if rewriting fails.
    """
    APP_NAME = "prompt_rewriter_app"
    USER_ID = "local_user"
    SESSION_ID = "test_session"

    session_service = InMemorySessionService()
    session_service.create_session(
        app_name=APP_NAME, user_id=USER_ID, session_id=SESSION_ID
    )
    runner = Runner(
        agent=root_agent,
        app_name=APP_NAME,
        session_service=session_service,
    )

    content = types.Content(role="user", parts=[types.Part(text=raw_prompt)])
    final_text = None
    for event in runner.run(user_id=USER_ID, session_id=SESSION_ID, new_message=content):
        if event.is_final_response():
            if event.content and event.content.parts and getattr(event.content.parts[0], "text", None):
                final_text = event.content.parts[0].text.strip()
            break

    if not final_text:
        return raw_prompt

    parsed = extract_json_block(final_text)
    if not parsed:
        return raw_prompt

    try:
        obj = json.loads(parsed)
        return obj.get("rewritten_prompt", raw_prompt)
    except Exception:
        return raw_prompt


# ---------------- Preloaded style refs ----------------
PRESET_GOOGLE_PARTS = _load_preset_parts_from_dir(GOOGLE_STYLES_DIR)
PRESET_MANGA_PARTS = _load_preset_parts_from_dir(MANGA_STYLES_DIR)


# ---------------- Routes ----------------
@app.get("/", response_class=HTMLResponse)
async def read_root(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})


@app.get("/files/{filename}")
async def serve_generated(filename: str):
    path = OUTPUT_DIR / filename
    if not path.exists():
        raise HTTPException(status_code=404, detail="File not found.")
    return FileResponse(str(path), media_type="image/png", filename=filename)


@app.get("/api/ping")
def ping():
    return {"status": "ok"}


@app.get("/api/get-default-prompt")
def get_default_prompt():
    return {"prompt": DEFAULT_PROMPT}


@app.get("/api/get-manga-prompt")
def get_manga_prompt():
    return {"prompt": MANGA_DEFAULT_PROMPT}


# --- Default Google flow ---
@app.post("/api/generate-default")
async def generate_default_api():
    if not PRESET_GOOGLE_PARTS:
        raise HTTPException(status_code=500, detail="Google style images missing.")
    try:
        response = client.models.generate_content(
            model=MODEL_NAME,
            contents=[*PRESET_GOOGLE_PARTS, DEFAULT_PROMPT],
        )
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Gemini generation failed: {e}")

    image_bytes = _extract_image_bytes(response)
    if not image_bytes:
        raise HTTPException(status_code=502, detail="No image bytes returned from Gemini.")
    out_path = _save_png(image_bytes)
    return FileResponse(str(out_path), media_type="image/png", filename=out_path.name)


# --- Default Manga flow ---
@app.post("/api/generate-manga-default")
async def generate_manga_default_api():
    if not PRESET_MANGA_PARTS:
        raise HTTPException(status_code=500, detail="Manga style images missing.")
    try:
        response = client.models.generate_content(
            model=MODEL_NAME,
            contents=[*PRESET_MANGA_PARTS, MANGA_DEFAULT_PROMPT],
        )
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Gemini generation failed: {e}")

    image_bytes = _extract_image_bytes(response)
    if not image_bytes:
        raise HTTPException(status_code=502, detail="No image bytes returned from Gemini.")
    out_path = _save_png(image_bytes)
    return FileResponse(str(out_path), media_type="image/png", filename=out_path.name)


# --- General form-driven endpoint ---
@app.post("/api/generate-image")
async def generate_image_api(
        prompt: str = Form(DEFAULT_PROMPT),
        include_default_google_styles: bool = Form(True),
        include_default_character: bool = Form(False),
        include_manga_styles: bool = Form(False),  # NEW
        style_images: Optional[List[UploadFile]] = File(None),
        # temperature: float = Form(1),  # Default to a moderate temperature
        # top_p: float = Form(0.95),        # Default to a moderate top_p
        # output_length: int = Form(8192)  # Default to a reasonable output length
):
    parts: List[types.Part] = []

    # user uploads first
    parts.extend(_load_uploads(style_images))

    # optional default character
    if include_default_character and DEFAULT_CHARACTER_PATH.exists():
        try:
            char_img = Image.open(DEFAULT_CHARACTER_PATH).convert("RGB")
            parts.append(_img_to_part(char_img, fmt="PNG"))
        except Exception as e:
            print(f"[warn] failed to load default character: {e}")

    # optional manga styles
    if include_manga_styles:
        parts.extend(PRESET_MANGA_PARTS)
        rewritten_prompt = rewrite_prompt(prompt)
        print(rewritten_prompt)
        parts.append(rewritten_prompt or DEFAULT_PROMPT)

    # optional google styles
    if include_default_google_styles:
        parts.extend(PRESET_GOOGLE_PARTS)
        parts.extend(prompt or DEFAULT_PROMPT)

    # if (not include_manga_styles) or (not include_default_google_styles):
    #     # prompt last
    #     rewritten_prompt = rewrite_prompt(prompt)
    #     print(rewritten_prompt)
    #     parts.append(rewritten_prompt or DEFAULT_PROMPT)

    parts.append(prompt or DEFAULT_PROMPT)

    try:
        response = client.models.generate_content(
            model=MODEL_NAME,
            contents=parts,
            # config=types.GenerateContentConfig(
            #     temperature=temperature,
            #     top_p=top_p,
            #     max_output_tokens=output_length
            # )
        )
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Gemini generation failed: {e}")

    image_bytes = _extract_image_bytes(response)
    if not image_bytes:
        raise HTTPException(status_code=502, detail="No image bytes returned from Gemini.")
    out_path = _save_png(image_bytes)
    return FileResponse(str(out_path), media_type="image/png", filename=out_path.name)


# Local dev
if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=True)
