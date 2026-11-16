import os
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from kubernetes import client, config
from kubernetes.client.exceptions import ApiException

app = FastAPI(title="Mobile SRE", version="0.5.0")

try:
    config.load_incluster_config()
except config.ConfigException:
    # Local development only; uses the current kubectl context.
    config.load_kube_config()

core = client.CoreV1Api()
authz = client.AuthorizationV1Api()
apps = client.AppsV1Api()


def has_namespace_access(namespace: str, verb: str, resource: str, group: str = "") -> bool:
    """Check the dashboard service account's effective permission without broadening RBAC."""
    review = client.V1SelfSubjectAccessReview(
        spec=client.V1SelfSubjectAccessReviewSpec(
            resource_attributes=client.V1ResourceAttributes(
                namespace=namespace, verb=verb, resource=resource, group=group
            )
        )
    )
    try:
        result = authz.create_self_subject_access_review(review)
        return bool(result.status and result.status.allowed)
    except ApiException:
        return False


def require_namespace_access(
    namespace: str, verb: str = "list", resource: str = "pods", group: str = ""
):
    if not namespace or len(namespace) > 63:
        raise HTTPException(400, "Invalid namespace")
    if not has_namespace_access(namespace, verb, resource, group):
        raise HTTPException(403, "Namespace access denied")


def pod_or_404(namespace: str, pod_name: str):
    require_namespace_access(namespace, "get", "pods")
    try:
        return core.read_namespaced_pod(pod_name, namespace)
    except ApiException as exc:
        if exc.status == 404:
            raise HTTPException(404, "Pod not found")
        raise HTTPException(exc.status or 500, "Unable to retrieve pod")


def pod_waiting_reason(pod):
    for container_status in (pod.status.container_statuses or []):
        state = container_status.state
        if state and state.waiting and state.waiting.reason:
            return state.waiting.reason
        if state and state.terminated and state.terminated.exit_code not in (None, 0):
            return state.terminated.reason or "ContainerExited"
    return None


def ready_condition(node):
    for condition in (node.status.conditions or []):
        if condition.type == "Ready":
            return condition.status == "True"
    return False


@app.get("/api/health")
def health():
    return {"status": "ok", "version": app.version}


@app.get("/api/namespaces")
def namespaces():
    try:
        all_ns = core.list_namespace().items
    except ApiException:
        raise HTTPException(500, "Unable to list namespaces")
    visible = []
    for item in all_ns:
        name = item.metadata.name
        if has_namespace_access(name, "list", "pods"):
            visible.append({"name": name, "status": item.status.phase})
    return sorted(visible, key=lambda item: item["name"])


