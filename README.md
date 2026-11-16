# Mobile SRE

A mobile-first, read-only Kubernetes operations dashboard for cluster overview, pod logs, Kubernetes Events, live CPU/memory metrics, alert evaluation, and troubleshooting guidance.

## Phase 4: Alert Center

- Separate Alert Center page at `/alerts.html`; the existing overview and pod cards remain focused.
- Current alerts for CrashLoopBackOff, image pull/configuration failures, OOMKilled containers, failed pods, and configurable restart thresholds.
- Node NotReady and high node CPU/memory alerts.
- Deployment, StatefulSet, and DaemonSet replica health alerts.
- Default thresholds: 3 container restarts, 80% node CPU, and 85% node memory. Thresholds can be adjusted in the Alert Center.
- Alert state is evaluated live and refreshes every 30 seconds. An alert disappears when its condition is no longer detected; durable acknowledgement history is not yet included.
- Slack/email delivery is a follow-on integration and is not enabled in this release.

## Phase 3 capabilities

- Live node CPU and memory usage, per-pod and per-container CPU and memory usage, and namespace-level aggregation.
- Metrics are sourced from Kubernetes Metrics API (Metrics Server), refreshed every 30 seconds, and are not historical data.

## Phase 2 capabilities

- Cluster overview and dynamic namespace discovery filtered by effective pod-list permission.
- Pod inventory, snapshot/previous/live logs, Kubernetes Events, read-only diagnostics, ConfigMap references and values, and Secret references (names only).

## Security model

- Alert evaluation uses only read-only Kubernetes list/get operations and existing RBAC.
- Pod and workload data is evaluated only in namespaces where the dashboard service account can list pods; workload categories are checked only when their namespace-scoped permission is granted.
- Node health and node metrics use existing read-only cluster-scoped node permissions.
- No Secret get/list permission, pod exec, restart, delete, scale, or deployment mutation API is added.
- This prototype has no end-user login. Keep it local/internal; do not expose publicly before authentication, per-user authorization, TLS, audit logging, and rate limiting are implemented and reviewed.

## Deploy on Minikube

```bash
git fetch origin
git switch feature/phase4-alerting-notifications
git pull --ff-only origin feature/phase4-alerting-notifications
minikube addons enable metrics-server
kubectl -n kube-system rollout status deployment/metrics-server --timeout=180s
kubectl top nodes
kubectl top pods -A
DOCKERHUB_USER=bhanuprasad124 KUBE_CONTEXT=minikube ./scripts/build-push-deploy.sh v0.6.0
```

The deployment script builds and pushes `bhanuprasad124/mobile-sre:v0.6.0`, applies the Kubernetes manifests, and waits for the Deployment rollout.

Grant access only to approved namespaces, for example:

```bash
./scripts/grant-namespace-access.sh dev
```

Access the dashboard:

```bash
kubectl port-forward -n mobile-sre svc/mobile-sre-platform 8080:80
```

Open `http://localhost:8080`; use the Alerts link or open `http://localhost:8080/alerts.html`.

## Validation notes

The GitHub integration can commit these changes, but it does not execute the Docker build or Minikube integration test. Run the deployment/build commands above before relying on alerts operationally.

## API

- `GET /api/alerts?restart_threshold=3&cpu_threshold=80&memory_threshold=85`
- Existing endpoints: `/api/health`, `/api/namespaces`, `/api/overview`, `/api/metrics/*`, and `/api/pods/*`.
