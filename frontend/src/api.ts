// Thin client for the backend API. Every request:
// - goes to the same origin (/api), so the HttpOnly session cookie is sent automatically
// - carries X-Requested-With, which the backend requires on state-changing requests (CSRF)

export type Role = "admin" | "user";
export type QcStatus = "pending" | "passed" | "failed";
export type Access = "owner" | "grantee" | "none";

export interface Me {
  id: string;
  username: string;
  role: Role;
}

export interface FileInfo {
  id: string;
  filename: string;
  qc_status: QcStatus;
  qc_updated_at: string | null;
  created_at: string;
  has_content: boolean;
  size_bytes: number | null;
  sha256: string | null;
  content_type: string | null;
}

export interface UploadInfo {
  id: string;
  sample_id: string;
  filename: string;
  size_bytes: number;
  received_bytes: number;
  chunk_bytes: number;
  expires_at: string;
}

export interface Sample {
  id: string;
  owner_id: string;
  owner_username: string | null;
  name: string;
  created_at: string;
  access: Access;
  files: FileInfo[];
}

export interface SampleSummary {
  id: string;
  name: string;
  owner_id: string;
  owner_username: string;
  created_at: string;
  file_count: number;
  access: Access;
}

export interface Grant {
  sample_id: string;
  user_id: string;
  username: string | null;
  granted_by: string;
  granted_at: string;
}

export interface User {
  id: string;
  username: string;
  role: Role;
  is_active: boolean;
  created_at: string;
}

export interface AuditEvent {
  id: number;
  ts: string;
  event: string;
  actor_id: string | null;
  sample_id: string | null;
  file_id: string | null;
  details: Record<string, unknown>;
}

export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
    message: string,
  ) {
    super(message);
  }
}

/** Fired when the session has expired, so the app can send the user back to sign-in. */
export const SESSION_EXPIRED_EVENT = "sfa:session-expired";

async function request<T>(
  method: string,
  path: string,
  body?: unknown,
  signal?: AbortSignal,
): Promise<T> {
  const headers: Record<string, string> = { "X-Requested-With": "fetch" };
  let payload: BodyInit | undefined;
  if (body instanceof Blob) {
    headers["Content-Type"] = "application/octet-stream"; // a raw upload chunk
    payload = body;
  } else if (body !== undefined) {
    headers["Content-Type"] = "application/json";
    payload = JSON.stringify(body);
  }

  const res = await fetch(`/api${path}`, {
    method,
    headers,
    body: payload,
    credentials: "same-origin",
    signal,
  });
  if (res.status === 204) return undefined as T;

  const data = await res.json().catch(() => null);
  if (!res.ok) {
    if (res.status === 401 && !path.startsWith("/auth/")) {
      window.dispatchEvent(new Event(SESSION_EXPIRED_EVENT));
    }
    const err = data?.error; // every API error is {"error": {"code", "message"}}
    throw new ApiError(res.status, err?.code ?? "error", err?.message ?? `Request failed (${res.status})`);
  }
  return data as T;
}

/** Encode an ID before putting it in a path (route params come from the address bar). */
const seg = encodeURIComponent;

export const api = {
  me: () => request<Me>("GET", "/auth/me"),
  login: (username: string, password: string) =>
    request<Me>("POST", "/auth/login", { username, password }),
  logout: () => request<void>("POST", "/auth/logout"),

  listSamples: () => request<SampleSummary[]>("GET", "/samples"),
  createSample: (name: string) => request<Sample>("POST", "/samples", { name }),
  getSample: (id: string) => request<Sample>("GET", `/samples/${seg(id)}`),
  deleteSample: (id: string) => request<void>("DELETE", `/samples/${seg(id)}`),

  startUpload: (sampleId: string, file: File) =>
    request<UploadInfo>("POST", `/samples/${seg(sampleId)}/uploads`, {
      filename: file.name,
      size_bytes: file.size,
      content_type: file.type || null,
    }),
  getUpload: (id: string) => request<UploadInfo>("GET", `/uploads/${seg(id)}`),
  putChunk: (id: string, offset: number, chunk: Blob, signal?: AbortSignal) =>
    request<UploadInfo>("PUT", `/uploads/${seg(id)}?offset=${offset}`, chunk, signal),
  completeUpload: (id: string) => request<FileInfo>("POST", `/uploads/${seg(id)}/complete`),
  abortUpload: (id: string) => request<void>("DELETE", `/uploads/${seg(id)}`),
  deleteFile: (id: string) => request<void>("DELETE", `/files/${seg(id)}`),
  downloadUrl: (id: string) =>
    request<{ url: string; expires_at: string }>("POST", `/files/${seg(id)}/download-url`),

  listGrants: (sampleId: string) => request<Grant[]>("GET", `/samples/${seg(sampleId)}/grants`),
  grant: (sampleId: string, username: string) =>
    request<Grant>("POST", `/samples/${seg(sampleId)}/grants`, { username }),
  revoke: (sampleId: string, userId: string) =>
    request<void>("DELETE", `/samples/${seg(sampleId)}/grants/${seg(userId)}`),

  listUsers: () => request<User[]>("GET", "/admin/users"),
  createUser: (username: string, password: string, role: Role) =>
    request<User>("POST", "/admin/users", { username, password, role }),
  updateUser: (id: string, change: { role?: Role; is_active?: boolean }) =>
    request<User>("PATCH", `/admin/users/${seg(id)}`, change),
  listAllSamples: () => request<SampleSummary[]>("GET", "/admin/samples"),
  listAudit: (event?: string) =>
    request<AuditEvent[]>("GET", `/admin/audit${event ? `?event=${encodeURIComponent(event)}` : ""}`),
};
