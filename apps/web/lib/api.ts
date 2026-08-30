const BASE = process.env.NEXT_PUBLIC_API_URL ?? "http://127.0.0.1:8080";

export const api = {
  workflow: () => fetch(`${BASE}/api/workflow`).then((r) => r.json()),
  incident: () => fetch(`${BASE}/api/incident`).then((r) => r.json()),
  investigate: () => fetch(`${BASE}/api/investigate`, { method: "POST" }),
  reset: () => fetch(`${BASE}/api/reset`, { method: "POST" }),
  approve: (actor: string) =>
    fetch(`${BASE}/api/approve`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ actor }),
    }),
  reject: (actor: string, reason: string) =>
    fetch(`${BASE}/api/reject`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ actor, reason }),
    }),
  eventsUrl: () => `${BASE}/api/events`,
};
