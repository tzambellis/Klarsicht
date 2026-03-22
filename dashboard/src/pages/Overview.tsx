import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { fetchStats } from "../lib/api";
import type { StatsResponse } from "../lib/types";

function formatDuration(seconds: number): string {
  if (seconds < 60) return `${Math.round(seconds)}s`;
  const mins = Math.floor(seconds / 60);
  const secs = Math.round(seconds % 60);
  return secs > 0 ? `${mins}m ${secs}s` : `${mins}m`;
}

function formatTime(iso: string): string {
  return new Date(iso).toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export default function Overview() {
  const [stats, setStats] = useState<StatsResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    fetchStats()
      .then(setStats)
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  }, []);

  const total = stats?.total_incidents ?? 0;
  const resolved = stats?.completed ?? 0;
  const resolutionRate = total > 0 ? Math.round((resolved / total) * 100) : 0;

  return (
    <main className="mx-auto max-w-5xl px-6 py-10">
      {/* Hero Banner */}
      <div className="border border-white/[0.08] rounded-md p-6 md:p-8 mb-8 bg-gradient-to-br from-[#22c55e]/[0.03] to-transparent">
        <div className="flex items-start justify-between gap-4 flex-wrap">
          <div>
            <div className="flex items-center gap-2 mb-3">
              <span className="h-2 w-2 rounded-full bg-[#22c55e] shadow-[0_0_8px_rgba(34,197,94,0.5)]"></span>
              <span className="text-xs text-[#22c55e] font-medium uppercase tracking-wider">Live</span>
            </div>
            <h2 className="text-2xl md:text-3xl font-bold tracking-tight mb-2">
              AI-Powered Root Cause Analysis
            </h2>
            <p className="text-sm text-[#888] max-w-md">
              Autonomous incident investigation for Kubernetes. Self-hosted, FINMA-ready, no data leaves your cluster.
            </p>
          </div>
          <div className="text-right">
            <div className="text-4xl md:text-5xl font-bold font-mono text-[#22c55e] tracking-tighter">
              {resolutionRate}%
            </div>
            <div className="text-xs text-[#888] uppercase tracking-wider mt-1">Resolution Rate</div>
          </div>
        </div>
      </div>

      {loading && (
        <div className="flex items-center gap-3 text-[#888] py-20 justify-center">
          <Spinner />
          <span className="text-sm">Loading stats...</span>
        </div>
      )}

      {error && (
        <div className="rounded-md border border-red-500/20 bg-red-500/5 px-4 py-3 text-red-400 text-sm">
          {error}
        </div>
      )}

      {stats && (
        <div className="space-y-6">
          {/* Stats cards */}
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
            <StatCard label="Total Incidents" value={stats.total_incidents} />
            <StatCard label="Resolved" value={stats.completed} accent />
            <StatCard label="Investigating" value={stats.investigating} />
            <StatCard
              label="Avg Investigation"
              value={formatDuration(stats.avg_investigation_seconds)}
              sub="per incident"
            />
          </div>

          {/* Key Metrics */}
          <div className="grid grid-cols-3 gap-3">
            <div className="border border-white/[0.08] rounded-md p-4 text-center">
              <p className="text-2xl font-bold font-mono text-white">
                {stats.avg_investigation_seconds > 0 ? "<60s" : "-"}
              </p>
              <p className="text-[10px] text-[#555] uppercase tracking-wider mt-1">Time to RCA</p>
            </div>
            <div className="border border-white/[0.08] rounded-md p-4 text-center">
              <p className="text-2xl font-bold font-mono text-[#22c55e]">
                {stats.category_breakdown.length}
              </p>
              <p className="text-[10px] text-[#555] uppercase tracking-wider mt-1">Root Cause Categories</p>
            </div>
            <div className="border border-white/[0.08] rounded-md p-4 text-center">
              <p className="text-2xl font-bold font-mono text-white">0</p>
              <p className="text-[10px] text-[#555] uppercase tracking-wider mt-1">Data Sent Outside</p>
            </div>
          </div>

          {/* Two-column row: Top Alerts + Top Namespaces */}
          <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
            <section className="border border-white/[0.08] rounded-md p-6">
              <SectionTitle>Top Alerts</SectionTitle>
              {stats.top_alerts.length === 0 ? (
                <p className="text-sm text-[#555]">No alerts yet</p>
              ) : (
                <div className="space-y-3">
                  {stats.top_alerts.map((a) => (
                    <BarItem
                      key={a.alert_name}
                      label={a.alert_name}
                      count={a.count}
                      max={stats.top_alerts[0].count}
                    />
                  ))}
                </div>
              )}
            </section>

            <section className="border border-white/[0.08] rounded-md p-6">
              <SectionTitle>Top Namespaces</SectionTitle>
              {stats.top_namespaces.length === 0 ? (
                <p className="text-sm text-[#555]">No namespaces yet</p>
              ) : (
                <div className="space-y-3">
                  {stats.top_namespaces.map((ns) => (
                    <BarItem
                      key={ns.namespace}
                      label={ns.namespace}
                      count={ns.count}
                      max={stats.top_namespaces[0].count}
                    />
                  ))}
                </div>
              )}
            </section>
          </div>

          {/* Category Breakdown */}
          <section className="border border-white/[0.08] rounded-md p-6">
            <SectionTitle>Root Cause Categories</SectionTitle>
            {stats.category_breakdown.length === 0 ? (
              <p className="text-sm text-[#555]">No root cause categories yet</p>
            ) : (
              <div className="space-y-3">
                {stats.category_breakdown.map((cat) => (
                  <BarItem
                    key={cat.category}
                    label={cat.category.replace(/_/g, " ")}
                    count={cat.count}
                    max={stats.category_breakdown[0].count}
                  />
                ))}
              </div>
            )}
          </section>

          {/* Recent Incidents */}
          <section className="border border-white/[0.08] rounded-md p-6">
            <div className="flex items-center justify-between mb-4">
              <SectionTitle>Recent Incidents</SectionTitle>
              <Link
                to="/incidents"
                className="text-xs text-[#888] hover:text-white transition-colors"
              >
                View all &rarr;
              </Link>
            </div>
            {stats.recent_incidents.length === 0 ? (
              <p className="text-sm text-[#555]">No incidents yet</p>
            ) : (
              <div className="space-y-1">
                {stats.recent_incidents.map((inc) => (
                  <Link
                    key={inc.incident_id}
                    to={`/incidents/${inc.incident_id}`}
                    className="flex items-center gap-3 rounded px-3 py-2.5 hover:bg-white/[0.02] transition-colors group"
                  >
                    <StatusDot status={inc.status} />
                    <div className="flex-1 min-w-0">
                      <span className="text-sm text-white group-hover:underline underline-offset-4 decoration-white/30">
                        {inc.alert_name}
                      </span>
                      <span className="hidden sm:inline ml-2 font-mono text-xs text-[#555]">
                        {inc.namespace}/{inc.pod}
                      </span>
                    </div>
                    {inc.confidence !== null && (
                      <span
                        className={`text-xs font-mono ${
                          inc.confidence >= 0.8 ? "text-[#22c55e]" : "text-[#888]"
                        }`}
                      >
                        {Math.round(inc.confidence * 100)}%
                      </span>
                    )}
                    <span className="text-xs text-[#555] hidden sm:inline">
                      {inc.started_at ? formatTime(inc.started_at) : "-"}
                    </span>
                  </Link>
                ))}
              </div>
            )}
          </section>

          {/* Compliance footer */}
          <div className="flex flex-wrap items-center gap-4 text-[10px] text-[#555] uppercase tracking-wider pt-2">
            <span className="flex items-center gap-1.5">
              <span className="h-1 w-1 rounded-full bg-[#22c55e]"></span>
              Self-hosted
            </span>
            <span className="flex items-center gap-1.5">
              <span className="h-1 w-1 rounded-full bg-[#22c55e]"></span>
              Read-only K8s access
            </span>
            <span className="flex items-center gap-1.5">
              <span className="h-1 w-1 rounded-full bg-[#22c55e]"></span>
              On-prem LLM ready
            </span>
            <span className="flex items-center gap-1.5">
              <span className="h-1 w-1 rounded-full bg-[#22c55e]"></span>
              FINMA / GDPR compliant
            </span>
          </div>
        </div>
      )}
    </main>
  );
}