@app.get("/api/overview")
def cluster_overview():
    """
    Cluster summary with a deliberately limited data scope:
    - Pod/workload totals only include namespaces where this service account
      can list pods.
    - Node summary is cluster-wide and requires the explicit read-only
      get/list nodes permission in the namespace-discovery ClusterRole.
    - No Secret resources or values are queried.
    """
    try:
        all_namespaces = core.list_namespace().items
    except ApiException:
        raise HTTPException(500, "Unable to discover namespaces")

    accessible_namespaces = [
        item.metadata.name for item in all_namespaces
        if has_namespace_access(item.metadata.name, "list", "pods")
    ]

    pod_counts = {
        "total": 0, "running": 0, "pending": 0, "failed": 0,
        "succeeded": 0, "needsAttention": 0
    }
    workload_counts = {
        "deployments": {"total": 0, "healthy": 0, "unhealthy": 0},
        "statefulSets": {"total": 0, "healthy": 0, "unhealthy": 0},
        "daemonSets": {"total": 0, "healthy": 0, "unhealthy": 0},
    }

    for namespace in accessible_namespaces:
        try:
            pod_list = core.list_namespaced_pod(namespace)
            for pod in pod_list.items:
                phase = pod.status.phase or "Unknown"
                reason = pod_waiting_reason(pod)
                pod_counts["total"] += 1
                if phase == "Running":
                    pod_counts["running"] += 1
                elif phase == "Pending":
                    pod_counts["pending"] += 1
                elif phase == "Failed":
                    pod_counts["failed"] += 1
                elif phase == "Succeeded":
                    pod_counts["succeeded"] += 1
                if phase in ("Pending", "Failed") or reason:
                    pod_counts["needsAttention"] += 1
        except ApiException:
            # A namespace that becomes inaccessible during the request is skipped.
            continue

        if has_namespace_access(namespace, "list", "deployments", "apps"):
            try:
                for deployment in apps.list_namespaced_deployment(namespace).items:
                    desired = deployment.spec.replicas or 0
                    ready = deployment.status.available_replicas or 0
                    updated = deployment.status.updated_replicas or 0
                    healthy = ready >= desired and updated >= desired
                    key = workload_counts["deployments"]
                    key["total"] += 1
                    key["healthy" if healthy else "unhealthy"] += 1
            except ApiException:
                pass

        if has_namespace_access(namespace, "list", "statefulsets", "apps"):
            try:
                for stateful_set in apps.list_namespaced_stateful_set(namespace).items:
                    desired = stateful_set.spec.replicas or 0
                    ready = stateful_set.status.ready_replicas or 0
                    key = workload_counts["statefulSets"]
                    key["total"] += 1
                    key["healthy" if ready >= desired else "unhealthy"] += 1
            except ApiException:
                pass

        if has_namespace_access(namespace, "list", "daemonsets", "apps"):
            try:
                for daemon_set in apps.list_namespaced_daemon_set(namespace).items:
                    desired = daemon_set.status.desired_number_scheduled or 0
                    ready = daemon_set.status.number_ready or 0
                    key = workload_counts["daemonSets"]
                    key["total"] += 1
                    key["healthy" if ready >= desired else "unhealthy"] += 1
            except ApiException:
                pass

    nodes_payload = {
        "available": False, "total": 0, "ready": 0, "notReady": 0,
        "items": [], "message": None
    }
    try:
        node_list = core.list_node()
        node_items = []
        for node in node_list.items:
            is_ready = ready_condition(node)
            labels = node.metadata.labels or {}
            roles = sorted(
                label.split("/", 1)[1]
                for label in labels
                if label.startswith("node-role.kubernetes.io/")
            )
            node_items.append({
                "name": node.metadata.name,
                "ready": is_ready,
                "roles": roles,
                "kubeletVersion": (
                    node.status.node_info.kubelet_version
                    if node.status and node.status.node_info else None
                ),
            })
        nodes_payload.update({
            "available": True,
            "total": len(node_items),
            "ready": sum(1 for item in node_items if item["ready"]),
            "notReady": sum(1 for item in node_items if not item["ready"]),
            "items": sorted(node_items, key=lambda item: item["name"]),
        })
    except ApiException as exc:
        nodes_payload["message"] = (
            "Node health is unavailable. Apply the updated dashboard RBAC "
            "to grant read-only node get/list access."
            if exc.status == 403 else "Unable to retrieve node health."
        )

    return {
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "scope": "authorized-namespaces",
        "namespaces": {"accessible": len(accessible_namespaces)},
        "pods": pod_counts,
        "workloads": workload_counts,
        "nodes": nodes_payload,
    }


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
        output.append({
            "name": pod.metadata.name,
            "phase": pod.status.phase or "Unknown",
            "reason": pod_waiting_reason(pod),
            "restarts": sum(cs.restart_count or 0 for cs in statuses),
            "node": pod.spec.node_name,
            "containers": [c.name for c in (pod.spec.containers or [])],
            "created": pod.metadata.creation_timestamp.isoformat() if pod.metadata.creation_timestamp else None
        })
    return output


@app.get("/api/pods/{pod_name}/logs/stream")
def stream_logs(
    pod_name: str,
    namespace: str = Query(..., min_length=1, max_length=63),
    container: str | None = None,
    tail_lines: int = Query(100, ge=1, le=1000),
):
    """Stream timestamped container logs using existing namespace-scoped RBAC."""
    pod = pod_or_404(namespace, pod_name)
    valid = [item.name for item in (pod.spec.containers or [])]
    if not valid:
        raise HTTPException(409, "Pod has no containers")
    if container is None:
        container = valid[0]
    if container not in valid:
        raise HTTPException(400, "Container does not belong to this pod")
    try:
        response = core.read_namespaced_pod_log(
            pod_name, namespace, container=container, follow=True,
            tail_lines=tail_lines, timestamps=True,
            _preload_content=False, _request_timeout=3600,
        )
    except ApiException as exc:
        if exc.status in (400, 404):
            raise HTTPException(409, "Container logs are not available yet. Check pod Events and scheduling diagnostics.")
        raise HTTPException(500, "Unable to start the log stream")

    def chunks():
        try:
            for chunk in response.stream(amt=1024, decode_content=False):
                if chunk:
                    yield chunk
        finally:
            response.close()

    return StreamingResponse(
        chunks(),
        media_type="text/plain; charset=utf-8",
        headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"},
    )


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
        if exc.status in (400, 404):
            raise HTTPException(409, "Container logs are not available yet. Check pod events and scheduling diagnostics.")
        raise HTTPException(500, "Unable to retrieve logs")


