# Garden Camera

ESP32 camera takes a photo every minute (6:00–22:00, deep sleep at night) and uploads it to a local server. The server stores images, detects grey herons via CLIP, and serves a web UI to browse images and detections. Battery voltage is monitored. Local system, not for production use.

## Architecture

```
camera/               ESP32 Arduino sketch (C++)
  camera.ino            Photo every 60s, HTTP POST to server, NTP time sync,
                        deep sleep 22:00–6:00, battery voltage via ADC
  wifi-config.h         WiFi credentials + upload URL (DO NOT commit!)
server/               Python FastAPI (Docker)
  main.py               API + web server + CLIP heron detection + auto-cleanup
  static/index.html     Main gallery (newest 60 images, heron badges, battery)
  static/stunde.html    Hourly detail view
  static/fischreiher.html  Heron detection results page
  Dockerfile
  requirements.txt
kubernetes/           All K8s manifests
  deploy.sh             Full build + deploy script
```

## Tech Stack

| Component | Technology |
|---|---|
| Camera | ESP32 Wrover Kit, Arduino IDE |
| Server | Python 3.12, FastAPI, Uvicorn |
| Deployment | Docker, Kubernetes (minikube) |
| Image storage | Filesystem (no S3/DB for images) |
| Bird detection | CLIP (openai/clip-vit-base-patch32), sidecar JSON per image |

## ESP32

- **Board**: ESP32 Wrover Kit
- **Upload Speed**: 460800
- **Partition Scheme**: Default 4MB with Spiffs
- **Image format**: JPEG, UXGA (1600x1200), quality 10 (high)
- **Interval**: 60 seconds
- **Active hours**: 6:00–22:00 (CET/CEST via NTP), deep sleep outside this window
- **Upload**: HTTP POST with `Content-Type: image/jpeg` + `X-Battery-Voltage` header to `uploadUrl`
- **Battery**: ADC on GPIO 32, voltage divider 2x 100k (ratio 2.0)
- `wifi-config.h` contains `ssid`, `password` and `uploadUrl` as `const char*` — **never commit**, add to `.gitignore`

## Commands

```bash
# Use minikube Docker daemon
eval $(minikube docker-env)

# Build server image (build context = project root)
docker build -t garden-camera-server:latest -f server/Dockerfile .

# Apply all K8s manifests
kubectl apply -f kubernetes/

# Redeploy after code changes
kubectl rollout restart deployment/garden-camera-server -n default

# View logs
kubectl logs -l app=garden-camera-server -n default --tail=100

# Get service URL (minikube)
minikube service garden-camera-server --url
```

### After Server Code Changes

```bash
# 1. Build
eval $(minikube docker-env) && docker build -t garden-camera-server:latest -f server/Dockerfile .

# 2. Redeploy
kubectl rollout restart deployment/garden-camera-server -n default

# 3. Check logs
kubectl logs -l app=garden-camera-server -n default --tail=50 --follow
```

## API Endpoints

| Method | Path | Description |
|---|---|---|
| `POST` | `/upload` | Receive image (JPEG, raw body). Saves with timestamp filename. Reads `X-Battery-Voltage` header |
| `GET` | `/images` | List images (JSON). Params: `limit` (int), `hour` (e.g. `2026-04-03_14`). Includes `heron_detected`/`heron_score` from sidecar |
| `GET` | `/images/{filename}` | Retrieve single image |
| `GET` | `/hours` | List all hours with image count |
| `GET` | `/detections` | List images where heron was detected |
| `GET` | `/status` | Battery voltage + last upload timestamp |
| `GET` | `/` | Main gallery |
| `GET` | `/stunde.html` | Hourly detail view |
| `GET` | `/fischreiher.html` | Heron detections page |

## Image Storage

- Images stored on filesystem at `/data/images/` inside the container
- Filename: timestamp-based, e.g. `2026-04-03_14-30-00.jpg`
- PersistentVolumeClaim ensures images survive pod restarts

## Kubernetes Resources

All resources in namespace `default`.

| Resource | Name | Description |
|---|---|---|
| Deployment | `garden-camera-server` | Web server + upload API, port 8080 |
| Service | `garden-camera-server` | Exposes deployment (NodePort for minikube) |
| PVC | `garden-camera-data` | Image storage at `/data/images` |

- Server listens on port 8080
- ESP32 `UPLOAD_URL` must point to minikube/host IP (currently `http://192.168.178.50/upload`)
- Use `minikube service garden-camera-server --url` to get the external URL

## Planned Features

- **Bird detection**: Image classification to check if a specific bird is visible in the photo
- Details (model, analysis timing) still open

## UI Language

German.

## Gotchas

### ESP32
- `wifi-config.h` must not be committed — add to `.gitignore`!
- Camera init errors (`0x...`) often mean wrong pin assignments for the board
- Always call `esp_camera_fb_return(fb)`, otherwise memory leaks

### Server
- Images arrive as raw JPEG body, not multipart form data
- PVC must be mounted at `/data/images`, otherwise images are lost on pod restart

### Kubernetes
- After manifest changes: always `kubectl apply -f kubernetes/` — file != cluster state
- Use `eval $(minikube docker-env)` before building images, otherwise minikube can't find them

## Development Rules

- **Always** read affected files before making changes
- ESP32: flash to board after changes and check Serial Monitor
- Server: build image → redeploy → check logs (see commands above)
- Never commit secrets (WiFi passwords, API keys) to the repository
