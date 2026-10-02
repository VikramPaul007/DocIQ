/** Single fetch wrapper for the whole app: same-origin "/api" (Caddy proxies
 * it, so no CORS), credentials included for the session cookie, and the CSRF
 * header attached to every mutating request. Hand-written response types for
 * now (see src/lib/types.ts) — `npm run gen:api-types` will replace them with
 * generated ones once the backend's OpenAPI schema is stable enough to pin to.
 */

export class ApiError extends Error {
  status: number;
  requestId?: string;

  constructor(message: string, status: number, requestId?: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.requestId = requestId;
  }
}

let csrfToken: string | null = null;

export function setCsrfToken(token: string | null): void {
  csrfToken = token;
}

function extractMessage(body: unknown): string {
  if (body && typeof body === "object" && "detail" in body) {
    const detail = (body as { detail: unknown }).detail;
    if (typeof detail === "string") return detail;
    return JSON.stringify(detail);
  }
  if (body && typeof body === "object" && "error" in body) {
    return String((body as { error: unknown }).error);
  }
  return "Request failed";
}

export async function apiFetch<T>(path: string, init: RequestInit = {}): Promise<T> {
  const method = (init.method ?? "GET").toUpperCase();
  const headers = new Headers(init.headers);
  if (init.body !== undefined && !(init.body instanceof FormData)) {
    headers.set("Content-Type", "application/json");
  }
  if (method !== "GET" && method !== "HEAD" && csrfToken) {
    headers.set("X-CSRF-Token", csrfToken);
  }

  const res = await fetch(`/api${path}`, { ...init, method, headers, credentials: "include" });

  if (res.status === 204) {
    return undefined as T;
  }

  const contentType = res.headers.get("content-type") ?? "";
  const body = contentType.includes("application/json") ? await res.json() : undefined;

  if (!res.ok) {
    const requestId = res.headers.get("X-Request-ID") ?? undefined;
    throw new ApiError(extractMessage(body), res.status, requestId);
  }

  return body as T;
}

export const api = {
  get: <T>(path: string) => apiFetch<T>(path),
  post: <T>(path: string, json?: unknown) =>
    apiFetch<T>(path, { method: "POST", body: json !== undefined ? JSON.stringify(json) : undefined }),
  put: <T>(path: string, json?: unknown) =>
    apiFetch<T>(path, { method: "PUT", body: json !== undefined ? JSON.stringify(json) : undefined }),
  patch: <T>(path: string, json?: unknown) =>
    apiFetch<T>(path, { method: "PATCH", body: json !== undefined ? JSON.stringify(json) : undefined }),
  delete: <T>(path: string) => apiFetch<T>(path, { method: "DELETE" }),
};