@app.get("/api/pods/{pod_name}/configmaps")
def configmaps(pod_name: str, namespace: str = Query(...)):
    pod = pod_or_404(namespace, pod_name)
    require_namespace_access(namespace, "list", "configmaps")
    names = set()
    for container in pod.spec.containers or []:
        for env in container.env or []:
            ref = getattr(getattr(env, "value_from", None), "config_map_key_ref", None)
            if ref:
                names.add(ref.name)
        for env_from in container.env_from or []:
            ref = getattr(env_from, "config_map_ref", None)
            if ref:
                names.add(ref.name)
    for volume in pod.spec.volumes or []:
        ref = getattr(volume, "config_map", None)
        if ref:
            names.add(ref.name)
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
    for container in pod.spec.containers or []:
        for env in container.env or []:
            ref = getattr(getattr(env, "value_from", None), "secret_key_ref", None)
            if ref:
                refs.add((ref.name, ref.key or ""))
        for env_from in container.env_from or []:
            ref = getattr(env_from, "secret_ref", None)
            if ref:
                refs.add((ref.name, "*"))
    for volume in pod.spec.volumes or []:
        ref = getattr(volume, "secret", None)
        if ref:
            refs.add((ref.secret_name, "volume"))
    return [{"name": name, "reference": key, "valuesExposed": False} for name, key in sorted(refs)]


@app.get("/api/pods/{pod_name}/events")
def events(pod_name: str, namespace: str = Query(...)):
    pod_or_404(namespace, pod_name)
    require_namespace_access(namespace, "list", "events")
    try:
        result = core.list_namespaced_event(
            namespace, field_selector=f"involvedObject.name={pod_name}"
        )
        return [{
            "type": event.type, "reason": event.reason, "message": event.message,
            "count": event.count,
            "lastTimestamp": (event.last_timestamp or event.event_time).isoformat()
            if (event.last_timestamp or event.event_time) else None
        } for event in sorted(
            result.items,
            key=lambda item: item.metadata.creation_timestamp or datetime.min.replace(tzinfo=timezone.utc),
            reverse=True
        )]
    except ApiException:
        raise HTTPException(500, "Unable to retrieve events")


@app.get("/api/pods/{pod_name}/diagnostics")
def diagnostics(pod_name: str, namespace: str = Query(...)):
    """Read-only diagnostic signals and guidance; never mutates cluster resources."""
    pod = pod_or_404(namespace, pod_name)
    findings = []

    def finding(severity, code, title, message, recommendation):
        findings.append({
            "severity": severity, "code": code, "title": title,
            "message": message, "recommendation": recommendation,
        })

    phase = pod.status.phase or "Unknown"
    if phase == "Pending":
        finding("warning", "POD_PENDING", "Pod is Pending",
                "The scheduler or kubelet has not completed pod startup.",
                "Review Warning Events for FailedScheduling, volume attachment, image pull, or node capacity messages.")
    elif phase == "Failed":
        finding("critical", "POD_FAILED", "Pod has Failed",
                "Kubernetes reports this pod in Failed phase.",
                "Inspect container termination details and recent Warning Events.")

    container_details = []
    for status in (pod.status.container_statuses or []):
        state = status.state
        waiting = state.waiting if state else None
        terminated = state.terminated if state else None
        last_terminated = status.last_state.terminated if status.last_state and status.last_state.terminated else None
        restarts = status.restart_count or 0

        if waiting and waiting.reason:
            reason = waiting.reason
            guidance = {
                "CrashLoopBackOff": ("The container repeatedly exits after starting.",
                    "Inspect current and previous logs, exit code, startup probes, and recent configuration changes."),
                "ImagePullBackOff": ("Kubernetes cannot pull the container image.",
                    "Verify image name and tag, registry availability, imagePullSecrets, and node network access."),
                "ErrImagePull": ("The image pull request failed.",
                    "Read the related Warning Event and verify registry credentials, image path, and tag."),
                "CreateContainerConfigError": ("Kubernetes could not construct the container configuration.",
                    "Check referenced ConfigMaps, Secret references, environment variables, and pod Events. Secret values are never read."),
                "ContainerCreating": ("The container is still being prepared.",
                    "Check Events for volume mounts, image downloads, CNI setup, and sandbox creation."),
            }
            message, recommendation = guidance.get(
                reason, (f"The container is waiting: {reason}.",
                         "Inspect pod Events and the container specification for the underlying cause.")
            )
            severity = "critical" if reason in (
                "CrashLoopBackOff", "ImagePullBackOff", "ErrImagePull", "CreateContainerConfigError"
            ) else "warning"
            finding(severity, reason.upper(), reason, message, recommendation)

        if last_terminated and last_terminated.reason == "OOMKilled":
            finding("critical", "OOMKILLED", f"{status.name} was OOMKilled",
                    "The previous container instance exceeded its memory allowance.",
                    "Compare memory usage with the container limit and inspect recent workload changes.")
        elif terminated and terminated.exit_code not in (None, 0):
            finding("warning", "CONTAINER_EXITED", f"{status.name} exited with code {terminated.exit_code}",
                    f"Container termination reason: {terminated.reason or 'not reported'}.",
                    "Inspect current or previous logs and application startup/shutdown behavior.")

        if restarts:
            finding("warning", "CONTAINER_RESTARTS", f"{status.name} has restarted {restarts} time(s)",
                    "The container has restarted during this pod's lifetime.",
                    "Review previous logs and Events to distinguish application exits, probe failures, and resource pressure.")

        container_details.append({
            "name": status.name, "ready": bool(status.ready),
            "restartCount": restarts,
            "waitingReason": waiting.reason if waiting else None,
            "lastTerminatedReason": last_terminated.reason if last_terminated else None,
            "lastExitCode": last_terminated.exit_code if last_terminated else None,
        })

    for condition in (pod.status.conditions or []):
        if condition.status == "False":
            finding("warning", (condition.reason or condition.type or "POD_CONDITION").upper(),
                    f"{condition.type} condition is False",
                    condition.message or condition.reason or "Kubernetes reports an unhealthy pod condition.",
                    "Review pod Events and condition details to identify the failing scheduling, initialization, or readiness step.")

    if not findings:
        finding("info", "NO_FAILURE_SIGNAL", "No obvious failure signal detected",
                "The pod status and container states do not currently expose a common failure condition.",
                "If the application is still unhealthy, inspect logs, readiness behavior, dependencies, and recent Events.")

    return {
        "name": pod.metadata.name, "namespace": namespace, "phase": phase,
        "node": pod.spec.node_name, "containers": container_details,
        "findings": findings,
    }


