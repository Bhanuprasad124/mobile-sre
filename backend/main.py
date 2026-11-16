import os
from pathlib import Path
from fastapi import FastAPI, HTTPException, Query
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from kubernetes import client, config
from kubernetes.client.exceptions import ApiException

app = FastAPI(title="OpsHealth Mobile SRE", version="0.2.0")

try:
    config.load_incluster_config()
except config.ConfigException:
    # Local development only; uses the current kubectl context.
    config.load_kube_config()

core = client.CoreV1Api()
authz = client.AuthorizationV1Api()

def require_namespace_access(namespace: str, verb: str = "list", resource: str = "pods"):
    if not namespace or len(namespace) > 63:
        raise HTTPException(400, "Invalid namespace")
    review = client.V1SelfSubjectAccessReview(
        spec=client.V1SelfSubjectAccessReviewSpec(
            resource_attributes=client.V1ResourceAttributes(
                namespace=namespace, verb=verb, resource=resource, group=""
            )
        )
    )
    try:
        result = authz.create_self_subject_access_review(review)
    except ApiException:
        raise HTTPException(403, "Unable to verify namespace permissions")
    if not result.status or not result.status.allowed:
        raise HTTPException(403, "Namespace access denied")

def pod_or_404(namespace: str, pod_name: str):
    require_namespace_access(namespace, "get", "pods")
    try:
        return core.read_namespaced_pod(pod_name, namespace)
    except ApiException as exc:
        if exc.status == 404:
            raise HTTPException(404, "Pod not found")
        raise HTTPException(exc.status or 500, "Unable to retrieve pod")

@app.get("/api/health")
def health():
    return {"status": "ok"}

@app.get("/api/namespaces")
def namespaces():
    try:
        all_ns = core.list_namespace().items
    except ApiException:
        raise HTTPException(500, "Unable to list namespaces")
    visible = []
    for item in all_ns:
        name = item.metadata.name
        try:
            require_namespace_access(name, "list", "pods")
            visible.append({"name": name, "status": item.status.phase})
        except HTTPException:
            continue
    return sorted(visible, key=lambda x: x["name"])

@app.get("/api/pods")
def pods(namespace: str = Query(..., min_length=1, max_length=63)):
    require_namespace_access(namespace, "list", "pods")
    try:
        result = core.list_namespaced_pod(namespace)
    except ApiException:
        raise HTTPException(500, "Unable to retrieve pods")
    output = []
    for pod in result.items:
        statuses = pod.status.container_statuses or []
        waiting = []
        for cs in statuses:
            state = cs.state
            if state and state.waiting and state.waiting.reason:
                waiting.append(state.waiting.reason)
        output.append({
            "name": pod.metadata.name,
            "phase": pod.status.phase or "Unknown",
            "reason": (waiting[0] if waiting else None),
            "restarts": sum(cs.restart_count or 0 for cs in statuses),
            "node": pod.spec.node_name,
            "containers": [c.name for c in (pod.spec.containers or [])],
            "created": pod.metadata.creation_timestamp.isoformat() if pod.metadata.creation_timestamp else None
        })
    return output

@app.get("/api/pods/{pod_name}/logs")
def logs(
    pod_name: str, namespace: str = Query(...),
    container: str | None = None, previous: bool = False,
    tail_lines: int = Query(200, ge=1, le=1000)
):
    pod = pod_or_404(namespace, pod_name)
    valid = [c.name for c in (pod.spec.containers or [])]
    if container and container not in valid:
        raise HTTPException(400, "Container does not belong to this pod")
    if not valid:
        raise HTTPException(409, "Pod has no containers")
    if not container and len(valid) > 1:
        container = valid[0]
    try:
        content = core.read_namespaced_pod_log(
            pod_name, namespace, container=container,
            previous=previous, tail_lines=tail_lines, timestamps=True
        )
        return {"container": container, "previous": previous, "logs": content}
    except ApiException as exc:
        if exc.status == 400 or exc.status == 404:
            raise HTTPException(409, "Container logs are not available yet. Check pod events and scheduling diagnostics.")
        raise HTTPException(500, "Unable to retrieve logs")

@app.get("/api/pods/{pod_name}/configmaps")
def configmaps(pod_name: str, namespace: str = Query(...)):
    pod = pod_or_404(namespace, pod_name)
    require_namespace_access(namespace, "list", "configmaps")
    names = set()
    for c in pod.spec.containers or []:
        for env in c.env or []:
            ref = getattr(getattr(env, "value_from", None), "config_map_key_ref", None)
            if ref: names.add(ref.name)
        for env_from in c.env_from or []:
            ref = getattr(env_from, "config_map_ref", None)
            if ref: names.add(ref.name)
    for vol in pod.spec.volumes or []:
        ref = getattr(vol, "config_map", None)
        if ref: names.add(ref.name)
    result = []
    for name in sorted(names):
        try:
            cm = core.read_namespaced_config_map(name, namespace)
            result.append({"name": name, "data": cm.data or {}, "binaryDataKeys": list((cm.binary_data or {}).keys())})
        except ApiException:
            result.append({"name": name, "data": None, "unavailable": True})
    return result

@app.get("/api/pods/{pod_name}/secrets")
def secret_references(pod_name: str, namespace: str = Query(...)):
    pod = pod_or_404(namespace, pod_name)
    # Intentionally do not GET or LIST Secret objects. Only report references
    # embedded in the pod specification; secret values are never retrieved.
    refs = set()
    for c in pod.spec.containers or []:
        for env in c.env or []:
            ref = getattr(getattr(env, "value_from", None), "secret_key_ref", None)
            if ref: refs.add((ref.name, ref.key or ""))
        for env_from in c.env_from or []:
            ref = getattr(env_from, "secret_ref", None)
            if ref: refs.add((ref.name, "*"))
    for vol in pod.spec.volumes or []:
        ref = getattr(vol, "secret", None)
        if ref: refs.add((ref.secret_name, "volume"))
    return [{"name": n, "reference": k, "valuesExposed": False} for n, k in sorted(refs)]

@app.get("/api/pods/{pod_name}/events")
def events(pod_name: str, namespace: str = Query(...)):
    pod_or_404(namespace, pod_name)
    require_namespace_access(namespace, "list", "events")
    try:
        result = core.list_namespaced_event(
            namespace, field_selector=f"involvedObject.name={pod_name}"
        )
        return [{"type": e.type, "reason": e.reason, "message": e.message,
                 "count": e.count, "lastTimestamp": (e.last_timestamp or e.event_time).isoformat() if (e.last_timestamp or e.event_time) else None}
                for e in sorted(result.items, key=lambda x: x.metadata.creation_timestamp or 0, reverse=True)]
    except ApiException:
        raise HTTPException(500, "Unable to retrieve events")

@app.get("/api/pods/{pod_name}/details")
def details(pod_name: str, namespace: str = Query(...)):
    pod = pod_or_404(namespace, pod_name)
    return {
        "name": pod.metadata.name, "namespace": namespace,
        "phase": pod.status.phase, "node": pod.spec.node_name,
        "podIP": pod.status.pod_ip,
        "conditions": [{"type": c.type, "status": c.status, "reason": c.reason, "message": c.message} for c in (pod.status.conditions or [])],
        "containers": [{"name": c.name, "image": c.image,
                        "requests": c.resources.requests if c.resources else None,
                        "limits": c.resources.limits if c.resources else None}
                       for c in (pod.spec.containers or [])]
    }

static_dir = Path(__file__).parent / "static"
if static_dir.exists():
    app.mount("/", StaticFiles(directory=static_dir, html=True), name="static")
