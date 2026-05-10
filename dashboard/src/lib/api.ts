import type { IncidentEntry, IncidentsResponse, InvestigationProgress, StatsResponse } from "./types";
import { authFetch } from "../auth";

const BASE = "/api";

export async function fetchIncidents(): Promise<IncidentsResponse> {
  const res = await authFetch(`${BASE}/incidents`);
  if (!res.ok) throw new Error(`Failed to fetch incidents: ${res.status}`);
  return res.json();
}

export async function fetchIncident(id: string): Promise<IncidentEntry> {
  const res = await authFetch(`${BASE}/incidents/${encodeURIComponent(id)}`);
  if (!res.ok) throw new Error(`Failed to fetch incident ${id}: ${res.status}`);
  return res.json();
}

export async function fetchStats(): Promise<StatsResponse> {
  const res = await authFetch(`${BASE}/stats`);
  if (!res.ok) throw new Error(`Failed to fetch stats: ${res.status}`);
  return res.json();
}

export async function fetchSteps(id: string): Promise<InvestigationProgress> {
  const res = await authFetch(`${BASE}/incidents/${encodeURIComponent(id)}/steps`);
  if (!res.ok) throw new Error(`Failed to fetch steps: ${res.status}`);
  return res.json();
}

export async function retryIncident(id: string): Promise<void> {
  const res = await authFetch(`${BASE}/incidents/${encodeURIComponent(id)}/retry`, { method: "POST" });
  if (!res.ok) {
    const detail = await res.text().catch(() => "");
    throw new Error(detail || `Retry failed: ${res.status}`);
  }
}

export async function manualInvestigate(input: {
  namespace: string;
  pod: string;
  context?: string;
  severity?: string;
}): Promise<{ incident_id: string }> {
  const res = await authFetch(`${BASE}/investigate`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(input),
  });
  if (!res.ok) {
    const detail = await res.text().catch(() => "");
    throw new Error(detail || `Investigate failed: ${res.status}`);
  }
  return res.json();
}