@app.get("/api/pods/{pod_name}/details")
def details(pod_name: str, namespace: str = Query(...)):
    pod = pod_or_404(namespace, pod_name)
    return {
        "name": pod.metadata.name, "namespace": namespace,
        "phase": pod.status.phase, "node": pod.spec.node_name,
        "podIP": pod.status.pod_ip,
        "conditions": [{
            "type": condition.type, "status": condition.status,
            "reason": condition.reason, "message": condition.message
        } for condition in (pod.status.conditions or [])],
        "containers": [{
            "name": container.name, "image": container.image,
            "requests": container.resources.requests if container.resources else None,
            "limits": container.resources.limits if container.resources else None
        } for container in (pod.spec.containers or [])]
    }


def quantity_value(value):
    """Convert a Kubernetes quantity to its base unit as a float."""
    from kubernetes.utils.quantity import parse_quantity
    return float(parse_quantity(value or "0"))


def metric_usage(usage):
    cpu_cores = quantity_value((usage or {}).get("cpu", "0"))
    memory_bytes = quantity_value((usage or {}).get("memory", "0"))
    return {
        "cpuCores": round(cpu_cores, 4),
        "cpuMillicores": round(cpu_cores * 1000, 2),
        "memoryBytes": int(memory_bytes),
        "memoryMiB": round(memory_bytes / (1024 * 1024), 2),
    }


def metrics_unavailable(exc):
    if exc.status in (404, 503):
        return "Metrics API is unavailable. Install or check Kubernetes Metrics Server, then wait for its first collection cycle."
    if exc.status == 403:
        return "Metrics API access is denied. Apply the Mobile SRE metrics RBAC permissions."
    return "Unable to retrieve live Kubernetes metrics."


def list_pod_metrics(namespace):
    require_namespace_access(namespace, "list", "pods")
    require_namespace_access(namespace, "list", "pods", "metrics.k8s.io")
    return client.CustomObjectsApi().list_namespaced_custom_object(
        group="metrics.k8s.io", version="v1beta1",
        namespace=namespace, plural="pods"
    )


