import asyncio
import json
import logging
import os
import re
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi import FastAPI, Request, Response
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

LOCAL_TZ = ZoneInfo("Europe/Berlin")
logger = logging.getLogger("garden-camera")
logging.basicConfig(level=logging.INFO)

IMAGES_DIR = Path(os.getenv("IMAGES_DIR", "/data/images"))
IMAGES_DIR.mkdir(parents=True, exist_ok=True)
STATUS_FILE = IMAGES_DIR.parent / "status.json"

DETECTION_THRESHOLD = float(os.getenv("DETECTION_THRESHOLD", "0.5"))
CANDIDATE_LABELS = [
    "a photo of a grey heron standing in a garden",
    "a photo of a garden with no birds",
    "a photo of a garden with plants and flowers",
]

clip_model = None
clip_processor = None

app = FastAPI()
app.mount("/static", StaticFiles(directory="static"), name="static")


def load_model():
    global clip_model, clip_processor
    from transformers import CLIPModel, CLIPProcessor
    logger.info("CLIP-Modell wird geladen...")
    clip_model = CLIPModel.from_pretrained("openai/clip-vit-base-patch32")
    clip_processor = CLIPProcessor.from_pretrained("openai/clip-vit-base-patch32")
    logger.info("CLIP-Modell geladen")


def detect_heron(image_path: Path) -> dict:
    import torch
    from PIL import Image

    image = Image.open(image_path).convert("RGB")
    inputs = clip_processor(text=CANDIDATE_LABELS, images=image, return_tensors="pt", padding=True)

    with torch.no_grad():
        outputs = clip_model(**inputs)

    logits = outputs.logits_per_image[0]
    probs = logits.softmax(dim=0).tolist()

    heron_score = probs[0]
    result = {
        "heron_detected": heron_score >= DETECTION_THRESHOLD,
        "heron_score": round(heron_score, 3),
        "analyzed_at": datetime.now(LOCAL_TZ).isoformat(),
    }

    sidecar = image_path.with_suffix(".json")
    sidecar.write_text(json.dumps(result))
    return result


async def detection_task():
    loop = asyncio.get_event_loop()
    while True:
        await asyncio.sleep(10)
        if clip_model is None:
            continue
        jpg_files = sorted(IMAGES_DIR.glob("*.jpg"))
        for jpg in jpg_files:
            sidecar = jpg.with_suffix(".json")
            if sidecar.exists():
                continue
            try:
                result = await loop.run_in_executor(None, detect_heron, jpg)
                status = "Fischreiher erkannt!" if result["heron_detected"] else "kein Fischreiher"
                logger.info("Analyse %s: %s (Score: %.3f)", jpg.name, status, result["heron_score"])
            except Exception:
                logger.exception("Fehler bei Analyse von %s", jpg.name)


def read_sidecar(jpg_path: Path) -> dict | None:
    sidecar = jpg_path.with_suffix(".json")
    if not sidecar.exists():
        return None
    try:
        return json.loads(sidecar.read_text())
    except Exception:
        return None


@app.post("/upload")
async def upload_image(request: Request):
    body = await request.body()
    if not body:
        return Response(status_code=400, content="Empty body")

    timestamp = datetime.now(LOCAL_TZ).strftime("%Y-%m-%d_%H-%M-%S")
    filename = f"{timestamp}.jpg"
    filepath = IMAGES_DIR / filename

    # Avoid overwriting if multiple uploads in same second
    counter = 1
    while filepath.exists():
        filename = f"{timestamp}_{counter}.jpg"
        filepath = IMAGES_DIR / filename
        counter += 1

    filepath.write_bytes(body)

    battery = request.headers.get("X-Battery-Voltage")
    if battery:
        try:
            status = {"battery_voltage": float(battery), "last_upload": timestamp}
            STATUS_FILE.write_text(json.dumps(status))
        except ValueError:
            logger.warning("Ignoriere ungueltigen X-Battery-Voltage Header: %r", battery)

    return {"filename": filename, "size": len(body)}


@app.get("/images")
async def list_images(limit: int = 0, hour: str = ""):
    files = sorted(IMAGES_DIR.glob("*.jpg"), reverse=True)
    if hour:
        files = [f for f in files if f.name.startswith(hour)]
    if limit > 0:
        files = files[:limit]
    result = []
    for f in files:
        entry = {"filename": f.name, "size": f.stat().st_size}
        detection = read_sidecar(f)
        if detection:
            entry["heron_detected"] = detection["heron_detected"]
            entry["heron_score"] = detection["heron_score"]
        else:
            entry["heron_detected"] = None
            entry["heron_score"] = None
        result.append(entry)
    return result


@app.get("/detections")
async def list_detections():
    results = []
    for f in sorted(IMAGES_DIR.glob("*.jpg"), reverse=True):
        detection = read_sidecar(f)
        if detection and detection.get("heron_detected"):
            results.append({
                "filename": f.name,
                "size": f.stat().st_size,
                "heron_score": detection["heron_score"],
                "analyzed_at": detection.get("analyzed_at"),
            })
    return results


@app.get("/hours")
async def list_hours():
    files = sorted(IMAGES_DIR.glob("*.jpg"), reverse=True)
    hours: dict[str, int] = {}
    for f in files:
        key = f.name[:13]
        hours[key] = hours.get(key, 0) + 1
    return [{"hour": k, "count": v} for k, v in hours.items()]


@app.get("/status")
async def get_status():
    if not STATUS_FILE.exists():
        return {"battery_voltage": None, "last_upload": None}
    return json.loads(STATUS_FILE.read_text())


@app.get("/images/{filename}")
async def get_image(filename: str):
    filepath = IMAGES_DIR / filename
    if not filepath.resolve().is_relative_to(IMAGES_DIR.resolve()):
        return Response(status_code=403, content="Forbidden")
    if not filepath.exists():
        return Response(status_code=404, content="Not found")
    return FileResponse(filepath, media_type="image/jpeg")


@app.get("/")
async def index():
    return FileResponse("static/index.html")


@app.get("/hour.html")
async def hour_page():
    return FileResponse("static/hour.html")


@app.get("/heron.html")
async def heron_page():
    return FileResponse("static/heron.html")


def cleanup_old_images():
    cutoff = datetime.now(LOCAL_TZ) - timedelta(days=2)
    deleted = 0
    for f in list(IMAGES_DIR.glob("*.jpg")) + list(IMAGES_DIR.glob("*.json")):
        match = re.match(r"(\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2})", f.name)
        if not match:
            continue
        ts = datetime.strptime(match.group(1), "%Y-%m-%d_%H-%M-%S").replace(tzinfo=LOCAL_TZ)
        if ts < cutoff:
            f.unlink()
            deleted += 1
    logger.info("Cleanup: %d alte Dateien gelöscht", deleted)


async def cleanup_task():
    while True:
        await asyncio.sleep(24 * 60 * 60)
        cleanup_old_images()


@app.on_event("startup")
async def startup():
    cleanup_old_images()
    asyncio.create_task(cleanup_task())
    load_model()
    asyncio.create_task(detection_task())
