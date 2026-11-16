# Mobile SRE

A mobile-first, read-only Kubernetes operations dashboard for cluster overview, pod logs, Kubernetes Events, ConfigMap references, Secret references (names only), and troubleshooting guidance.

## Phase 3 capabilities

- Live node CPU and memory usage, with percentages against Kubernetes allocatable capacity.
- Per-pod and per-container CPU and memory usage for the selected namespace.
- Namespace-level CPU and memory aggregation across approved namespaces.
- Metrics are sourced from the Kubernetes Metrics API (Metrics Server), refreshed every 30 seconds, and are not presented as historical data.
- A clear unavailable state is shown when Metrics Server is missing, not ready, or blocked by RBAC.

## Phase 2 capabilities

- Cluster overview: node readiness and workload health.
- Dynamic namespace discovery, filtered by the dashboard service account's effective pod-list permission.
- Pod inventory with phase, waiting reason, restart count, node, and containers.
- Snapshot logs, previous-container logs, and live timestamped log streaming.
- Pod Events with reason, type, count, source, and timestamps.
- Read-only pod diagnostics for Pending pods, CrashLoopBackOff, image pull failures, configuration errors, OOMKilled containers, restarts, and unhealthy conditions.
- ConfigMap references and values, subject to namespace-scoped ConfigMap read access.
- Secret names/references extracted from pod specifications. Secret objects and values are never fetched.

## Security model

- Cluster-scoped access is limited to namespace discovery, SelfSubjectAccessReviews, read-only node metadata, and read-only node metrics.
- Pod, pod-log, Event, ConfigMap, workload, and pod-metrics access is namespace-scoped.
- The dashboard requires pod get/list, pods/log get, events get/list, ConfigMaps get/list, and apps workload get/list in each approved namespace.
- No Secret get/list permission is granted.
- No pod exec, delete, restart, scale, or deployment mutation API is exposed.
- Live log streams are opened only after namespace and pod access checks. Stopping the stream or closing the pod drawer cancels the browser request.
- Diagnostics provide suggestions only; they do not make cluster changes.
- This prototype has no end-user login. Keep it local/internal. Do not expose it publicly until authentication, per-user authorization, TLS, audit logging, and rate limiting are implemented and reviewed.

## Prerequisites

Docker, kubectl, a working Kubernetes context (Minikube or EKS), and a Docker Hub account.

## Build, push, and deploy

From the project root:

```bash
chmod +x scripts/*.sh
docker login
DOCKERHUB_USER=bhanuprasad124 KUBE_CONTEXT=minikube ./scripts/build-push-deploy.sh v0.5.0
```

The script builds and pushes `bhanuprasad124/mobile-sre:v0.5.0`, applies the Kubernetes manifests, updates the `mobile-sre-platform` Deployment, waits for a successful rollout, and then removes the previous Deployment and Service names if they exist.

The application runs in the `mobile-sre` namespace and is exposed through the `mobile-sre-platform` Service:

```bash
kubectl get pods,svc -n mobile-sre
kubectl port-forward -n mobile-sre svc/mobile-sre-platform 8080:80
```

Open `http://localhost:8080` on the machine running port-forward.

## Enable live metrics

Metrics Server must be installed in the Kubernetes cluster. For the existing Minikube environment:

```bash
minikube addons enable metrics-server
kubectl -n kube-system rollout status deployment/metrics-server --timeout=180s
kubectl top nodes
kubectl top pods -A
```

For EKS or another Kubernetes distribution, install the upstream Kubernetes Metrics Server using its maintained deployment instructions and verify that the `metrics.k8s.io/v1beta1` API is available. Do not disable kubelet TLS verification as a general workaround; resolve kubelet certificate and network configuration instead.

The dashboard reads only the current Metrics API samples. Metrics Server does not provide long-term history. Prometheus is a later optional integration for historical charts and PromQL analytics.

After deploying Phase 3, re-apply the namespace-scoped Role/RoleBinding in every namespace you approve so the dashboard can read pod metrics:

```bash
./scripts/grant-namespace-access.sh dev
```

## Grant access to approved namespaces

Apply the namespace-scoped Role and RoleBinding separately for every namespace you explicitly approve:

```bash
./scripts/grant-namespace-access.sh dev
./scripts/grant-namespace-access.sh test
```

Namespaces without this RoleBinding will not appear in the dashboard dropdown. The dashboard deliberately does not grant itself additional permissions.

## Useful checks

```bash
kubectl auth can-i list pods -n dev --as=system:serviceaccount:mobile-sre:sre-dashboard
kubectl auth can-i get pods/log -n dev --as=system:serviceaccount:mobile-sre:sre-dashboard
kubectl auth can-i list events -n dev --as=system:serviceaccount:mobile-sre:sre-dashboard
kubectl auth can-i list secrets -n dev --as=system:serviceaccount:mobile-sre:sre-dashboard
kubectl logs deployment/mobile-sre-platform -n mobile-sre
kubectl describe deployment mobile-sre-platform -n mobile-sre
```

Expected permissions in an approved namespace: pods list = yes, pods/log get = yes, events list = yes, secrets list = no.

## API endpoints

- `GET /api/health`
- `GET /api/namespaces`
- `GET /api/overview`
- `GET /api/metrics/nodes`
- `GET /api/metrics/pods?namespace=<namespace>`
- `GET /api/metrics/namespaces`
- `GET /api/pods?namespace=<namespace>`
- `GET /api/pods/<pod>/logs?namespace=<namespace>`
- `GET /api/pods/<pod>/logs/stream?namespace=<namespace>&container=<container>`
- `GET /api/pods/<pod>/events?namespace=<namespace>`
- `GET /api/pods/<pod>/diagnostics?namespace=<namespace>`
- `GET /api/pods/<pod>/details?namespace=<namespace>`
- `GET /api/pods/<pod>/configmaps?namespace=<namespace>`
- `GET /api/pods/<pod>/secrets?namespace=<namespace>`

## Notes

- Pending pods often have no container logs because the container has not started. Use Events and diagnostics to inspect scheduling, image pull, volume, and configuration failures.
- Kubernetes Events are retained only temporarily.
- ConfigMaps may contain sensitive values. Grant ConfigMap read access only in namespaces approved for this dashboard.
- This is a testing platform, not a production security boundary. Complete authentication, per-user authorization, CSRF/session protections where applicable, audit logging, rate limits, and private network access before broader deployment.
