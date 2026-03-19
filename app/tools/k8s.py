"""Kubernetes read-only tools for the RCA agent."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from kubernetes import client, config
from kubernetes.client.rest import ApiException

logger = logging.getLogger(__name__)


_configured = False


def _ensure_config():
    """Lazy-load K8s config on first actual use."""
    global _configured
    if _configured:
        return
    try:
        config.load_incluster_config()
    except config.ConfigException:
        config.load_kube_config()
    _configured = True


def _v1() -> client.CoreV1Api:
    _ensure_config()
    return client.CoreV1Api()


def _apps_v1() -> client.AppsV1Api:
    _ensure_config()
    return client.AppsV1Api()


def k8s_get_pod(namespace: str, pod_name: str) -> dict[str, Any]:
    """Get pod status: phase, restartCount, conditions, resources, node, containerStatuses."""
    try:
        pod = _v1().read_namespaced_pod(name=pod_name, namespace=namespace)
    except ApiException as e:
        return {"error": f"Failed to get pod: {e.status} {e.reason}"}

    containers = []
    for cs in pod.status.container_statuses or []:
        state_info = {}
        if cs.state:
            if cs.state.waiting:
                state_info = {"state": "waiting", "reason": cs.state.waiting.reason, "message": cs.state.waiting.message}
            elif cs.state.running:
                state_info = {"state": "running", "started_at": str(cs.state.running.started_at)}
            elif cs.state.terminated:
                state_info = {
                    "state": "terminated",
                    "reason": cs.state.terminated.reason,
                    "exit_code": cs.state.terminated.exit_code,
                    "message": cs.state.terminated.message,
                }

        last_state_info = {}
        if cs.last_state and cs.last_state.terminated:
            t = cs.last_state.terminated
            last_state_info = {
                "reason": t.reason,
                "exit_code": t.exit_code,
                "message": t.message,
                "finished_at": str(t.finished_at),
            }

        containers.append({
            "name": cs.name,
            "ready": cs.ready,
            "restart_count": cs.restart_count,
            "image": cs.image,
            **state_info,
            "last_termination": last_state_info or None,
        })

    # Extract resource requests/limits from spec
    resources = []
    for c in pod.spec.containers or []:
        res = c.resources
        resources.append({
            "container": c.name,
            "requests": dict(res.requests) if res and res.requests else None,
            "limits": dict(res.limits) if res and res.limits else None,
        })

    conditions = []
    for cond in pod.status.conditions or []:
        conditions.append({
            "type": cond.type,
            "status": cond.status,
            "reason": cond.reason,
            "message": cond.message,
        })

    return {
        "name": pod.metadata.name,
        "namespace": pod.metadata.namespace,
        "phase": pod.status.phase,
        "node_name": pod.spec.node_name,
        "conditions": conditions,
        "containers": containers,
        "resources": resources,
    }


def k8s_get_events(namespace: str, involved_object_name: str) -> list[dict[str, Any]]:
    """Get warning events for a specific object in the last 60 minutes."""
    try:
        events = _v1().list_namespaced_event(
            namespace=namespace,
            field_selector=f"involvedObject.name={involved_object_name}",
        )
    except ApiException as e:
        return [{"error": f"Failed to list events: {e.status} {e.reason}"}]

    cutoff = datetime.now(timezone.utc).timestamp() - 3600
    result = []
    for ev in events.items:
        last_ts = ev.last_timestamp
        if last_ts and last_ts.timestamp() < cutoff:
            continue
        result.append({
            "type": ev.type,
            "reason": ev.reason,
            "message": ev.message,
            "count": ev.count,
            "first_timestamp": str(ev.first_timestamp) if ev.first_timestamp else None,
            "last_timestamp": str(ev.last_timestamp) if ev.last_timestamp else None,
            "source": ev.source.component if ev.source else None,
        })

    return result


def k8s_get_logs(
    namespace: str,
    pod_name: str,
    container: str | None = None,
    previous: bool = False,
    tail: int = 100,
) -> str:
    """Get container logs. Set previous=True to get logs from the last terminated container."""
    try:
        kwargs: dict[str, Any] = {
            "name": pod_name,
            "namespace": namespace,
            "tail_lines": tail,
            "previous": previous,
        }
        if container:
            kwargs["container"] = container
        return _v1().read_namespaced_pod_log(**kwargs)
    except ApiException as e:
        return f"Failed to get logs: {e.status} {e.reason}"


def k8s_list_deployments(namespace: str) -> list[dict[str, Any]]:
    """List deployments with replica counts, images, and last update timestamps."""
    try:
        deps = _apps_v1().list_namespaced_deployment(namespace=namespace)
    except ApiException as e:
        return [{"error": f"Failed to list deployments: {e.status} {e.reason}"}]

    result = []
    for dep in deps.items:
        images = []
        for c in dep.spec.template.spec.containers or []:
            images.append({"container": c.name, "image": c.image})

        conditions = []
        for cond in dep.status.conditions or []:
            conditions.append({
                "type": cond.type,
                "status": cond.status,
                "reason": cond.reason,
                "last_update": str(cond.last_update_time) if cond.last_update_time else None,
            })

        result.append({
            "name": dep.metadata.name,
            "replicas": dep.spec.replicas,
            "ready_replicas": dep.status.ready_replicas or 0,
            "updated_replicas": dep.status.updated_replicas or 0,
            "images": images,
            "conditions": conditions,
        })

    return result


def k8s_get_node(node_name: str) -> dict[str, Any]:
    """Get node status: allocatable resources, conditions, taints."""
    try:
        node = _v1().read_node(name=node_name)
    except ApiException as e:
        return {"error": f"Failed to get node: {e.status} {e.reason}"}

    conditions = []
    for cond in node.status.conditions or []:
        conditions.append({
            "type": cond.type,
            "status": cond.status,
            "reason": cond.reason,
            "message": cond.message,
        })

    taints = []
    for taint in node.spec.taints or []:
        taints.append({
            "key": taint.key,
            "value": taint.value,
            "effect": taint.effect,
        })

    return {
        "name": node.metadata.name,
        "allocatable": dict(node.status.allocatable) if node.status.allocatable else {},
        "capacity": dict(node.status.capacity) if node.status.capacity else {},
        "conditions": conditions,
        "taints": taints,
    }
