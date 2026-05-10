import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { manualInvestigate } from "../lib/api";

export default function Investigate() {
  const navigate = useNavigate();
  const [namespace, setNamespace] = useState("");
  const [pod, setPod] = useState("");
  const [context, setContext] = useState("");
  const [severity, setSeverity] = useState("info");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (busy) return;
    setBusy(true);
    setError(null);
    try {
      const { incident_id } = await manualInvestigate({ namespace: namespace.trim(), pod: pod.trim(), context: context.trim(), severity });
      navigate(`/incidents/${incident_id}`);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setBusy(false);
    }
  };

  return (
    <main className="mx-auto max-w-3xl px-6 py-10">
      <h1 className="text-2xl font-semibold tracking-tight mb-2">Investigate a pod</h1>
      <p className="text-sm text-[#888] mb-8">
        Run an RCA on demand. Same pipeline as a Grafana alert — pick a namespace and pod, optionally tell the agent
        anything you know about the workload.
      </p>

      <form onSubmit={submit} className="space-y-5">
        <div className="grid grid-cols-1 md:grid-cols-2 gap-5">
          <div>
            <label className="block text-xs font-medium text-[#888] uppercase tracking-wider mb-2">Namespace</label>
            <input
              type="text"
              required
              value={namespace}
              onChange={(e) => setNamespace(e.target.value)}
              placeholder="e.g. demo"
              className="w-full rounded border border-white/[0.12] bg-white/[0.02] px-3 py-2 text-sm text-white placeholder:text-[#555] focus:outline-none focus:border-white/[0.25]"
            />
          </div>
          <div>
            <label className="block text-xs font-medium text-[#888] uppercase tracking-wider mb-2">Pod</label>
            <input
              type="text"
              required
              value={pod}
              onChange={(e) => setPod(e.target.value)}
              placeholder="e.g. my-app-7d9f8b-xkj2p"
              className="w-full rounded border border-white/[0.12] bg-white/[0.02] px-3 py-2 text-sm text-white placeholder:text-[#555] focus:outline-none focus:border-white/[0.25]"
            />
          </div>
        </div>

        <div>
          <label className="block text-xs font-medium text-[#888] uppercase tracking-wider mb-2">
            Context for the agent <span className="text-[#555] normal-case font-normal">(optional)</span>
          </label>
          <textarea
            value={context}
            onChange={(e) => setContext(e.target.value)}
            rows={6}
            placeholder="What do you know about this app? Recent changes, expected behaviour, env vars it depends on, etc. The agent will read this before investigating."
            className="w-full rounded border border-white/[0.12] bg-white/[0.02] px-3 py-2 text-sm text-white placeholder:text-[#555] focus:outline-none focus:border-white/[0.25] font-mono"
          />
        </div>

        <div>
          <label className="block text-xs font-medium text-[#888] uppercase tracking-wider mb-2">Severity label</label>
          <select
            value={severity}
            onChange={(e) => setSeverity(e.target.value)}
            className="rounded border border-white/[0.12] bg-white/[0.02] px-3 py-2 text-sm text-white focus:outline-none focus:border-white/[0.25]"
          >
            <option value="info">info</option>
            <option value="warning">warning</option>
            <option value="critical">critical</option>
          </select>
        </div>

        {error && (
          <div className="rounded border border-red-500/20 bg-red-500/5 px-3 py-2 text-xs text-red-300">
            {error}
          </div>
        )}

        <div className="flex justify-end pt-2">
          <button
            type="submit"
            disabled={busy || !namespace.trim() || !pod.trim()}
            className="rounded bg-white text-black px-4 py-2 text-sm font-medium hover:bg-white/90 transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
          >
            {busy ? "Starting investigation…" : "Investigate"}
          </button>
        </div>
      </form>
    </main>
  );
}
