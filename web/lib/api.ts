/**
 * The BFF's server-side API client.
 *
 * **The browser never holds the session token.** It lives in an httpOnly cookie
 * that JavaScript cannot read, and every call to the reconciliation API is made
 * from the Next.js server with the token attached here. The alternative —
 * shipping the token to the browser and calling the API directly — puts a
 * credential that can queue settlement runs and read client revenue into a place
 * any XSS can lift it from.
 *
 * The API reads only the `Authorization` header and never a cookie, which is why
 * CSRF against the API is structurally absent rather than mitigated. CSRF does
 * exist here at the BFF, where a server action is a browser POST with the cookie
 * attached — see next.config.mjs.
 *
 * It is also why the API needs no public address in a deployment: only this
 * server talks to it, over the private network.
 *
 * Everything in this file is server-only. `import "server-only"` makes a stray
 * client-side import a build error rather than a leaked token.
 */
import "server-only";
import { cookies } from "next/headers";
import { cache } from "react";

// Renamed from `recon_token` in M6. A constant named for the deleted thing is how
// the next person reintroduces token paste. Cost: every browser signed in before
// the rename reads as signed out once, and is redirected to /login.
export const SESSION_COOKIE = "recon_session";

/**
 * Read at REQUEST time, never baked in at build time — see next.config.mjs.
 *
 * Exported since M6: the artifact download route had its own inline copy of this,
 * so a change here silently missed it.
 */
export function apiBase(): string {
  return process.env.RECON_API_URL ?? "http://127.0.0.1:8080";
}

export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly detail: string,
  ) {
    super(`${status}: ${detail}`);
  }
}

/** The raw session token. Exported because the download route streams bytes and
 * cannot go through `api()`. */
export async function currentSession(): Promise<string | undefined> {
  const jar = await cookies();
  return jar.get(SESSION_COOKIE)?.value;
}

type Options = {
  method?: string;
  body?: unknown;
  token?: string;
  /** Run views poll; nothing here should ever be served from a cache. */
  cache?: RequestCache;
};

