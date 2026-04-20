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

DETECTION_THRESHOLD = float(os.getenv("DETECTION_THRESHOLD", "0.6"))
OTHER_THRESHOLD = float(os.getenv("OTHER_THRESHOLD", "0.6"))
HERON_LABELS = [
    "a grey heron bird",
    "the body of a grey heron bird",
    "the head and neck of a grey heron bird",
]
OTHER_ANIMAL_LABELS = [
    "a cat",
    "a dog",
    "a fox",
    "a squirrel",
    "a hedgehog",
    "a rabbit",
    "a small songbird",
    "a crow or magpie",
    "a duck",
]
NON_HERON_LABELS = [
    "a wooden deck or patio in a garden",
    "trees and bushes in a garden",
    "green plants and grass",
    "a garden pond with water and irises, no bird",
    "an empty garden with no animals",
    "garden furniture or decorations",
    "the sky above a garden",
]
CANDIDATE_LABELS = HERON_LABELS + OTHER_ANIMAL_LABELS + NON_HERON_LABELS
HERON_IDX = list(range(len(HERON_LABELS)))
OTHER_IDX = list(range(len(HERON_LABELS), len(HERON_LABELS) + len(OTHER_ANIMAL_LABELS)))

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


def _build_crops(image):
    w, h = image.size
    crops = [image]
    tile_w, tile_h = w // 2, h // 2
    step_x, step_y = w // 4, h // 4
    for iy in range(3):
        for ix in range(3):
            x = ix * step_x
            y = iy * step_y
            crops.append(image.crop((x, y, x + tile_w, y + tile_h)))
    return crops


def detect_heron(image_path: Path) -> dict:
    import torch
    from PIL import Image

    image = Image.open(image_path).convert("RGB")
    crops = _build_crops(image)

    inputs = clip_processor(text=CANDIDATE_LABELS, images=crops, return_tensors="pt", padding=True)

    with torch.no_grad():
        outputs = clip_model(**inputs)

    probs = outputs.logits_per_image.softmax(dim=1)
    heron_max_per_crop = probs[:, HERON_IDX].max(dim=1).values
    heron_score = float(heron_max_per_crop.max().item())

    other_probs = probs[:, OTHER_IDX]
    other_max_per_crop, other_argmax_per_crop = other_probs.max(dim=1)
    best_crop = int(other_max_per_crop.argmax().item())
    other_score = float(other_max_per_crop[best_crop].item())
    other_label = OTHER_ANIMAL_LABELS[int(other_argmax_per_crop[best_crop].item())]

    heron_detected = heron_score >= DETECTION_THRESHOLD
    other_detected = (not heron_detected) and other_score >= OTHER_THRESHOLD

    result = {
        "heron_detected": heron_detected,
        "heron_score": round(heron_score, 3),
        "other_animal_detected": other_detected,
        "other_animal_score": round(other_score, 3),
        "other_animal_label": other_label,
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
            existing = read_sidecar(jpg)
            if existing is not None and "other_animal_detected" in existing:
                continue
            try:
                result = await loop.run_in_executor(None, detect_heron, jpg)
                if result["heron_detected"]:
                    status = "Fischreiher erkannt!"
                elif result["other_animal_detected"]:
                    status = f"Sonstiges Tier erkannt ({result['other_animal_label']})"
                else:
                    status = "kein Tier"
                logger.info(
                    "Analyse %s: %s (Reiher: %.3f, Sonstige: %.3f)",
                    jpg.name, status, result["heron_score"], result["other_animal_score"],
                )
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

    capture_time = request.headers.get("X-Capture-Time")
    timestamp = capture_time if capture_time else datetime.now(LOCAL_TZ).strftime("%Y-%m-%d_%H-%M-%S")
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
            entry["other_animal_detected"] = detection.get("other_animal_detected")
            entry["other_animal_score"] = detection.get("other_animal_score")
            entry["other_animal_label"] = detection.get("other_animal_label")
        else:
            entry["heron_detected"] = None
            entry["heron_score"] = None
            entry["other_animal_detected"] = None
            entry["other_animal_score"] = None
            entry["other_animal_label"] = None
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
    kept = 0
    for f in list(IMAGES_DIR.glob("*.jpg")) + list(IMAGES_DIR.glob("*.json")):
        match = re.match(r"(\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2})", f.name)
        if not match:
            continue
        ts = datetime.strptime(match.group(1), "%Y-%m-%d_%H-%M-%S").replace(tzinfo=LOCAL_TZ)
        if ts >= cutoff:
            continue
        jpg = f.with_suffix(".jpg")
        detection = read_sidecar(jpg)
        if detection and (detection.get("heron_detected") or detection.get("other_animal_detected")):
            kept += 1
            continue
        f.unlink()
        deleted += 1
    logger.info("Cleanup: %d alte Dateien gelöscht, %d Tier-Dateien behalten", deleted, kept)


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
