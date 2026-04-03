import asyncio
import logging
import os
import re
from datetime import datetime, timezone, timedelta
from pathlib import Path

from fastapi import FastAPI, Request, Response
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

CEST = timezone(timedelta(hours=2))
logger = logging.getLogger("garden-camera")

IMAGES_DIR = Path(os.getenv("IMAGES_DIR", "/data/images"))
IMAGES_DIR.mkdir(parents=True, exist_ok=True)

app = FastAPI()
app.mount("/static", StaticFiles(directory="static"), name="static")


@app.post("/upload")
async def upload_image(request: Request):
    body = await request.body()
    if not body:
        return Response(status_code=400, content="Empty body")

    timestamp = datetime.now(CEST).strftime("%Y-%m-%d_%H-%M-%S")
    filename = f"{timestamp}.jpg"
    filepath = IMAGES_DIR / filename

    # Avoid overwriting if multiple uploads in same second
    counter = 1
    while filepath.exists():
        filename = f"{timestamp}_{counter}.jpg"
        filepath = IMAGES_DIR / filename
        counter += 1

    filepath.write_bytes(body)
    return {"filename": filename, "size": len(body)}


@app.get("/images")
async def list_images(limit: int = 0, hour: str = ""):
    files = sorted(IMAGES_DIR.glob("*.jpg"), reverse=True)
    if hour:
        # hour format: "2026-04-03_14"
        files = [f for f in files if f.name.startswith(hour)]
    if limit > 0:
        files = files[:limit]
    return [{"filename": f.name, "size": f.stat().st_size} for f in files]


@app.get("/hours")
async def list_hours():
    files = sorted(IMAGES_DIR.glob("*.jpg"), reverse=True)
    hours: dict[str, int] = {}
    for f in files:
        # filename: 2026-04-03_14-30-00.jpg -> hour key: 2026-04-03_14
        key = f.name[:13]
        hours[key] = hours.get(key, 0) + 1
    return [{"hour": k, "count": v} for k, v in hours.items()]


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


@app.get("/stunde.html")
async def stunde():
    return FileResponse("static/stunde.html")


def cleanup_old_images():
    cutoff = datetime.now(CEST) - timedelta(days=2)
    deleted = 0
    for f in IMAGES_DIR.glob("*.jpg"):
        match = re.match(r"(\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2})", f.name)
        if not match:
            continue
        ts = datetime.strptime(match.group(1), "%Y-%m-%d_%H-%M-%S").replace(tzinfo=CEST)
        if ts < cutoff:
            f.unlink()
            deleted += 1
    logger.info("Cleanup: %d alte Bilder gelöscht", deleted)


async def cleanup_task():
    while True:
        await asyncio.sleep(24 * 60 * 60)
        cleanup_old_images()


@app.on_event("startup")
async def startup():
    cleanup_old_images()
    asyncio.create_task(cleanup_task())