@app.get("/api/metrics/nodes")
def node_metrics():
    """Current node usage from metrics.k8s.io; no historical values are implied."""
    custom = client.CustomObjectsApi()
    try:
        metrics = custom.list_cluster_custom_object(
            group="metrics.k8s.io", version="v1beta1", plural="nodes"
        )
        node_objects = {item["metadata"]["name"]: item for item in metrics.get("items", [])}
        nodes = core.list_node().items
        output = []
        for node in nodes:
            item = node_objects.get(node.metadata.name)
            capacity = node.status.allocatable or node.status.capacity or {}
            cpu_capacity = quantity_value(capacity.get("cpu", "0"))
            memory_capacity = quantity_value(capacity.get("memory", "0"))
            usage = metric_usage(item.get("usage", {})) if item else None
            output.append({
                "name": node.metadata.name,
                "ready": ready_condition(node),
                "usage": usage,
                "allocatable": {
                    "cpuMillicores": round(cpu_capacity * 1000, 2),
                    "memoryBytes": int(memory_capacity),
                    "memoryMiB": round(memory_capacity / (1024 * 1024), 2),
                },
                "cpuPercent": round((usage["cpuCores"] / cpu_capacity) * 100, 2)
                    if usage and cpu_capacity else None,
                "memoryPercent": round((usage["memoryBytes"] / memory_capacity) * 100, 2)
                    if usage and memory_capacity else None,
            })
        return {
            "available": True,
            "timestamp": metrics.get("timestamp"),
            "window": metrics.get("window"),
            "items": output,
        }
    except ApiException as exc:
        return {"available": False, "message": metrics_unavailable(exc), "items": []}
    except (KeyError, TypeError, ValueError):
        return {"available": False, "message": "Metrics Server returned an unexpected metrics payload.", "items": []}


@app.get("/api/metrics/pods")
def pod_metrics(namespace: str = Query(..., min_length=1, max_length=63)):
    """Current per-pod and per-container CPU/memory usage in an approved namespace."""
    try:
        result = list_pod_metrics(namespace)
        items = []
        for pod in result.get("items", []):
            containers = []
            total_cpu = 0.0
            total_memory = 0
            for container in pod.get("containers", []):
                usage = metric_usage(container.get("usage", {}))
                total_cpu += usage["cpuCores"]
                total_memory += usage["memoryBytes"]
                containers.append({"name": container.get("name"), **usage})
            items.append({
                "name": pod.get("metadata", {}).get("name"),
                "timestamp": pod.get("timestamp"),
                "window": pod.get("window"),
                "cpuMillicores": round(total_cpu * 1000, 2),
                "memoryBytes": total_memory,
                "memoryMiB": round(total_memory / (1024 * 1024), 2),
                "containers": containers,
            })
        items.sort(key=lambda item: item["cpuMillicores"], reverse=True)
        return {"available": True, "namespace": namespace,
                "timestamp": result.get("items", [{}])[0].get("timestamp") if items else None,
                "items": items}
    except ApiException as exc:
        return {"available": False, "namespace": namespace,
                "message": metrics_unavailable(exc), "items": []}
    except (KeyError, TypeError, ValueError):
        return {"available": False, "namespace": namespace,
                "message": "Metrics Server returned an unexpected metrics payload.", "items": []}


@app.get("/api/metrics/namespaces")
def namespace_metrics():
    """Aggregate current pod usage for namespaces where pod metrics are authorized."""
    try:
        all_namespaces = core.list_namespace().items
    except ApiException:
        raise HTTPException(500, "Unable to discover namespaces")
    output = []
    unavailable = None
    authorized = 0
    succeeded = 0
    for item in all_namespaces:
        namespace = item.metadata.name
        if not has_namespace_access(namespace, "list", "pods"):
            continue
        if not has_namespace_access(namespace, "list", "pods", "metrics.k8s.io"):
            continue
        authorized += 1
        try:
            result = list_pod_metrics(namespace)
            succeeded += 1
            cpu = 0.0
            memory = 0
            pod_count = 0
            for pod in result.get("items", []):
                pod_count += 1
                for container in pod.get("containers", []):
                    usage = metric_usage(container.get("usage", {}))
                    cpu += usage["cpuCores"]
                    memory += usage["memoryBytes"]
            output.append({
                "namespace": namespace,
                "pods": pod_count,
                "cpuMillicores": round(cpu * 1000, 2),
                "memoryBytes": memory,
                "memoryMiB": round(memory / (1024 * 1024), 2),
            })
        except ApiException as exc:
            unavailable = metrics_unavailable(exc)
    output.sort(key=lambda item: item["cpuMillicores"], reverse=True)
    if authorized == 0:
        unavailable = "No approved namespace has pod-metrics permission yet. Reapply scripts/grant-namespace-access.sh for each approved namespace."
    elif succeeded == 0 and unavailable is None:
        unavailable = "No namespace metrics could be retrieved. Check Metrics Server and its API availability."
    return {"available": succeeded > 0, "items": output, "message": unavailable}




static_dir = Path(__file__).parent / "static"
if static_dir.exists():
    app.mount("/", StaticFiles(directory=static_dir, html=True), name="static")
