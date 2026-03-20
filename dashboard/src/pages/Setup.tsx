import { useState, useCallback } from "react";

const TEST_CRASHLOOP_YAML = `# test-crashloop.yaml
# A deployment that deliberately crash-loops due to a missing DATABASE_URL env var.
# Apply this to test Klarsicht's RCA pipeline end-to-end.
#
# Usage:
#   kubectl apply -f test-crashloop.yaml
#   Watch Klarsicht investigate: https://klarsicht.vibebros.net/incidents
#   Clean up: kubectl delete -f test-crashloop.yaml

apiVersion: v1
kind: Namespace
metadata:
  name: demo
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: test-crashloop
  namespace: demo
  labels:
    app: test-crashloop
    purpose: klarsicht-testing
spec:
  replicas: 1
  selector:
    matchLabels:
      app: test-crashloop
  template:
    metadata:
      labels:
        app: test-crashloop
    spec:
      containers:
        - name: app
          image: python:3.13-slim
          command:
            - python3
            - "-c"
            - |
              import os, sys, time
              time.sleep(2)  # Brief pause so logs are visible
              db_url = os.environ.get("DATABASE_URL")
              if not db_url:
                  print("ERROR: DATABASE_URL environment variable is not set", file=sys.stderr)
                  print("The application cannot start without a database connection.", file=sys.stderr)
                  sys.exit(1)
              print(f"Connected to {db_url}")
          # Note: DATABASE_URL is intentionally NOT set, causing the crash.
          resources:
            limits:
              memory: "64Mi"
              cpu: "100m"
            requests:
              memory: "32Mi"
              cpu: "50m"`;


const WEBHOOK_URL = `${window.location.protocol}//${window.location.host}/api/alert`;
const INTERNAL_URL = "http://klarsicht-agent.klarsicht.svc:8000/alert";

