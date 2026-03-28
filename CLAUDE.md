# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What is Klarsicht

AI-powered root cause analysis agent for Kubernetes. Receives Grafana alert webhooks, inspects K8s workloads (pods, logs, events, metrics), and produces structured RCA with fix steps and postmortems. Self-hosted via Helm chart.

## Commands

```bash
# Backend tests (26 tests, pytest with asyncio_mode=auto)
cd /path/to/klars8t && source .venv/bin/activate
python -m pytest tests/ -v -W ignore::UserWarning

# Single test file
python -m pytest tests/test_webhook.py -v

# Dashboard build (React + Vite + Tailwind)
cd dashboard && bun run build

# Hugo docs/blog build
cd site && hugo --minify

# Helm lint
helm lint helm/klarsicht/

# Run agent locally
uvicorn app.webhook:app --host 0.0.0.0 --port 8000
```

## Architecture

**Core flow:** Grafana webhook → `POST /alert` → async `run_investigation()` → ReAct agent with tools → parse JSON → store RCA → notify Slack/Teams/Discord

**Key entry points:**
- `app/webhook.py` — FastAPI app, all HTTP endpoints, `_run_and_store()` spawns async investigations
- `app/agent/rca_agent.py` — `_build_llm()` creates the LLM client (Anthropic/OpenAI/Ollama), `_build_agent()` creates the LangGraph ReAct agent, `run_investigation()` is the main async entry point
- `app/agent/tools.py` — `get_tools()` returns active tool set based on config (K8S_TOOLS always, MIMIR_TOOLS if metrics endpoint set, GITLAB_TOOLS if GitLab configured)
- `app/config.py` — All settings via pydantic-settings with `KLARSICHT_` env prefix

**Adding a new tool:**
1. Write the function in `app/tools/yourservice.py` (returns dict/str)
2. Add a `@tool` wrapper in `app/agent/tools.py` with a clear docstring (the LLM reads this to decide when to use it)
3. Add to a `*_TOOLS` list and conditionally include in `get_tools()` based on settings
4. Add config fields to `app/config.py` and Helm values/templates

**Adding a new notification channel:**
1. Create `app/tools/yourchannel.py` with `post_rca_to_X(rca, dashboard_url)` function
2. Add webhook URL to `app/config.py`
3. Add call to `_notify()` in `app/webhook.py`
4. Add to Helm values, secret template, and deployment env vars

## Key patterns

- **K8s tools lazy-load config** — `_ensure_config()` in `app/tools/k8s.py` defers kubeconfig loading to first use so imports don't crash in CI without a cluster
- **LLM output parsing** — `_parse_agent_output()` extracts JSON from mixed text/markdown by finding first `{` and last `}`, because LLMs sometimes add reasoning text before the JSON
- **Database is optional** — `_use_db = bool(settings.database_url)` in webhook.py; falls back to in-memory dict if no Postgres configured
- **Postgres password preservation** — Helm secret template looks up existing password via `lookup` before generating a new `randAlphaNum` to survive upgrades
- **Tests mock K8s** — `_v1()` and `_apps_v1()` are functions (not module-level objects) so tests can `@patch` them without needing a real cluster

## Configuration

All env vars use `KLARSICHT_` prefix. Key ones:
- `LLM_PROVIDER` (anthropic/openai/ollama), `LLM_MODEL`, `LLM_API_KEY`, `LLM_BASE_URL`
- `MIMIR_ENDPOINT` — Prometheus: `http://prometheus:9090`, Mimir: `http://mimir:9009/prometheus`
- `GITLAB_URL`, `GITLAB_TOKEN`, `GITLAB_PROJECT` — enables CI/CD correlation tools
- `TEAMS_WEBHOOK_URL`, `SLACK_WEBHOOK_URL`, `DISCORD_WEBHOOK_URL`
- `DATABASE_URL` — empty = in-memory mode

## Deployment

- **GitLab CI**: test → build (Kaniko) → deploy (Helm) to outcept K8s cluster
- **GitHub Actions**: test → build → push to `ghcr.io/outcept/klarsicht/{agent,dashboard}`
- **Helm chart** at `helm/klarsicht/` — deploys agent, dashboard (nginx+React), postgres (optional)
- **Dashboard nginx** proxies: `/api/` → agent:8000, `/docs/` + `/blog/` → Hugo site, `/app/` → React SPA, `/` → landing page
- **RBAC**: ClusterRole is strictly read-only (pods, pods/log, events, deployments, replicasets, nodes)

## Sensitive files (not in public repo)

Internal files live in `../klars8t-internal/`: competitive analysis, cluster-specific Helm values, deploy manifests, stress test script. Keep them out of the public GitHub repo.
