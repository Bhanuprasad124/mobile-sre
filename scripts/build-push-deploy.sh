#!/usr/bin/env bash
set -euo pipefail

# Usage:
# DOCKERHUB_USER=bhanuprasad124 ./scripts/build-push-deploy.sh [tag] [namespace]
# Optional: KUBE_CONTEXT=minikube, PLATFORM=linux/amd64
IMAGE_REPO="${DOCKERHUB_USER:-bhanuprasad124}/mobile-sre"
TAG="${1:-$(date +%Y%m%d-%H%M%S)}"
APP_NS="mobile-sre"
KUBE_CONTEXT="${KUBE_CONTEXT:-}"
PLATFORM="${PLATFORM:-linux/amd64}"
IMAGE="${IMAGE_REPO}:${TAG}"

command -v docker >/dev/null || { echo "docker is required"; exit 1; }
command -v kubectl >/dev/null || { echo "kubectl is required"; exit 1; }
if [[ -n "$KUBE_CONTEXT" ]]; then kubectl config use-context "$KUBE_CONTEXT"; fi

echo "==> Building ${IMAGE}"
docker build --platform "$PLATFORM" -t "$IMAGE" .

echo "==> Logging in to Docker Hub (if not already logged in)"
docker login

echo "==> Pushing ${IMAGE}"
docker push "$IMAGE"

echo "==> Applying dashboard namespace, service account, RBAC discovery, Deployment and Service"
kubectl apply -f k8s/base.yaml

echo "==> Updating deployment image"
kubectl set image deployment/opshealth-mobile-sre dashboard="$IMAGE" -n "$APP_NS"
kubectl rollout status deployment/opshealth-mobile-sre -n "$APP_NS" --timeout=180s

echo
echo "Deployment complete."
echo "Image: ${IMAGE}"
echo "Namespace: ${APP_NS}"
echo "Check: kubectl get pods,svc -n ${APP_NS}"
echo "Access: kubectl port-forward -n ${APP_NS} svc/opshealth-mobile-sre 8080:80"
echo "Then open http://localhost:8080 on the same machine."
echo
echo "IMPORTANT: grant access only to approved namespaces, for example:"
echo "  ./scripts/grant-namespace-access.sh dev"
echo "Do NOT expose this prototype publicly until authentication, authorization review, TLS and audit controls are added."