export default function Setup() {
  return (
    <main className="mx-auto max-w-5xl px-6 py-10">
      <div className="mb-10">
        <h2 className="text-xl font-semibold tracking-tight">Setup</h2>
        <p className="text-sm text-[#888] mt-1">Connect Grafana to Klarsicht to start receiving root cause analyses.</p>
      </div>

      <div className="space-y-6">
        {/* Test */}
        <TestAlertCard />

        {/* Step 1 */}
        <Card step={1} title="Create a Contact Point">
          <p className="text-sm text-[#888] mb-4">
            In Grafana, go to <strong className="text-white">Alerting &rarr; Contact points</strong> and click <strong className="text-white">Add contact point</strong>.
          </p>
          <FieldList fields={[
            { label: "Name", value: "klarsicht" },
            { label: "Type", value: "Webhook" },
            { label: "URL (in-cluster)", value: INTERNAL_URL },
            { label: "URL (external)", value: WEBHOOK_URL },
            { label: "HTTP Method", value: "POST" },
          ]} />
          <p className="text-xs text-[#555] mt-4">
            Use the in-cluster URL if Grafana runs in the same Kubernetes cluster. Use the external URL if Grafana is outside the cluster.
          </p>
        </Card>

        {/* Step 2 */}
        <Card step={2} title="Create a Notification Policy">
          <p className="text-sm text-[#888] mb-4">
            Go to <strong className="text-white">Alerting &rarr; Notification policies</strong>. Either set <strong className="text-white">klarsicht</strong> as the default contact point, or add a nested policy.
          </p>
          <p className="text-sm text-[#888] mb-3">Recommended: route only critical alerts to Klarsicht.</p>
          <FieldList fields={[
            { label: "Matching labels", value: "severity = critical" },
            { label: "Contact point", value: "klarsicht" },
          ]} />
        </Card>

        {/* Step 3 */}
        <Card step={3} title="Create an Alert Rule">
          <p className="text-sm text-[#888] mb-4">
            Go to <strong className="text-white">Alerting &rarr; Alert rules</strong> and create a new rule. The alert labels <strong className="text-white">must include <code className="text-[#22c55e] bg-white/[0.05] px-1 rounded">namespace</code> and <code className="text-[#22c55e] bg-white/[0.05] px-1 rounded">pod</code></strong> so Klarsicht knows which workload to inspect.
          </p>
          <p className="text-sm text-[#888] mb-3">Example alert rule for CrashLoopBackOff:</p>
          <CodeBlock code={`# PromQL query
kube_pod_container_status_waiting_reason{reason="CrashLoopBackOff"} > 0

# Labels to add
severity = critical
alertname = CrashLoopBackOff
namespace = {{ $labels.namespace }}
pod = {{ $labels.pod }}`} />
          <p className="text-xs text-[#555] mt-4">
            Klarsicht works best with alerts that include namespace, pod, and alertname labels. It can also use node, container, and deployment labels when available.
          </p>
        </Card>

        {/* Step 4 */}
        <Card step={4} title="Test with a Real Failure">
          <p className="text-sm text-[#888] mb-4">
            Deploy a pod that <strong className="text-white">intentionally crashes</strong> due to a missing <code className="text-[#22c55e] bg-white/[0.05] px-1 rounded text-xs">DATABASE_URL</code> environment variable. This triggers a real CrashLoopBackOff that Klarsicht can investigate end-to-end.
          </p>
          <p className="text-sm text-[#888] mb-3">Save this YAML and apply it to your cluster:</p>
          <CodeBlock code={TEST_CRASHLOOP_YAML} />
          <div className="mt-4 space-y-3">
            <div>
              <p className="text-xs font-medium text-[#888] uppercase tracking-wider mb-2">Apply</p>
              <CodeBlock code="kubectl apply -f test-crashloop.yaml" />
            </div>
            <div>
              <p className="text-xs font-medium text-[#888] uppercase tracking-wider mb-2">Clean up</p>
              <CodeBlock code="kubectl delete -f test-crashloop.yaml" />
            </div>
          </div>
          <p className="text-xs text-[#555] mt-4">
            Requires a Grafana alert rule for CrashLoopBackOff (Step 3) so the alert fires and reaches Klarsicht.
            If you just want to test the mock path without a real cluster failure, use the <strong className="text-[#888]">Send Test Alert</strong> button above instead.
          </p>
        </Card>

        {/* Step 5 */}
        <Card step={5} title="Verify">
          <p className="text-sm text-[#888] mb-4">
            Send a test alert using the button above, or wait for a real alert to fire. Once received, Klarsicht will:
          </p>
          <ol className="space-y-2 text-sm text-[#888]">
            <li className="flex items-start gap-2.5">
              <span className="shrink-0 flex h-5 w-5 items-center justify-center rounded-full border border-white/[0.15] text-xs text-[#888]">1</span>
              Inspect the pod via the Kubernetes API (status, logs, events)
            </li>
            <li className="flex items-start gap-2.5">
              <span className="shrink-0 flex h-5 w-5 items-center justify-center rounded-full border border-white/[0.15] text-xs text-[#888]">2</span>
              Query Prometheus metrics for anomalies in the incident window
            </li>
            <li className="flex items-start gap-2.5">
              <span className="shrink-0 flex h-5 w-5 items-center justify-center rounded-full border border-white/[0.15] text-xs text-[#888]">3</span>
              Correlate with recent deployments and upstream/downstream pods
            </li>
            <li className="flex items-start gap-2.5">
              <span className="shrink-0 flex h-5 w-5 items-center justify-center rounded-full border border-white/[0.15] text-xs text-[#888]">4</span>
              Produce a root cause analysis with fix steps and a postmortem draft
            </li>
          </ol>
          <p className="text-sm text-[#888] mt-4">
            Results appear on the <a href="/incidents" className="text-white hover:underline underline-offset-4 decoration-white/30">Incidents</a> page.
          </p>
        </Card>

        {/* API Setup */}
        <Card title="Alternative: Setup via Grafana API">
          <p className="text-sm text-[#888] mb-4">
            If you prefer automation over the UI, you can configure everything via <code className="text-[#22c55e] bg-white/[0.05] px-1 rounded text-xs">curl</code> and a Grafana Service Account token.
          </p>

          <div className="space-y-4">
            <div>
              <h4 className="text-xs font-medium text-[#888] uppercase tracking-wider mb-2">1. Create a Service Account Token</h4>
              <p className="text-xs text-[#888] mb-2">
                In Grafana: <strong className="text-white">Administration &rarr; Service accounts &rarr; Add service account</strong> (role: Editor). Then create a token.
              </p>
            </div>

            <div>
              <h4 className="text-xs font-medium text-[#888] uppercase tracking-wider mb-2">2. Create the Contact Point</h4>
              <CodeBlock code={`curl -s -X POST \\
  https://YOUR_GRAFANA_URL/api/v1/provisioning/contact-points \\
  -H "Authorization: Bearer YOUR_SERVICE_ACCOUNT_TOKEN" \\
  -H "Content-Type: application/json" \\
  -d '{
    "name": "klarsicht",
    "type": "webhook",
    "settings": {
      "url": "${INTERNAL_URL}",
      "httpMethod": "POST"
    }
  }'`} />
            </div>

            <div>
              <h4 className="text-xs font-medium text-[#888] uppercase tracking-wider mb-2">3. Set as Default Notification Policy</h4>
              <CodeBlock code={`curl -s -X PUT \\
  https://YOUR_GRAFANA_URL/api/v1/provisioning/policies \\
  -H "Authorization: Bearer YOUR_SERVICE_ACCOUNT_TOKEN" \\
  -H "Content-Type: application/json" \\
  -d '{
    "receiver": "klarsicht",
    "group_by": ["grafana_folder", "alertname"],
    "group_wait": "30s",
    "group_interval": "5m",
    "repeat_interval": "4h"
  }'`} />
            </div>

            <div>
              <h4 className="text-xs font-medium text-[#888] uppercase tracking-wider mb-2">4. Create a Sample Alert Rule</h4>
              <CodeBlock code={`curl -s -X POST \\
  https://YOUR_GRAFANA_URL/api/v1/provisioning/alert-rules \\
  -H "Authorization: Bearer YOUR_SERVICE_ACCOUNT_TOKEN" \\
  -H "Content-Type: application/json" \\
  -d '{
    "title": "CrashLoopBackOff",
    "ruleGroup": "klarsicht",
    "folderUID": "default",
    "condition": "C",
    "for": "1m",
    "labels": { "severity": "critical" },
    "annotations": {
      "summary": "Pod {{ $labels.pod }} is crash looping in {{ $labels.namespace }}"
    },
    "data": [
      {
        "refId": "A",
        "relativeTimeRange": { "from": 300, "to": 0 },
        "datasourceUid": "prometheus",
        "model": {
          "expr": "kube_pod_container_status_waiting_reason{reason=\\"CrashLoopBackOff\\"} > 0",
          "refId": "A"
        }
      },
      {
        "refId": "C",
        "relativeTimeRange": { "from": 0, "to": 0 },
        "datasourceUid": "-100",
        "model": {
          "type": "reduce",
          "expression": "A",
          "reducer": "last",
          "refId": "C"
        }
      }
    ]
  }'`} />
            </div>

            <GrafanaAutoSetup />
          </div>
        </Card>

        {/* HMAC */}
        <Card title="Optional: HMAC Authentication">
          <p className="text-sm text-[#888] mb-4">
            To verify that alerts are genuinely from Grafana, configure HMAC-SHA256 signing.
          </p>
          <p className="text-sm text-[#888] mb-3">
            1. Set <code className="text-[#22c55e] bg-white/[0.05] px-1 rounded text-xs">grafana.webhookSecret</code> in your Helm values to a shared secret.
          </p>
          <p className="text-sm text-[#888] mb-3">
            2. In the Grafana contact point, add a custom header:
          </p>
          <FieldList fields={[
            { label: "Header", value: "X-Grafana-Alerting-Signature" },
            { label: "Value", value: "HMAC-SHA256 hex digest of the request body" },
          ]} />
          <p className="text-xs text-[#555] mt-4">
            Note: Grafana's built-in webhook does not natively compute HMAC signatures. You may need a proxy or custom contact point to add this header.
          </p>
        </Card>
      </div>
    </main>
  );
}

