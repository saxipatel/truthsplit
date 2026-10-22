import type {
  AuditEvent,
  CatalogTable,
  ColumnMapping,
  Connection,
  ConnectionTest,
  Dataset,
  DatasetProfile,
  DnaComparison,
  Evidence,
  Freshness,
  ImportIssue,
  Investigation,
  InvestigationSummary,
  MetricDefinition,
  MetricSpec,
  Note,
  Page,
  Pattern,
  Preview,
  RegisteredQuery,
  SettingsSummary,
  SimilarMatch,
  Simulation,
  UploadResponse,
} from "./types";

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}

const TIMEOUT_MS = 60_000;

function describe(body: unknown, resp: Response): string {
  const detail = (body as { detail?: unknown } | null)?.detail ?? (body as { database?: unknown } | null)?.database;
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    // FastAPI validation errors: [{loc: [...], msg: "..."}]
    return detail
      .map((d: { loc?: unknown[]; msg?: string }) => `${(d.loc ?? []).filter((p) => p !== "body").join(".")}: ${d.msg ?? ""}`)
      .join("; ");
  }
  return `HTTP ${resp.status} ${resp.statusText}`;
}

export async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let resp: Response;
  try {
    resp = await fetch(path, { signal: AbortSignal.timeout(TIMEOUT_MS), ...init });
  } catch (err) {
    const timedOut = err instanceof DOMException && err.name === "TimeoutError";
    throw new ApiError(0, timedOut ? "Request timed out." : "Network error: the API could not be reached.");
  }
  const body = await resp.json().catch(() => null);
  if (!resp.ok) throw new ApiError(resp.status, describe(body, resp));
  return body as T;
}

const json = (method: string, body: unknown): RequestInit => ({
  method,
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(body),
});

const qs = (params: Record<string, string | number | boolean | null | undefined>) => {
  const p = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) if (v !== null && v !== undefined && v !== "") p.set(k, String(v));
  const s = p.toString();
  return s ? `?${s}` : "";
};

export type Readiness = { status: string; database: string; schema_revision?: string };

export const api = {
  readiness: () => request<Readiness>("/health/ready"),
  settings: () => request<SettingsSummary>("/api/settings"),
  loadDemo: () => request<Record<string, { investigation_id: string; title: string }>>("/api/demo", { method: "POST" }),

  upload: (file: File) => {
    const form = new FormData();
    form.append("file", file);
    return request<UploadResponse>("/api/imports/file", { method: "POST", body: form });
  },
  preview: (uploadId: string, sheet?: string | null, encoding?: string) =>
    request<Preview>(`/api/imports/${uploadId}/preview${qs({ sheet, encoding })}`),
  importDataset: (body: { upload_id: string; sheet?: string | null; encoding?: string; label?: string; mapping: ColumnMapping }) =>
    request<{ dataset: Dataset; already_imported: boolean }>("/api/datasets", json("POST", body)),
  datasets: () => request<Dataset[]>("/api/datasets"),
  dataset: (id: string) => request<Dataset>(`/api/datasets/${id}`),
  datasetProfile: (id: string) => request<DatasetProfile>(`/api/datasets/${id}/profile`),
  datasetIssues: (id: string, offset = 0) => request<Page<ImportIssue>>(`/api/datasets/${id}/issues${qs({ offset, limit: 50 })}`),
  freshness: (id: string) => request<Freshness>(`/api/datasets/${id}/freshness`, { method: "POST" }),

  connections: () => request<Connection[]>("/api/connections"),
  testConnection: (name: string) => request<ConnectionTest>(`/api/connections/${name}/test`, { method: "POST" }),
  catalog: (name: string) => request<CatalogTable[]>(`/api/connections/${name}/catalog`),
  queries: () => request<RegisteredQuery[]>("/api/registered-queries"),
  previewQuery: (key: string, params: Record<string, string>) =>
    request<{ columns: string[]; rows: (string | null)[][]; truncated: boolean }>(`/api/registered-queries/${key}/preview`, json("POST", { params })),
  snapshot: (key: string, params: Record<string, string>, label?: string) =>
    request<{ dataset: Dataset; already_imported: boolean }>(`/api/registered-queries/${key}/snapshots`, json("POST", { params, label })),

  metrics: () => request<MetricDefinition[]>("/api/metric-definitions"),
  createMetric: (body: MetricSpec & { name: string; description?: string | null }) =>
    request<MetricDefinition>("/api/metric-definitions", json("POST", body)),
  compareMetrics: (left: string, right: string) => request<DnaComparison>(`/api/metric-definitions/compare${qs({ left, right })}`),

  investigations: (filters: { status?: string; q?: string; finding_type?: string; is_demo?: boolean | null } = {}) =>
    request<InvestigationSummary[]>(`/api/investigations${qs({ ...filters, limit: 200 })}`),
  investigation: (id: string) => request<Investigation>(`/api/investigations/${id}`),
  createInvestigation: (body: { title: string; left: { dataset_id: string; metric_definition_id: string }; right: { dataset_id: string; metric_definition_id: string } }) =>
    request<Investigation>("/api/investigations", json("POST", body)),
  evidence: (id: string, findingId: string, offset = 0, limit = 25) =>
    request<Page<Evidence>>(`/api/investigations/${id}/findings/${findingId}/evidence${qs({ offset, limit })}`),

  simulations: (id: string) => request<Simulation[]>(`/api/investigations/${id}/simulations`),
  simulate: (id: string, rule: Record<string, unknown>) => request<Simulation>(`/api/investigations/${id}/simulations`, json("POST", rule)),

  setStatus: (id: string, body: { status: string; note?: string; reviewer?: string }) =>
    request<{ id: string; status: string }>(`/api/investigations/${id}/status`, json("PATCH", body)),
  notes: (id: string) => request<Note[]>(`/api/investigations/${id}/notes`),
  addNote: (id: string, body: { decision: string; note: string; finding_id?: string | null; reviewer?: string }) =>
    request<Note>(`/api/investigations/${id}/notes`, json("POST", body)),
  history: (id: string) => request<AuditEvent[]>(`/api/investigations/${id}/history`),
  similar: (id: string) => request<{ method: string; matches: SimilarMatch[] }>(`/api/investigations/${id}/similar`),
  patterns: (includeDemo: boolean) => request<Pattern[]>(`/api/memory/patterns${qs({ include_demo: includeDemo })}`),
};

export const links = {
  evidenceCsv: (id: string) => `/api/investigations/${id}/evidence.csv`,
  report: (id: string, format: "json" | "html", download = false) => `/api/investigations/${id}/export${qs({ format, download: download || null })}`,
};
