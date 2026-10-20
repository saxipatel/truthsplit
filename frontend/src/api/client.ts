export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}

const TIMEOUT_MS = 15_000;

export async function getJson<T>(path: string, init?: RequestInit): Promise<T> {
  let resp: Response;
  try {
    resp = await fetch(path, { signal: AbortSignal.timeout(TIMEOUT_MS), ...init });
  } catch (err) {
    const timedOut = err instanceof DOMException && err.name === "TimeoutError";
    throw new ApiError(0, timedOut ? "request timed out" : "network error");
  }
  const body = await resp.json().catch(() => null);
  if (!resp.ok) {
    const detail = body?.detail ?? body?.database ?? `HTTP ${resp.status} ${resp.statusText}`;
    throw new ApiError(resp.status, typeof detail === "string" ? detail : JSON.stringify(detail));
  }
  return body as T;
}

export type Readiness = { status: string; database: string; schema_revision?: string };

export const fetchReadiness = () => getJson<Readiness>("/health/ready");
