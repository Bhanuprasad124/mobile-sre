from datetime import datetime, timezone

from fastapi import APIRouter, Query
from kubernetes import client
from kubernetes.client.exceptions import ApiException

from backend.main import apps, authz, core, has_namespace_access, ready_condition

router = APIRouter()


def _alert(severity, category, code, title, message, namespace=None, resource=None, observed=None):
    identity = f"{category}:{namespace or 'cluster'}:{resource or code}:{code}"
    return {
        "id": identity,
        "severity": severity,
        "category": category,
        "code": code,
        "title": title,
        "message": message,
        "namespace": namespace,
        "resource": resource,
        "observedAt": observed.isoformat() if observed else None,
        "status": "active",
    }


@router.get("/api/alerts")
def get_alerts(
    restart_threshold: int = Query(3, ge=1, le=100),
    cpu_threshold: int = Query(80, ge=1, le=100),
    memory_threshold: int = Query(85, ge=1, le=100),
):
    """Evaluate current Kubernetes state and return active, read-only alerts."""
    alerts = []
    access_errors = []
    try:
        namespace_items = core.list_namespace().items
    except ApiException:
        return {
            "generatedAt": datetime.now(timezone.utc).isoformat(),
            "available": False,
            "message": "Unable to discover namespaces for alert evaluation.",
            "items": [],
            "counts": {"critical": 0, "warning": 0, "info": 0, "total": 0},
        }

    namespaces = [
        item.metadata.name for item in namespace_items
        if has_namespace_access(item.metadata.name, "list", "pods")
    ]

    for namespace in namespaces:
        try:
            for pod in core.list_namespaced_pod(namespace).items:
                pod_name = pod.metadata.name
                statuses = pod.status.container_statuses or []
                for status in statuses:
                    waiting = status.state.waiting if status.state else None
                    last_terminated = (
                        status.last_state.terminated
                        if status.last_state and status.last_state.terminated else None
                    )
                    reason = waiting.reason if waiting else None
                    if reason in ("CrashLoopBackOff", "ImagePullBackOff", "ErrImagePull", "CreateContainerConfigError"):
                        alerts.append(_alert(
                            "critical", "pod", reason.upper(), reason,
                            f"Container {status.name} in pod {pod_name} is waiting: {reason}. Review pod Events and logs.",
                            namespace, pod_name,
                        ))
                    if last_terminated and last_terminated.reason == "OOMKilled":
                        alerts.append(_alert(
                            "critical", "pod", "OOMKILLED", "Container was OOMKilled",
                            f"Container {status.name} in pod {pod_name} was terminated after exceeding its memory allowance.",
                            namespace, pod_name,
                        ))
                    if (status.restart_count or 0) >= restart_threshold:
                        alerts.append(_alert(
                            "warning", "pod", "RESTART_THRESHOLD",
                            "Container restart threshold exceeded",
                            f"Container {status.name} has restarted {status.restart_count} times; configured threshold is {restart_threshold}.",
                            namespace, pod_name,
                        ))
                if pod.status.phase == "Failed":
                    alerts.append(_alert(
                        "critical", "pod", "POD_FAILED", "Pod is Failed",
                        "Kubernetes reports this pod in Failed phase.", namespace, pod_name,
                        pod.metadata.creation_timestamp,
                    ))
        except ApiException as exc:
            access_errors.append(f"{namespace}: pod inventory unavailable (HTTP {exc.status}).")

        if has_namespace_access(namespace, "list", "deployments", "apps"):
            try:
                for workload in apps.list_namespaced_deployment(namespace).items:
                    desired = workload.spec.replicas or 0
                    available = workload.status.available_replicas or 0
                    if available < desired:
                        alerts.append(_alert(
                            "warning", "workload", "DEPLOYMENT_UNAVAILABLE",
                            "Deployment has unavailable replicas",
                            f"{available} of {desired} replicas are available.",
                            namespace, workload.metadata.name,
                        ))
            except ApiException as exc:
                access_errors.append(f"{namespace}: deployment inventory unavailable (HTTP {exc.status}).")

        if has_namespace_access(namespace, "list", "statefulsets", "apps"):
            try:
                for workload in apps.list_namespaced_stateful_set(namespace).items:
                    desired = workload.spec.replicas or 0
                    ready = workload.status.ready_replicas or 0
                    if ready < desired:
                        alerts.append(_alert(
                            "warning", "workload", "STATEFULSET_UNREADY",
                            "StatefulSet has unready replicas",
                            f"{ready} of {desired} replicas are ready.",
                            namespace, workload.metadata.name,
                        ))
            except ApiException as exc:
                access_errors.append(f"{namespace}: StatefulSet inventory unavailable (HTTP {exc.status}).")

        if has_namespace_access(namespace, "list", "daemonsets", "apps"):
            try:
                for workload in apps.list_namespaced_daemon_set(namespace).items:
                    desired = workload.status.desired_number_scheduled or 0
                    ready = workload.status.number_ready or 0
                    if ready < desired:
                        alerts.append(_alert(
                            "warning", "workload", "DAEMONSET_UNREADY",
                            "DaemonSet has unready pods",
                            f"{ready} of {desired} scheduled pods are ready.",
                            namespace, workload.metadata.name,
                        ))
            except ApiException as exc:
                access_errors.append(f"{namespace}: DaemonSet inventory unavailable (HTTP {exc.status}).")

    try:
        for node in core.list_node().items:
            if not ready_condition(node):
                alerts.append(_alert(
                    "critical", "node", "NODE_NOT_READY", "Node is NotReady",
                    "Kubernetes does not currently report this node as Ready.",
                    None, node.metadata.name,
                ))
    except ApiException as exc:
        access_errors.append(f"Node inventory unavailable (HTTP {exc.status}).")

    try:
        metrics = client.CustomObjectsApi().list_cluster_custom_object(
            group="metrics.k8s.io", version="v1beta1", plural="nodes"
        )
        node_metrics = {item["metadata"]["name"]: item for item in metrics.get("items", [])}
        for node in core.list_node().items:
            metric = node_metrics.get(node.metadata.name)
            if not metric:
                continue
            usage = metric.get("usage", {})
            allocatable = node.status.allocatable or node.status.capacity or {}
            cpu_capacity = client.ApiClient().deserialize(
                client.ApiClient().sanitize_for_serialization(allocatable.get("cpu", "0")), "str"
            ) if False else allocatable.get("cpu", "0")
            from kubernetes.utils.quantity import parse_quantity
            cpu_total = float(parse_quantity(cpu_capacity))
            memory_total = float(parse_quantity(allocatable.get("memory", "0")))
            cpu_used = float(parse_quantity(usage.get("cpu", "0")))
            memory_used = float(parse_quantity(usage.get("memory", "0")))
            cpu_pct = cpu_used / cpu_total * 100 if cpu_total else 0
            memory_pct = memory_used / memory_total * 100 if memory_total else 0
            if cpu_pct >= cpu_threshold:
                alerts.append(_alert(
                    "warning", "node", "NODE_CPU_HIGH", "Node CPU usage is high",
                    f"Current CPU usage is {cpu_pct:.1f}% of allocatable capacity (threshold {cpu_threshold}%).",
                    None, node.metadata.name,
                ))
            if memory_pct >= memory_threshold:
                alerts.append(_alert(
                    "warning", "node", "NODE_MEMORY_HIGH", "Node memory usage is high",
                    f"Current memory usage is {memory_pct:.1f}% of allocatable capacity (threshold {memory_threshold}%).",
                    None, node.metadata.name,
                ))
    except ApiException as exc:
        if exc.status not in (403, 404, 503):
            access_errors.append(f"Node metrics unavailable (HTTP {exc.status}).")
    except (KeyError, TypeError, ValueError, ZeroDivisionError):
        access_errors.append("Node metrics returned an unexpected payload.")

    alerts.sort(key=lambda item: ({"critical": 0, "warning": 1, "info": 2}[item["severity"]], item["category"], item["title"]))
    counts = {
        "critical": sum(item["severity"] == "critical" for item in alerts),
        "warning": sum(item["severity"] == "warning" for item in alerts),
        "info": sum(item["severity"] == "info" for item in alerts),
        "total": len(alerts),
    }
    return {
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "available": True,
        "message": "Some resource categories could not be checked." if access_errors else None,
        "limitations": access_errors,
        "thresholds": {
            "restarts": restart_threshold,
            "cpuPercent": cpu_threshold,
            "memoryPercent": memory_threshold,
        },
        "counts": counts,
        "items": alerts,
    }
