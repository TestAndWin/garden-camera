# Garden Camera

A local system that captures photos from an ESP32 camera every minute, uploads them to a server, detects grey herons with CLIP, and provides a web UI to browse images, hourly groups, detections, and battery status.

## Components

- **ESP32 (Wrover Kit)** — Takes a photo every 60 seconds and sends it via HTTP POST to the server
- **Server (FastAPI)** — Receives and stores images, serves a web gallery, and analyzes images for grey herons
- **Kubernetes (minikube)** — Local deployment with persistent storage

## Setup

### Server

```bash
bash kubernetes/deploy.sh
```

This builds the Docker image from the project root, loads it into minikube when applicable, applies the Kubernetes manifests, and restarts the deployment.

Access the web UI at `http://mini-pc/garden-camera`.

For local browser-only access without ingress:

```bash
kubectl port-forward svc/garden-camera-server 8080:80 -n default
```

Then open `http://localhost:8080`.

### ESP32

Create `camera/wifi-config.h` with your WiFi credentials and upload URL:

```cpp
const char* ssid = "YOUR_WIFI";
const char* password = "YOUR_PASSWORD";
const char* uploadUrl = "http://mini-pc/garden-camera/upload";
```

Notes:

- `camera/wifi-config.h` is ignored by git and must not be committed
- The default ingress expects the host `mini-pc`; if you want to use a different hostname, update `kubernetes/ingress.yaml`
- Flash to ESP32 Wrover Kit via Arduino IDE with upload speed `460800`

## API

| Method | Path | Description |
|---|---|---|
| `POST` | `/upload` | Receive JPEG image (raw body) and optional `X-Battery-Voltage` header |
| `GET` | `/images` | List stored images (JSON), supports `limit` and `hour` |
| `GET` | `/images/{filename}` | Retrieve a single image |
| `GET` | `/hours` | List available hours with image counts |
| `GET` | `/detections` | List images where a grey heron was detected |
| `GET` | `/status` | Last reported battery voltage and upload timestamp |
| `GET` | `/` | Web UI |
| `GET` | `/hour.html` | Hourly detail page |
| `GET` | `/heron.html` | Detection results page |
