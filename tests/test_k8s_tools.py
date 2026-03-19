"""Tests for K8s tools using mocked kubernetes client."""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from app.tools.k8s import k8s_get_events, k8s_get_logs, k8s_get_node, k8s_get_pod, k8s_list_deployments


def _make_pod(
    name="worker-abc123",
    namespace="production",
    phase="Running",
    restart_count=7,
    node_name="node-1",
    terminated_reason=None,
    waiting_reason=None,
):
    """Build a fake V1Pod-like object."""
    # Container state
    if terminated_reason:
        state = SimpleNamespace(
            waiting=None,
            running=None,
            terminated=SimpleNamespace(reason=terminated_reason, exit_code=137, message="OOMKilled"),
        )
    elif waiting_reason:
        state = SimpleNamespace(
            waiting=SimpleNamespace(reason=waiting_reason, message="Back-off restarting"),
            running=None,
            terminated=None,
        )
    else:
        state = SimpleNamespace(
            waiting=None,
            running=SimpleNamespace(started_at="2025-03-19T10:00:00Z"),
            terminated=None,
        )

    last_state = SimpleNamespace(
        terminated=SimpleNamespace(reason="OOMKilled", exit_code=137, message="killed", finished_at="2025-03-19T09:58:00Z")
    )

    container_status = SimpleNamespace(
        name="worker",
        ready=True,
        restart_count=restart_count,
        image="myapp:v1.2.3",
        state=state,
        last_state=last_state,
    )

    resources = SimpleNamespace(
        requests={"cpu": "100m", "memory": "256Mi"},
        limits={"cpu": "500m", "memory": "512Mi"},
    )
    container_spec = SimpleNamespace(name="worker", resources=resources, image="myapp:v1.2.3")

    pod = SimpleNamespace(
        metadata=SimpleNamespace(name=name, namespace=namespace),
        status=SimpleNamespace(
            phase=phase,
            container_statuses=[container_status],
            conditions=[
                SimpleNamespace(type="Ready", status="True", reason=None, message=None),
            ],
        ),
        spec=SimpleNamespace(
            node_name=node_name,
            containers=[container_spec],
        ),
    )
    return pod


@patch("app.tools.k8s._v1")
def test_k8s_get_pod(mock_v1_fn):
    mock_client = MagicMock()
    mock_v1_fn.return_value = mock_client
    mock_client.read_namespaced_pod.return_value = _make_pod(restart_count=7, waiting_reason="CrashLoopBackOff")
    result = k8s_get_pod("production", "worker-abc123")

    assert result["name"] == "worker-abc123"
    assert result["phase"] == "Running"
    assert result["node_name"] == "node-1"
    assert result["containers"][0]["restart_count"] == 7
    assert result["containers"][0]["state"] == "waiting"
    assert result["resources"][0]["limits"]["memory"] == "512Mi"


@patch("app.tools.k8s._v1")
def test_k8s_get_pod_oomkilled(mock_v1_fn):
    mock_client = MagicMock()
    mock_v1_fn.return_value = mock_client
    mock_client.read_namespaced_pod.return_value = _make_pod(terminated_reason="OOMKilled")
    result = k8s_get_pod("production", "worker-abc123")

    assert result["containers"][0]["state"] == "terminated"
    assert result["containers"][0]["reason"] == "OOMKilled"


@patch("app.tools.k8s._v1")
def test_k8s_get_events(mock_v1_fn):
    from datetime import datetime, timezone

    mock_client = MagicMock()
    mock_v1_fn.return_value = mock_client

    event = SimpleNamespace(
        type="Warning",
        reason="BackOff",
        message="Back-off restarting failed container",
        count=5,
        first_timestamp=datetime(2025, 3, 19, 10, 0, 0, tzinfo=timezone.utc),
        last_timestamp=datetime.now(timezone.utc),
        source=SimpleNamespace(component="kubelet"),
    )
    mock_client.list_namespaced_event.return_value = SimpleNamespace(items=[event])

    result = k8s_get_events("production", "worker-abc123")
    assert len(result) == 1
    assert result[0]["reason"] == "BackOff"
    assert result[0]["count"] == 5


@patch("app.tools.k8s._v1")
def test_k8s_get_logs(mock_v1_fn):
    mock_client = MagicMock()
    mock_v1_fn.return_value = mock_client
    mock_client.read_namespaced_pod_log.return_value = "KeyError: 'SECRET_KEY'\nTraceback..."
    result = k8s_get_logs("production", "worker-abc123", previous=True)

    assert "SECRET_KEY" in result
    mock_client.read_namespaced_pod_log.assert_called_once_with(
        name="worker-abc123",
        namespace="production",
        tail_lines=100,
        previous=True,
    )


@patch("app.tools.k8s._apps_v1")
def test_k8s_list_deployments(mock_apps_fn):
    mock_client = MagicMock()
    mock_apps_fn.return_value = mock_client

    dep = SimpleNamespace(
        metadata=SimpleNamespace(name="worker"),
        spec=SimpleNamespace(
            replicas=3,
            template=SimpleNamespace(
                spec=SimpleNamespace(
                    containers=[SimpleNamespace(name="worker", image="myapp:v1.2.3")]
                )
            ),
        ),
        status=SimpleNamespace(
            ready_replicas=2,
            updated_replicas=3,
            conditions=[
                SimpleNamespace(type="Available", status="True", reason="MinimumReplicasAvailable", last_update_time="2025-03-19T10:00:00Z"),
            ],
        ),
    )
    mock_client.list_namespaced_deployment.return_value = SimpleNamespace(items=[dep])

    result = k8s_list_deployments("production")
    assert len(result) == 1
    assert result[0]["name"] == "worker"
    assert result[0]["replicas"] == 3
    assert result[0]["ready_replicas"] == 2


@patch("app.tools.k8s._v1")
def test_k8s_get_node(mock_v1_fn):
    mock_client = MagicMock()
    mock_v1_fn.return_value = mock_client

    node = SimpleNamespace(
        metadata=SimpleNamespace(name="node-1"),
        status=SimpleNamespace(
            allocatable={"cpu": "4", "memory": "16Gi"},
            capacity={"cpu": "4", "memory": "16Gi"},
            conditions=[
                SimpleNamespace(type="Ready", status="True", reason="KubeletReady", message="kubelet is ready"),
                SimpleNamespace(type="MemoryPressure", status="False", reason="KubeletHasSufficientMemory", message="no pressure"),
            ],
        ),
        spec=SimpleNamespace(taints=[]),
    )
    mock_client.read_node.return_value = node

    result = k8s_get_node("node-1")
    assert result["name"] == "node-1"
    assert result["allocatable"]["cpu"] == "4"
    assert len(result["conditions"]) == 2
