import { useEffect, useState } from "react";

// NOTE: Phase 5 ships the TS SDK (@mini-cloud/sdk) with a typed client, config, and obs. Until
// then this template is "backend-complete": it wires config/routing/Grafana but calls the API with
// a plain fetch through the /api proxy. Replace this with the SDK client when it lands.
export function App() {
  const [status, setStatus] = useState("…");

  useEffect(() => {
    fetch("/api/healthz")
      .then((r) => r.json())
      .then((d) => setStatus(d.status ?? "unknown"))
      .catch(() => setStatus("unreachable"));
  }, []);

  return (
    <main style={{ fontFamily: "system-ui", padding: "2rem" }}>
      <h1>{{name}}</h1>
      <p>API health: <strong>{status}</strong></p>
      <p>Scaffolded by <code>mini new --type vite</code>.</p>
    </main>
  );
}
