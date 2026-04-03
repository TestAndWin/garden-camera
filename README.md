# Garden Camera

A local system that captures photos from an ESP32 camera every minute, uploads them to a server, and provides a web UI to browse the images. Planned: automatic bird detection.

## Components

- **ESP32 (Wrover Kit)** — Takes a photo every 60 seconds and sends it via HTTP POST to the server
- **Server (FastAPI)** — Receives and stores images, serves a web gallery
- **Kubernetes (minikube)** — Local deployment with persistent storage

## Setup

### Server

```bash
bash kubernetes/deploy.sh
```

This builds the Docker image, loads it into minikube, applies all Kubernetes manifests, and restarts the deployment.

Access the web UI at `http://mini-pc/garden-camera`.

### ESP32

1. Copy `camera/wifi-config.h.example` to `camera/wifi-config.h` and fill in your WiFi credentials
2. Set `UPLOAD_URL` in `camera.ino` to your server address (e.g. `http://<server-ip>:30080/upload`)
3. Flash to ESP32 Wrover Kit via Arduino IDE (upload speed 460800)

## API

| Method | Path | Description |
|---|---|---|
| `POST` | `/upload` | Receive JPEG image (raw body) |
| `GET` | `/images` | List all stored images (JSON) |
| `GET` | `/images/{filename}` | Retrieve a single image |
| `GET` | `/` | Web UI |
