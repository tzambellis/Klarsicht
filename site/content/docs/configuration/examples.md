---
title: Example Configurations
weight: 2
---

Ready-to-use Helm values for common deployment scenarios.

## Minimal (quickstart)

The bare minimum to get Klarsicht running. No ingress, no metrics, just the agent and dashboard.

```yaml
agent:
  llmProvider: anthropic
  llmApiKey: "sk-ant-..."
```

```bash
helm install klarsicht oci://ghcr.io/outcept/klarsicht/helm/klarsicht \
  -f values.yaml -n klarsicht --create-namespace
```

Access the dashboard via port-forward:

```bash
kubectl port-forward svc/klarsicht-dashboard -n klarsicht 8080:80
# Open http://localhost:8080/app/
```

---

## Production

Full setup with ingress, TLS, Prometheus, namespace scoping, and basic auth.

```yaml
agent:
  llmProvider: anthropic
  llmApiKey: ""  # set via --set or external secret
  metricsEndpoint: http://prometheus-server.monitoring.svc:9090
  watchNamespaces:
    - production
    - staging
  resources:
    requests:
      cpu: 200m
      memory: 512Mi
    limits:
      cpu: "2"
      memory: 1Gi

dashboard:
  ingress:
    enabled: true
    className: nginx
    host: klarsicht.mycompany.ch
    annotations:
      cert-manager.io/cluster-issuer: letsencrypt-prod
    tls:
      - hosts:
          - klarsicht.mycompany.ch
        secretName: klarsicht-tls
  auth:
    htpasswd: "admin:$apr1$xyz$..." # generate with: htpasswd -nb admin yourpassword

postgres:
  storage: 20Gi
  storageClassName: gp3  # or your storage class
  resources:
    requests:
      cpu: 250m
      memory: 512Mi
    limits:
      cpu: "1"
      memory: 1Gi
```

```bash
helm install klarsicht oci://ghcr.io/outcept/klarsicht/helm/klarsicht \
  -f values-production.yaml -n klarsicht --create-namespace \
  --set agent.llmApiKey=$ANTHROPIC_API_KEY
```

---

## Air-gapped (Ollama)

Fully on-prem. No external API calls. LLM runs inside the cluster via Ollama.

Prerequisites: Ollama running in your cluster with a model pulled.

```yaml
agent:
  llmProvider: ollama
  llmModel: llama3.1
  llmBaseUrl: http://ollama.ai-platform.svc:11434/v1
  metricsEndpoint: http://prometheus-server.monitoring.svc:9090
  watchNamespaces:
    - production

dashboard:
  ingress:
    enabled: true
    className: nginx
    host: klarsicht.internal.mycompany.ch
    tls:
      - hosts:
          - klarsicht.internal.mycompany.ch
        secretName: klarsicht-tls
```

```bash
helm install klarsicht oci://ghcr.io/outcept/klarsicht/helm/klarsicht \
  -f values-airgap.yaml -n klarsicht --create-namespace
```

> **Note:** No `llmApiKey` needed for Ollama. No traffic leaves the cluster.

---

## OpenAI / Azure OpenAI

Using GPT-4o or Azure-hosted models.

```yaml
# OpenAI
agent:
  llmProvider: openai
  llmModel: gpt-4o
  llmApiKey: ""  # set via --set

# Azure OpenAI
agent:
  llmProvider: openai
  llmModel: gpt-4o
  llmApiKey: ""
  llmBaseUrl: https://your-resource.openai.azure.com/openai/deployments/gpt-4o/v1
```

---

## vLLM / LiteLLM / Custom

Any OpenAI-compatible API endpoint works.

```yaml
agent:
  llmProvider: openai
  llmModel: meta-llama/Llama-3.1-70B-Instruct
  llmBaseUrl: http://vllm.ai-platform.svc:8000/v1
  llmApiKey: "not-needed"  # vLLM doesn't require auth by default
```

---

## External database

Use an existing PostgreSQL instead of the built-in one.

```yaml
postgres:
  enabled: false

externalDatabase:
  url: postgresql://klarsicht:secretpassword@postgres.database.svc:5432/klarsicht
```

---

## HAProxy ingress (K3s)

For K3s clusters with HAProxy ingress controller.

```yaml
dashboard:
  ingress:
    enabled: true
    className: haproxy
    host: klarsicht.example.com
    annotations:
      cert-manager.io/cluster-issuer: letsencrypt-prod
    tls:
      - hosts:
          - klarsicht.example.com
        secretName: klarsicht-tls
```

---

## Traefik ingress

For clusters using Traefik (default in K3s).

```yaml
dashboard:
  ingress:
    enabled: true
    className: traefik
    host: klarsicht.example.com
    annotations:
      cert-manager.io/cluster-issuer: letsencrypt-prod
      traefik.ingress.kubernetes.io/router.entrypoints: websecure
    tls:
      - hosts:
          - klarsicht.example.com
        secretName: klarsicht-tls
```

---

## Multi-namespace monitoring

Watch specific namespaces only. The agent will only investigate pods in these namespaces.

```yaml
agent:
  watchNamespaces:
    - production
    - staging
    - payments
```

Leave empty to watch all namespaces:

```yaml
agent:
  watchNamespaces: []
```
