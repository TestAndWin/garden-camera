#!/usr/bin/env bash
# Full deploy script – run from the project root directory.
# Usage:  bash kubernetes/deploy.sh
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
K8S="$PROJECT_ROOT/kubernetes"

echo "=== Garden Camera Deploy ==="

# ── 1. Build Docker image ───────────────────────────────────────────────────
echo ""
echo "→ Building Docker image…"
docker build -t garden-camera-server:latest -f "$PROJECT_ROOT/server/Dockerfile" "$PROJECT_ROOT"

# If running on minikube, force-load the exact local image via tar.
# This avoids stale :latest tags in the node runtime.
if command -v minikube >/dev/null 2>&1 && [ "$(kubectl config current-context 2>/dev/null || true)" = "minikube" ]; then
  echo "→ Loading image into minikube runtime…"
  TMP_DIR="$(mktemp -d)"
  trap 'rm -rf "$TMP_DIR"' EXIT

  docker save garden-camera-server:latest -o "$TMP_DIR/garden-camera-server-latest.tar"
  minikube image load "$TMP_DIR/garden-camera-server-latest.tar" --overwrite=true
fi

# ── 2. Apply K8s manifests ──────────────────────────────────────────────────
echo ""
echo "→ Applying PVC, Deployment, Service, Ingress…"
kubectl apply -f "$K8S/pvc.yaml"
kubectl apply -f "$K8S/deployment.yaml"
kubectl apply -f "$K8S/service.yaml"
kubectl apply -f "$K8S/ingress.yaml"

# ── 3. Restart deployment to pick up new image ─────────────────────────────
echo "→ Restarting deployment…"
kubectl rollout restart deployment/garden-camera-server -n default
kubectl rollout status  deployment/garden-camera-server -n default

# ── 4. Show status ──────────────────────────────────────────────────────────
echo ""
echo "=== Status ==="
kubectl get all -n default -l app=garden-camera-server

echo ""
echo "=== Done! ==="
echo ""
echo "Access:"
echo "  Ingress:       http://mini-pc/garden-camera"
echo "  port-forward:  kubectl port-forward svc/garden-camera-server 8080:80 -n default"
echo "                 then open http://localhost:8080"
echo ""
echo "Logs:"
echo "  kubectl logs -f deployment/garden-camera-server -n default"