export async function api<T>(path: string, options: Options = {}): Promise<T> {
  const token = options.token ?? (await currentSession());
  const headers: Record<string, string> = { accept: "application/json" };
  if (token) headers.authorization = `Bearer ${token}`;
  if (options.body !== undefined) headers["content-type"] = "application/json";

  const response = await fetch(`${apiBase()}${path}`, {
    method: options.method ?? "GET",
    headers,
    body: options.body === undefined ? undefined : JSON.stringify(options.body),
    cache: options.cache ?? "no-store",
  });

  if (!response.ok) {
    let detail = response.statusText;
    try {
      const parsed = (await response.json()) as { detail?: string };
      if (parsed?.detail) detail = parsed.detail;
    } catch {
      /* a non-JSON error body is still an error; keep the status text */
    }
    throw new ApiError(response.status, detail);
  }
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

/**
 * Forward a multipart upload, streaming rather than buffering.
 *
 * Separate from `api()` because that one JSON-encodes its body, and a settlement
 * export is 5-40 MB of xlsx. `FormData` is passed to `fetch` untouched so undici
 * sets its own boundary and streams the file through — reading it into a string
 * first would put the whole export in the Node heap on its way to a server that is
 * about to read it again.
 */
export async function apiUpload<T>(path: string, form: FormData): Promise<T> {
  const token = await currentSession();
  const headers: Record<string, string> = { accept: "application/json" };
  if (token) headers.authorization = `Bearer ${token}`;
  // No content-type: fetch must set it, because only it knows the boundary.

  const response = await fetch(`${apiBase()}${path}`, {
    method: "POST",
    headers,
    body: form,
    cache: "no-store",
    // Required by undici for a streaming body; harmless otherwise.
    duplex: "half",
  } as RequestInit & { duplex: "half" });

  if (!response.ok) {
    let detail = response.statusText;
    try {
      const parsed = (await response.json()) as { detail?: string };
      if (parsed?.detail) detail = parsed.detail;
    } catch {
      /* a non-JSON error body is still an error */
    }
    throw new ApiError(response.status, detail);
  }
  return (await response.json()) as T;
}

/**
 * The signed-in principal, or null.
 *
 * Wrapped in React `cache()` so the layout and the page it renders share one
 * `/me` round trip instead of making two (three once the admin nav link is
 * considered). Pre-existing waste; M6 made it worse by adding a nav item.
 */
export const whoami = cache(async function whoami(): Promise<Principal | null> {
  const token = await currentSession();
  if (!token) return null;
  try {
    return await api<Principal>("/me", { token });
  } catch (error) {
    // A revoked token must read as "signed out", not as a crash. Revocation
    // takes effect on the next request by design, and this is that request.
    if (error instanceof ApiError && error.status === 401) return null;
    throw error;
  }
});

// ---------------------------------------------------------------------------
// Shapes returned by service/api.py. Hand-written rather than generated: the
// API is small and a generator would be another toolchain for a single
// maintainer to keep alive.
// ---------------------------------------------------------------------------

export type Role = "recon.viewer" | "recon.user" | "recon.admin";

export type Principal = {
  subject: string;
  role: Role;
  method: string;
  must_change_password: boolean;
  display_name: string | null;
};

export type SessionCreated = {
  token: string;
  expires_at: string;
  subject: string;
  role: Role;
  must_change_password: boolean;
  display_name: string | null;
};

export type User = {
  id: number;
  username: string;
  role: Role;
  display_name: string | null;
  must_change_password: boolean;
  created_at: string | null;
  created_by: string | null;
  last_login_at: string | null;
  disabled_at: string | null;
  disabled_by: string | null;
};

export type SessionInfo = {
  id: number;
  created_at: string | null;
  last_seen_at: string | null;
  absolute_expires_at: string | null;
  revoked_at: string | null;
  revoked_reason: string | null;
  user_agent: string | null;
  client_ip: string | null;
};

export type BoardRow = {
  platform: string;
  period: string;
  job_id: number;
  job_state: "queued" | "leased" | "done" | "error" | "cancelled";
  /** Since M6 this comes from the WINDOW's declaration, not from the job — so the
   *  board shows what a person stated about the window, with their reason. */
  partial_roster: boolean;
  roster_reason: string | null;
  roster_declared_by: string | null;
  /** Expected stores with no file. NULL, not 0, when the input came from a
   *  directory rather than uploads: no preview was computed. */
  roster_missing: number | null;
  requested_by: string | null;
  queued_at: string;
  run_id: number | null;
  status: "ok" | "variance" | "unverified" | "hard_stop" | null;
  exit_code: number | null;
  started_at: string | null;
  finished_at: string | null;
  wall_s: number | null;
  peak_rss_mb: number | null;
  config_was_pinned: boolean;
  finding_count: number | null;
  job_count: number;
};

// ---------------------------------------------------------------------------
// Uploads and windows (M6)
// ---------------------------------------------------------------------------

export type Upload = {
  id: number;
  filename: string;
  sha256: string;
  bytes: number;
  platform: string | null;
  period: string | null;
  kind: string | null;
  store: string | null;
  store_canonical: string | null;
  object_key: string | null;
  state: "stored" | "consumed" | "rejected" | "received" | "staged";
  reason: string | null;
  sanitized: boolean;
  pii_columns_dropped: string[];
  uploaded_by: string;
  created_at: string;
  consumed_by_run_id: number | null;
};

export type UploadCreated = Upload & {
  sheet: string;
  rows: number;
  kept_columns: string[];
  dropped_known_pii: string[];
  sheets_read: number;
  store_derived_from_filename: string;
  store_corrected: boolean;
  /** The name with the ordinal still undecided — rendered greyed, because the
   *  ordinal is a property of the whole window and is assigned per run. */
  uniform_name_preview: string;
};

export type PlannedFile = {
  filename: string;
  upload_id: number;
  store: string | null;
  uniform_name: string | null;
  ordinal?: number;
  renamed?: boolean;
  uploaded_by?: string;
  bytes?: number;
  state?: string;
};

export type RosterDeclaration = {
  platform: string;
  period: string;
  roster_declared_partial: boolean;
  reason: string | null;
  declared_by: string;
  declared_at: string;
};

export type WindowPlan = {
  platform: string;
  period: string;
  files: Record<string, PlannedFile[]>;
  stores_present: string[];
  missing_stores: string[];
  unexpected_stores: string[];
  expected_store_count: number;
  problems: string[];
  roster_declaration: RosterDeclaration | null;
  /** What `POST /jobs` will do with this window as it stands. */
  ready: boolean;
};

/** The file kinds each platform has. Mirrors service/naming.KINDS_BY_PLATFORM;
 *  the API returns the pair-checked error if this ever drifts. */
export const KINDS_BY_PLATFORM: Record<string, string[]> = {
  tiktok: ["orders", "income"],
  shopee: ["orders", "income"],
  lazada: ["weekly", "daily"],
};

export type Run = {
  id: number;
  job_id: number;
  platform: string;
  period: string;
  status: BoardRow["status"];
  exit_code: number | null;
  in_flight: boolean;
  findings: [string, string][];
  variances: string[];
  unverified: string[];
  wall_s: number | null;
  io_s: number | null;
  compute_s: number | null;
  serialize_s: number | null;
  peak_rss_mb: number | null;
  error: string | null;
  started_at: string | null;
  finished_at: string | null;
  config_version_id: number | null;
  config_was_pinned: boolean;
  artifacts: Artifact[];
  exception_sheets?: ExceptionSheet[];
};

export type Artifact = {
  name: string;
  uri: string;
  bytes: number;
  bytes_sha256: string;
};

export type ExceptionSheet = {
  sheet: string;
  total_rows: number;
  stored_rows: number;
  truncated: boolean;
};

export type ExceptionRow = {
  id: number;
  sheet: string;
  fingerprint: string;
  payload: Record<string, unknown>;
};

export type LogLine = {
  seq: number;
  kind: "line" | "warning" | "section";
  text: string;
};

export type LogPage = {
  run_id: number;
  lines: LogLine[];
  next_seq: number;
  complete: boolean;
};

export type Proposal = {
  id: number;
  base_sha256: string;
  summary: string;
  diff?: string;
  content?: string;
  state: "pending" | "approved" | "rejected" | "applied" | "withdrawn";
  proposed_by: string;
  proposed_at: string;
  decided_by: string | null;
  decided_at: string | null;
  decision_note: string | null;
  applied_version_id: number | null;
  /** The operations that were requested, since M6. Absent on an M5 proposal, which
   *  recorded only the resulting file — which is why one of those cannot be
   *  replayed. */
  edits?: { op: string; path: string[]; value?: unknown; key?: string }[] | null;
  rebased_from?: number | null;
  /** Computed by the database: the approver was the author. Permitted, recorded. */
  self_approved?: boolean;
};

/** One editable setting, with the comment block that justifies its current value. */
export type ConfigField = {
  path: string[];
  /** Wire format only. NEVER rendered — see service/config_schema.py. */
  dotted: string;
  label: string;
  widget:
    | "money_vnd"
    | "number"
    | "enum"
    | "bool"
    | "text"
    | "string_list"
    | "store_roster"
    | "alias_map"
    | "column_map"
    | "date_bounds"
    | "pattern"
    | "locked"
    | "dead";
  reader: string;
  help: string;
  invalidates_goldens: boolean;
  options: { value: string; label: string }[];
  on_means: string;
  off_means: string;
  locked_reason: string;
  editable: boolean;
  allows_new_keys: boolean;
  value: unknown;
  /** The comment block from settings.yaml itself, verbatim. */
  evidence: string[];
};

export type ConfigSection = {
  key: string;
  title: string;
  blurb: string;
  per_platform: boolean;
  fields: ConfigField[];
};

export type ConfigSchema = {
  sections: ConfigSection[];
  sha256: string;
  canonical_fields: string[];
  operations: string[];
};

export type ConfigVersion = {
  id: number;
  sha256: string;
  source: string;
  git_commit: string | null;
  created_at: string;
  created_by: string | null;
  verification_state:
    | "verified"
    | "cells_moved"
    | "unavailable"
    | "failed"
    | "not_applicable"
    | null;
  verified_window: string | null;
  verified_window_is_real: boolean | null;
  cells_moved: number | null;
  verification: { message?: string } | null;
};

export type ConfigPin = {
  platform: string;
  period: string;
  config_version_id: number;
  sha256: string;
  pinned_at: string;
  pinned_by: string | null;
  reason: string | null;
};
