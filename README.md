# OpsHealth Mobile SRE Dashboard (Phase 1 cluster overview)

A read-only Kubernetes dashboard with dynamic namespace discovery, pod cards, and quick actions for Logs, ConfigMaps, and Secret references.

## Security model

- Namespace names are discovered dynamically.
- Cluster overview pod and workload totals include only namespaces where the dashboard service account is authorized to list pods.
- The dropdown includes only namespaces where the dashboard service account can list pods.
- A namespace-scoped RoleBinding must be installed for each namespace approved for dashboard access.
- The dashboard can read pods, pod logs, events, ConfigMaps, Deployments, StatefulSets, and DaemonSets in approved namespaces.
- The dashboard can read node metadata (node name, Ready condition, role labels, and kubelet version) cluster-wide. It does not return node IP addresses or node capacity details.
- It does **not** get/list Kubernetes Secret objects. Secret names/references are extracted from the pod specification; secret values are never fetched.
- No pod exec, delete, restart, scale, or deployment mutation APIs exist.
- This starter has no end-user login. Keep it local/internal; do not expose it through a public ALB until authentication, authorization, TLS, and audit logging are implemented and reviewed.

## Prerequisites

Docker, kubectl, a working Kubernetes context (Minikube or EKS), and a Docker Hub account.

## 1. Build, push and deploy in one command

From the project root:

```bash
chmod +x scripts/*.sh
docker login
DOCKERHUB_USER=bhanuprasad124 ./scripts/build-push-deploy.sh v0.2.0
```

For Minikube, if your context is named `minikube`:

```bash
DOCKERHUB_USER=bhanuprasad124 KUBE_CONTEXT=minikube ./scripts/build-push-deploy.sh v0.2.0
```

The script builds `bhanuprasad124/mobile-sre:v0.2.0`, pushes it to Docker Hub, applies Kubernetes resources, updates the Deployment image, and waits for rollout.

For a private Docker Hub repository, configure an imagePullSecret in the `mobile-sre` namespace before deployment. For a public repository, the default pull works.

## 2. Grant dashboard access to approved namespaces

The base deployment deliberately has no pod/config access until you approve a namespace.

```bash
./scripts/grant-namespace-access.sh dev
```

Repeat for each namespace you explicitly approve:

```bash
./scripts/grant-namespace-access.sh test
```

Namespaces without this RoleBinding will not appear in the dashboard dropdown. The namespace discovery ClusterRole only lists namespace names and creates SelfSubjectAccessReviews; it does not grant access to their resources.

## 3. Open the dashboard

```bash
kubectl get pods,svc -n mobile-sre
kubectl port-forward -n mobile-sre svc/opshealth-mobile-sre 8080:80
```

Open `http://localhost:8080` on the machine running port-forward.

## 4. Local development

The API can run locally using the current kubeconfig:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r backend/requirements.txt
uvicorn backend.main:app --reload --port 8080
```

Build the UI in another terminal:

```bash
cd frontend
npm install
npm run build
```

For a local combined run, copy `frontend/dist/*` into `backend/static/` before starting FastAPI.

## 5. Phase 1 cluster overview

The `/api/overview` endpoint returns:

- Pod totals and health counts across approved namespaces only.
- Deployment, StatefulSet, and DaemonSet replica health where the service account has the corresponding namespace-scoped permissions.
- Node readiness and limited node metadata using explicit read-only `nodes get/list` RBAC.

The dashboard refreshes this summary every 30 seconds. CPU and memory utilization are not included yet; those require a metrics source such as Metrics Server or Prometheus.

## 6. Useful troubleshooting commands

```bash
kubectl get pods -n mobile-sre
kubectl logs deployment/opshealth-mobile-sre -n mobile-sre
kubectl describe deployment opshealth-mobile-sre -n mobile-sre
kubectl auth can-i list pods -n dev --as=system:serviceaccount:mobile-sre:sre-dashboard
kubectl auth can-i get pods/log -n dev --as=system:serviceaccount:mobile-sre:sre-dashboard
kubectl auth can-i list secrets -n dev --as=system:serviceaccount:mobile-sre:sre-dashboard
```

Expected permissions after granting `dev`: pods list = yes, pod logs = yes, deployments list = yes, secrets list = no.

After upgrading to Phase 1, re-apply the namespace Role/RoleBinding in every namespace you have approved so the dashboard can read workload inventory:

```bash
./scripts/grant-namespace-access.sh dev
./scripts/grant-namespace-access.sh external
```

The updated `k8s/base.yaml` also grants only `get` and `list` on cluster-scoped `nodes` for node readiness. Apply it with `kubectl apply -f k8s/base.yaml` or use the build/deploy script.

## Notes

- Pending pods generally have no application logs until a container starts. Use Events and pod conditions to diagnose scheduling, image pull, and volume issues.
- ConfigMaps can contain sensitive values. Grant ConfigMap read access only in namespaces approved for this dashboard.
- Kubernetes Events are retained only temporarily.
- This is a testing starter, not a production-ready security boundary. Add user authentication, per-user authorization, CSRF/session protections where applicable, audit logging, rate limits, and private network access before broader deployment.
