"""LangChain tool wrappers around K8s and Mimir functions."""

from __future__ import annotations

import json
from typing import Any

from langchain_core.tools import tool

from app.tools.k8s import (
    k8s_get_events,
    k8s_get_logs,
    k8s_get_node,
    k8s_get_pod,
    k8s_list_deployments,
)
from app.tools.mimir import mimir_instant_query, mimir_query


def _serialize(obj: Any) -> str:
    return json.dumps(obj, indent=2, default=str)


@tool
def get_pod(namespace: str, pod_name: str) -> str:
    """Get pod status including phase, restart count, conditions, resource limits/requests, node name, and container statuses.

    Args:
        namespace: Kubernetes namespace.
        pod_name: Name of the pod.
    """
    return _serialize(k8s_get_pod(namespace, pod_name))


@tool
def get_events(namespace: str, involved_object_name: str) -> str:
    """Get Kubernetes warning events for a specific object (pod, node, etc.) from the last 60 minutes.

    Args:
        namespace: Kubernetes namespace.
        involved_object_name: Name of the involved object (e.g. pod name).
    """
    return _serialize(k8s_get_events(namespace, involved_object_name))


@tool
def get_logs(
    namespace: str,
    pod_name: str,
    container: str = "",
    previous: bool = False,
    tail: int = 100,
) -> str:
    """Get container logs from a pod. Use previous=True to get logs from the last terminated container (useful for crash loops).

    Args:
        namespace: Kubernetes namespace.
        pod_name: Name of the pod.
        container: Container name. Leave empty for single-container pods.
        previous: If True, get logs from the previous (crashed) container instance.
        tail: Number of log lines to return (default 100).
    """
    return k8s_get_logs(namespace, pod_name, container or None, previous, tail)


@tool
def list_deployments(namespace: str) -> str:
    """List all deployments in a namespace with replica counts, images, and last update timestamps. Useful for checking recent rollouts.

    Args:
        namespace: Kubernetes namespace.
    """
    return _serialize(k8s_list_deployments(namespace))


@tool
def get_node(node_name: str) -> str:
    """Get node status including allocatable resources, conditions (MemoryPressure, DiskPressure), and taints.

    Args:
        node_name: Name of the Kubernetes node.
    """
    return _serialize(k8s_get_node(node_name))


@tool
def query_metrics(promql: str, start: str, end: str, step: str = "60s") -> str:
    """Execute a PromQL range query against Mimir/Prometheus. Returns time series data.

    Args:
        promql: PromQL expression (e.g. 'container_memory_usage_bytes{pod="worker-abc"}').
        start: Range start as RFC3339 timestamp (e.g. '2025-03-19T09:30:00Z').
        end: Range end as RFC3339 timestamp.
        step: Query resolution step (e.g. '60s', '5m').
    """
    return _serialize(mimir_query(promql, start, end, step))


@tool
def query_metrics_instant(promql: str) -> str:
    """Execute an instant PromQL query against Mimir/Prometheus. Returns current values.

    Args:
        promql: PromQL expression.
    """
    return _serialize(mimir_instant_query(promql))


K8S_TOOLS = [
    get_pod,
    get_events,
    get_logs,
    list_deployments,
    get_node,
]

MIMIR_TOOLS = [
    query_metrics,
    query_metrics_instant,
]


# --- GitLab tools ---

@tool
def gitlab_pipelines(status: str = "", last_n: int = 5) -> str:
    """List recent CI/CD pipelines from GitLab. Shows pipeline status, branch, and commit SHA. Use to check if a recent deploy failed.

    Args:
        status: Filter by status (e.g. 'failed', 'success'). Empty for all.
        last_n: Number of pipelines to return (default 5).
    """
    from app.tools.gitlab import gitlab_list_pipelines
    return _serialize(gitlab_list_pipelines(status=status, last_n=last_n))


@tool
def gitlab_pipeline_detail(pipeline_id: int) -> str:
    """Get details of a specific GitLab CI pipeline including all jobs, their status, and failure reasons.

    Args:
        pipeline_id: The pipeline ID number.
    """
    from app.tools.gitlab import gitlab_get_pipeline
    return _serialize(gitlab_get_pipeline(pipeline_id))


@tool
def gitlab_job_log(job_id: int, tail: int = 200) -> str:
    """Get the raw log output of a GitLab CI job. Useful for understanding why a build or deploy failed.

    Args:
        job_id: The job ID number.
        tail: Number of log lines to return (default 200).
    """
    from app.tools.gitlab import gitlab_get_job_log
    return gitlab_get_job_log(job_id, tail)


@tool
def gitlab_merge_requests(state: str = "merged", last_n: int = 5) -> str:
    """List recent merge requests from GitLab. Use to find what code changes were deployed recently.

    Args:
        state: MR state filter: 'merged', 'opened', 'closed' (default 'merged').
        last_n: Number of MRs to return (default 5).
    """
    from app.tools.gitlab import gitlab_list_merge_requests
    return _serialize(gitlab_list_merge_requests(state=state, last_n=last_n))


@tool
def gitlab_mr_changes(mr_iid: int) -> str:
    """Get the file changes (diffs) from a GitLab merge request. Only shows config files (Dockerfile, YAML, .env, etc.) not application code.

    Args:
        mr_iid: The merge request IID (the number shown in the URL, e.g. !42).
    """
    from app.tools.gitlab import gitlab_get_mr_changes
    return _serialize(gitlab_get_mr_changes(mr_iid))


@tool
def gitlab_deployments(environment: str = "", last_n: int = 5) -> str:
    """List recent GitLab deployments. Shows which ref/SHA was deployed to which environment and when.

    Args:
        environment: Filter by environment name (e.g. 'production'). Empty for all.
        last_n: Number of deployments to return (default 5).
    """
    from app.tools.gitlab import gitlab_list_deployments
    return _serialize(gitlab_list_deployments(environment=environment, last_n=last_n))


@tool
def gitlab_file(file_path: str, ref: str = "main") -> str:
    """Read a file from the GitLab repository. Use to check Dockerfiles, k8s manifests, CI configs, or Helm values.

    Args:
        file_path: Path to the file (e.g. 'k8s/deployment.yaml', 'Dockerfile').
        ref: Branch or commit SHA (default 'main').
    """
    from app.tools.gitlab import gitlab_get_file
    return gitlab_get_file(file_path, ref)


@tool
def gitlab_code_search(query: str) -> str:
    """Search for a string in the GitLab project codebase. Use to find where an env var, config key, or resource is defined.

    Args:
        query: Search string (e.g. 'DATABASE_URL', 'memory limit').
    """
    from app.tools.gitlab import gitlab_search_code
    return _serialize(gitlab_search_code(query))


GITLAB_TOOLS = [
    gitlab_pipelines,
    gitlab_pipeline_detail,
    gitlab_job_log,
    gitlab_merge_requests,
    gitlab_mr_changes,
    gitlab_deployments,
    gitlab_file,
    gitlab_code_search,
]


def get_tools() -> list:
    """Return the active tool set based on configuration."""
    from app.config import settings

    tools = list(K8S_TOOLS)
    if settings.mimir_endpoint:
        tools.extend(MIMIR_TOOLS)
    if settings.gitlab_url and settings.gitlab_token and settings.gitlab_project:
        tools.extend(GITLAB_TOOLS)
    return tools
