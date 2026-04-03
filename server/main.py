import os
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, Request, Response
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

IMAGES_DIR = Path(os.getenv("IMAGES_DIR", "/data/images"))
IMAGES_DIR.mkdir(parents=True, exist_ok=True)

app = FastAPI()
app.mount("/static", StaticFiles(directory="static"), name="static")


@app.post("/upload")
async def upload_image(request: Request):
    body = await request.body()
    if not body:
        return Response(status_code=400, content="Empty body")

    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
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
async def list_images():
    files = sorted(IMAGES_DIR.glob("*.jpg"), reverse=True)
    return [{"filename": f.name, "size": f.stat().st_size} for f in files]


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
