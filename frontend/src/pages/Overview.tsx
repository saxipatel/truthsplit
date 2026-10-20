import { useEffect, useState } from "react";

import { fetchReadiness, type Readiness } from "../api/client";

type State = { kind: "loading" } | { kind: "ok"; data: Readiness } | { kind: "error"; message: string };

export default function Overview() {
  const [state, setState] = useState<State>({ kind: "loading" });

  useEffect(() => {
    fetchReadiness()
      .then((data) => setState({ kind: "ok", data }))
      .catch((err: Error) => setState({ kind: "error", message: err.message }));
  }, []);

  return (
    <section aria-labelledby="overview-title" className="space-y-4">
      <h1 id="overview-title" className="text-xl font-semibold">
        Overview
      </h1>
      <div className="rounded-md border border-slate-200 bg-white p-4 text-sm">
        <h2 className="mb-2 font-medium">Backend status</h2>
        {state.kind === "loading" && <p className="text-slate-500">Checking API…</p>}
        {state.kind === "error" && (
          <p role="alert" className="text-red-700">
            API unavailable: {state.message}
          </p>
        )}
        {state.kind === "ok" && (
          <dl className="grid grid-cols-[max-content_1fr] gap-x-4 gap-y-1">
            <dt className="text-slate-500">API</dt>
            <dd>{state.data.status}</dd>
            <dt className="text-slate-500">Metadata database</dt>
            <dd>{state.data.database}</dd>
            <dt className="text-slate-500">Schema revision</dt>
            <dd className="font-mono">{state.data.schema_revision}</dd>
          </dl>
        )}
      </div>
    </section>
  );
}
