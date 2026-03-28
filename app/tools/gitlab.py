"""GitLab read-only tools for the RCA agent.

Provides access to pipelines, merge requests, deployments, and code
to correlate incidents with recent changes.
"""

from __future__ import annotations

import logging
from typing import Any

import requests

from app.config import settings

logger = logging.getLogger(__name__)


def _headers() -> dict[str, str]:
    return {"PRIVATE-TOKEN": settings.gitlab_token}


def _api(path: str, params: dict | None = None) -> dict | list | str:
    """Call the GitLab API. Returns parsed JSON or error string."""
    url = f"{settings.gitlab_url.rstrip('/')}/api/v4{path}"
    try:
        resp = requests.get(url, headers=_headers(), params=params or {}, timeout=15)
        resp.raise_for_status()
        return resp.json()
    except requests.RequestException as e:
        logger.error("GitLab API error: %s", e)
        return {"error": str(e)}


def _project_path() -> str:
    """URL-encoded project path."""
    from urllib.parse import quote
    return quote(settings.gitlab_project, safe="")


def gitlab_list_pipelines(status: str = "", ref: str = "", last_n: int = 10) -> list[dict[str, Any]]:
    """List recent pipelines for the configured project."""
    params: dict[str, Any] = {"per_page": last_n, "order_by": "id", "sort": "desc"}
    if status:
        params["status"] = status
    if ref:
        params["ref"] = ref
    result = _api(f"/projects/{_project_path()}/pipelines", params)
    if isinstance(result, dict) and "error" in result:
        return [result]
    return [
        {
            "id": p.get("id"),
            "status": p.get("status"),
            "ref": p.get("ref"),
            "sha": p.get("sha", "")[:8],
            "created_at": p.get("created_at"),
            "source": p.get("source"),
            "web_url": p.get("web_url"),
        }
        for p in result
    ]


def gitlab_get_pipeline(pipeline_id: int) -> dict[str, Any]:
    """Get pipeline details including jobs."""
    pipeline = _api(f"/projects/{_project_path()}/pipelines/{pipeline_id}")
    if isinstance(pipeline, dict) and "error" in pipeline:
        return pipeline

    jobs = _api(f"/projects/{_project_path()}/pipelines/{pipeline_id}/jobs", {"per_page": 50})
    job_list = []
    if isinstance(jobs, list):
        for j in jobs:
            job_list.append({
                "id": j.get("id"),
                "name": j.get("name"),
                "stage": j.get("stage"),
                "status": j.get("status"),
                "failure_reason": j.get("failure_reason"),
                "duration": j.get("duration"),
                "web_url": j.get("web_url"),
            })

    return {
        "id": pipeline.get("id"),
        "status": pipeline.get("status"),
        "ref": pipeline.get("ref"),
        "sha": pipeline.get("sha"),
        "created_at": pipeline.get("created_at"),
        "duration": pipeline.get("duration"),
        "jobs": job_list,
    }


def gitlab_get_job_log(job_id: int, tail: int = 200) -> str:
    """Get the raw log output of a CI job (last N lines)."""
    url = f"{settings.gitlab_url.rstrip('/')}/api/v4/projects/{_project_path()}/jobs/{job_id}/trace"
    try:
        resp = requests.get(url, headers=_headers(), timeout=15)
        resp.raise_for_status()
        lines = resp.text.splitlines()
        return "\n".join(lines[-tail:])
    except requests.RequestException as e:
        return f"Failed to get job log: {e}"


def gitlab_list_merge_requests(state: str = "merged", last_n: int = 5) -> list[dict[str, Any]]:
    """List recent merge requests."""
    result = _api(
        f"/projects/{_project_path()}/merge_requests",
        {"state": state, "per_page": last_n, "order_by": "updated_at", "sort": "desc"},
    )
    if isinstance(result, dict) and "error" in result:
        return [result]
    return [
        {
            "iid": mr.get("iid"),
            "title": mr.get("title"),
            "author": mr.get("author", {}).get("username"),
            "state": mr.get("state"),
            "merged_at": mr.get("merged_at"),
            "source_branch": mr.get("source_branch"),
            "target_branch": mr.get("target_branch"),
            "web_url": mr.get("web_url"),
        }
        for mr in result
    ]


def gitlab_get_mr_changes(mr_iid: int) -> dict[str, Any]:
    """Get the diff/changes from a merge request. Focuses on config files."""
    result = _api(f"/projects/{_project_path()}/merge_requests/{mr_iid}/changes")
    if isinstance(result, dict) and "error" in result:
        return result

    # Filter to relevant config files only
    config_patterns = (
        "Dockerfile", "docker-compose", ".yaml", ".yml", ".json",
        ".env", ".toml", "values", "kustomization", "Chart",
    )
    changes = []
    for change in result.get("changes", []):
        path = change.get("new_path", "")
        if any(p in path for p in config_patterns):
            changes.append({
                "file": path,
                "renamed": change.get("renamed_file", False),
                "deleted": change.get("deleted_file", False),
                "diff": change.get("diff", "")[:2000],  # truncate large diffs
            })

    return {
        "iid": result.get("iid"),
        "title": result.get("title"),
        "author": result.get("author", {}).get("username"),
        "merged_at": result.get("merged_at"),
        "config_changes": changes,
        "total_changes": len(result.get("changes", [])),
        "config_changes_count": len(changes),
    }


def gitlab_list_deployments(environment: str = "", last_n: int = 5) -> list[dict[str, Any]]:
    """List recent deployments."""
    params: dict[str, Any] = {"per_page": last_n, "order_by": "created_at", "sort": "desc"}
    if environment:
        params["environment"] = environment
    result = _api(f"/projects/{_project_path()}/deployments", params)
    if isinstance(result, dict) and "error" in result:
        return [result]
    return [
        {
            "id": d.get("id"),
            "status": d.get("status"),
            "environment": d.get("environment", {}).get("name"),
            "ref": d.get("ref"),
            "sha": d.get("sha", "")[:8],
            "created_at": d.get("created_at"),
            "user": d.get("user", {}).get("username"),
        }
        for d in result
    ]


def gitlab_get_file(file_path: str, ref: str = "main") -> str:
    """Get raw file content from the repository."""
    from urllib.parse import quote
    encoded_path = quote(file_path, safe="")
    url = f"{settings.gitlab_url.rstrip('/')}/api/v4/projects/{_project_path()}/repository/files/{encoded_path}/raw"
    try:
        resp = requests.get(url, headers=_headers(), params={"ref": ref}, timeout=15)
        resp.raise_for_status()
        return resp.text[:5000]  # truncate large files
    except requests.RequestException as e:
        return f"Failed to get file: {e}"


def gitlab_search_code(query: str) -> list[dict[str, Any]]:
    """Search for a string in the project codebase."""
    result = _api(
        f"/projects/{_project_path()}/search",
        {"scope": "blobs", "search": query, "per_page": 10},
    )
    if isinstance(result, dict) and "error" in result:
        return [result]
    return [
        {
            "file": r.get("filename"),
            "path": r.get("path"),
            "line": r.get("startline"),
            "data": r.get("data", "")[:500],
        }
        for r in result
    ]