function TestAlertCard() {
  const [status, setStatus] = useState<"idle" | "sending" | "sent" | "error">("idle");
  const [response, setResponse] = useState<string | null>(null);

  const sendTest = useCallback(async () => {
    setStatus("sending");
    setResponse(null);
    try {
      const res = await fetch("/api/test", { method: "POST" });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`);
      setResponse(`Accepted. Incident ID: ${data.incidents?.[0] ?? "unknown"}`);
      setStatus("sent");
    } catch (e: unknown) {
      setResponse(e instanceof Error ? e.message : "Failed to send test alert");
      setStatus("error");
    }
  }, []);

  return (
    <div className="border border-white/[0.08] rounded-md p-6 bg-white/[0.01]">
      <div className="flex flex-wrap items-center justify-between gap-4">
        <div>
          <h3 className="text-sm font-semibold text-white mb-1">Test Alert</h3>
          <p className="text-xs text-[#888]">
            Send a mock CrashLoopBackOff alert (simulates a <code className="text-[#22c55e] bg-white/[0.05] px-1 rounded">test-crashloop</code> pod with a missing DATABASE_URL) to verify the pipeline works end-to-end.
          </p>
        </div>
        <button
          onClick={sendTest}
          disabled={status === "sending"}
          className="rounded bg-white text-black text-sm font-medium px-4 py-2 hover:bg-white/90 disabled:opacity-50 transition-colors"
        >
          {status === "sending" ? "Sending..." : "Send Test Alert"}
        </button>
      </div>
      {response && (
        <div className={`mt-4 rounded border px-3 py-2 text-xs font-mono ${
          status === "error"
            ? "border-red-500/20 bg-red-500/5 text-red-400"
            : "border-[#22c55e]/20 bg-[#22c55e]/5 text-[#22c55e]"
        }`}>
          {response}
        </div>
      )}
    </div>
  );
}

function Card({ step, title, children }: { step?: number; title: string; children: React.ReactNode }) {
  return (
    <div className="border border-white/[0.08] rounded-md p-6">
      <div className="flex items-start gap-3 mb-4">
        {step != null && (
          <span className="shrink-0 flex h-6 w-6 items-center justify-center rounded-full border border-white/[0.15] text-xs font-medium text-[#888]">
            {step}
          </span>
        )}
        <h3 className="text-sm font-semibold text-white">{title}</h3>
      </div>
      {children}
    </div>
  );
}

function FieldList({ fields }: { fields: { label: string; value: string }[] }) {
  return (
    <div className="rounded border border-white/[0.08] overflow-hidden">
      {fields.map((f, i) => (
        <div
          key={f.label}
          className={`flex items-center text-sm ${i > 0 ? "border-t border-white/[0.04]" : ""}`}
        >
          <span className="text-[#888] text-xs w-36 shrink-0 px-3 py-2 bg-white/[0.02]">{f.label}</span>
          <CopyableValue value={f.value} />
        </div>
      ))}
    </div>
  );
}

function CopyableValue({ value }: { value: string }) {
  const [copied, setCopied] = useState(false);
  const copy = useCallback(() => {
    navigator.clipboard.writeText(value).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    });
  }, [value]);

  return (
    <div className="flex items-center flex-1 min-w-0 px-3 py-2 gap-2">
      <code className="text-xs font-mono text-white/90 truncate flex-1">{value}</code>
      <button
        onClick={copy}
        className="shrink-0 text-xs text-[#555] hover:text-white transition-colors"
      >
        {copied ? "Copied" : "Copy"}
      </button>
    </div>
  );
}

function GrafanaAutoSetup() {
  const [grafanaUrl, setGrafanaUrl] = useState("");
  const [token, setToken] = useState("");
  const [status, setStatus] = useState<"idle" | "running" | "done" | "error">("idle");
  const [log, setLog] = useState<string[]>([]);

  const run = useCallback(async () => {
    if (!grafanaUrl || !token) return;
    setStatus("running");
    setLog(["Configuring Grafana via Klarsicht backend..."]);

    try {
      const res = await fetch("/api/grafana-setup", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ grafana_url: grafanaUrl.replace(/\/+$/, ""), token }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`);
      setLog(data.steps || ["Done."]);
      setStatus("done");
    } catch (e: unknown) {
      setLog([`Error: ${e instanceof Error ? e.message : String(e)}`]);
      setStatus("error");
    }
  }, [grafanaUrl, token]);

  return (
    <div className="mt-6 border-t border-white/[0.08] pt-6">
      <h4 className="text-xs font-medium text-[#888] uppercase tracking-wider mb-3">One-Click Setup</h4>
      <p className="text-xs text-[#888] mb-4">
        Enter your Grafana URL and a Service Account token to auto-configure the contact point and notification policy.
      </p>
      <div className="space-y-3 mb-4">
        <input
          type="url"
          placeholder="https://grafana.example.com"
          value={grafanaUrl}
          onChange={(e) => setGrafanaUrl(e.target.value)}
          className="w-full rounded border border-white/[0.08] bg-white/[0.02] px-3 py-2 text-sm text-white placeholder-[#555] outline-none focus:border-white/[0.2]"
        />
        <input
          type="password"
          placeholder="Service Account Token (glsa_...)"
          value={token}
          onChange={(e) => setToken(e.target.value)}
          className="w-full rounded border border-white/[0.08] bg-white/[0.02] px-3 py-2 text-sm text-white placeholder-[#555] outline-none focus:border-white/[0.2]"
        />
      </div>
      <button
        onClick={run}
        disabled={!grafanaUrl || !token || status === "running"}
        className="rounded bg-white text-black text-sm font-medium px-4 py-2 hover:bg-white/90 disabled:opacity-50 transition-colors"
      >
        {status === "running" ? "Configuring..." : "Configure Grafana"}
      </button>
      {log.length > 0 && (
        <div className={`mt-4 rounded border px-3 py-2 text-xs font-mono space-y-0.5 ${
          status === "error"
            ? "border-red-500/20 bg-red-500/5 text-red-400"
            : "border-[#22c55e]/20 bg-[#22c55e]/5 text-[#22c55e]"
        }`}>
          {log.map((l, i) => <div key={i}>{l}</div>)}
        </div>
      )}
    </div>
  );
}

function CodeBlock({ code }: { code: string }) {
  const [copied, setCopied] = useState(false);
  const copy = useCallback(() => {
    navigator.clipboard.writeText(code).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    });
  }, [code]);

  return (
    <div className="relative rounded border border-white/[0.08] bg-white/[0.02]">
      <button
        onClick={copy}
        className="absolute top-2 right-2 text-xs text-[#555] hover:text-white transition-colors"
      >
        {copied ? "Copied" : "Copy"}
      </button>
      <pre className="p-4 text-xs font-mono text-[#22c55e] overflow-x-auto leading-relaxed">{code}</pre>
    </div>
  );
}