function StatCard({
  label,
  value,
  accent,
  sub,
}: {
  label: string;
  value: string | number;
  accent?: boolean;
  sub?: string;
}) {
  return (
    <div className="border border-white/[0.08] rounded-md p-4 md:p-5">
      <p className="text-[10px] font-medium uppercase tracking-wider text-[#888] mb-1.5">{label}</p>
      <p className={`text-2xl md:text-3xl font-semibold ${accent ? "text-[#22c55e]" : "text-white"}`}>
        {value}
      </p>
      {sub && <p className="text-[10px] text-[#555] mt-0.5">{sub}</p>}
    </div>
  );
}

function SectionTitle({ children }: { children: React.ReactNode }) {
  return (
    <h3 className="text-xs font-medium uppercase tracking-wider text-[#888] mb-4">{children}</h3>
  );
}

function BarItem({ label, count, max }: { label: string; count: number; max: number }) {
  const pct = max > 0 ? (count / max) * 100 : 0;
  return (
    <div>
      <div className="flex items-center justify-between text-sm mb-1.5">
        <span className="text-white/90 truncate mr-2">{label}</span>
        <span className="text-[#888] font-mono text-xs shrink-0">{count}</span>
      </div>
      <div className="h-1.5 rounded-full bg-white/[0.08] overflow-hidden">
        <div
          className="h-full rounded-full bg-[#22c55e] transition-all duration-500"
          style={{ width: `${pct}%` }}
        />
      </div>
    </div>
  );
}

function StatusDot({ status }: { status: string }) {
  const color =
    status === "completed"
      ? "bg-[#22c55e]"
      : status === "failed"
        ? "bg-red-500"
        : "bg-amber-400 animate-pulse";
  return <span className={`h-1.5 w-1.5 rounded-full shrink-0 ${color}`} />;
}

function Spinner() {
  return (
    <svg className="animate-spin h-4 w-4 text-[#888]" viewBox="0 0 24 24" fill="none">
      <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
      <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
    </svg>
  );
}
