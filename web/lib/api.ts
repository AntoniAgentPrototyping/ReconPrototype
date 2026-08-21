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
  /** NULL when the window is known only from uploads or a roster declaration —
   *  nothing has been queued for it yet (D2). */
  job_id: number | null;
  job_state: "queued" | "leased" | "done" | "error" | "cancelled" | null;
  /** Since M6 this comes from the WINDOW's declaration, not from the job — so the
   *  board shows what a person stated about the window, with their reason. */
  partial_roster: boolean;
  roster_reason: string | null;
  roster_declared_by: string | null;
  /** Expected stores with no file. NULL, not 0, when the input came from a
   *  directory rather than uploads: no preview was computed. */
  roster_missing: number | null;
  requested_by: string | null;
  queued_at: string | null;
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
  /** Live (non-rejected) uploads recorded for this window — what there is to
   *  show about a window that has never been queued (D2). */
  upload_count: number;
  /** 'window' for a settlement run, 'month_master' for the month-end summary
   *  (M8 Phase 3). The api already splits these into separate lists, so a board
   *  row is always a window; this is here for the master rows. */
  kind?: "window" | "month_master";
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

/** One filename's answer from `POST /uploads/store-preview` (D7). */
export type StorePreviewFile = {
  filename: string;
  /** The store the pipeline's own rule reads out of the name, or null when it
   *  cannot read one — in which case `problem` says so. */
  store: string | null;
  /** After store aliases. This is what the file will be renamed to. */
  canonical: string | null;
  /** true / false against the window's roster, or **null when nothing checked
   *  it** — Lazada has no roster (register A6), and "unchecked" must not render
   *  as "wrong". */
  on_roster: boolean | null;
  uniform_name: string | null;
  problem: string | null;
};

export type StorePreview = {
  platform: string;
  period: string;
  kind: string;
  /** The correction picklist's options: the roster of the config THIS window
   *  runs under, which for a pinned window is not today's. */
  expected_stores: string[];
  roster_checked: boolean;
  files: StorePreviewFile[];
};

export type RosterDeclaration = {
  platform: string;
  period: string;
  roster_declared_partial: boolean;
  reason: string | null;
  declared_by: string;
  declared_at: string;
  /** WHICH expected stores are declared absent (D3). NULL is the blanket —
   *  every expected store optional — the only state that existed before
   *  migration 021, kept for declarations that predate the store list. */
  declared_absent_stores: string[] | null;
};

/** Format drift for one file kind of one window (register D5). */
export type KindDrift = {
  /** Canonical fields NO file of this kind supplies, so the run will stop. The
   *  arithmetic `ingest.read_parts` does, one step earlier — over the union of
   *  the kind's files, because a part file with fewer columns is legitimate. */
  missing_fields: string[];
  /** filename -> headers the contract does not name, PII excluded. A renamed
   *  column looks exactly like this. */
  unrecognised_headers: Record<string, string[]>;
  /** false means nothing was measured, not that nothing is wrong: Lazada has no
   *  required field set, and files uploaded before migration 023 recorded no
   *  headers. "Clean" and "unchecked" must not render the same. */
  checked: boolean;
};

export type WindowPlan = {
  platform: string;
  period: string;
  files: Record<string, PlannedFile[]>;
  stores_present: string[];
  missing_stores: string[];
  unexpected_stores: string[];
  /** The roster this window runs under, as names — the declaration form's
   *  picklist (D3). Business identifiers, never customer PII. */
  expected_stores: string[];
  expected_store_count: number;
  problems: string[];
  roster_declaration: RosterDeclaration | null;
  /** Declared-absent stores that now HAVE files (D3's re-evaluation nudge).
   *  Their figures are included either way; the declaration no longer
   *  describes the window. */
  declared_absent_present: string[];
  /** Per file kind: what the export's headers say about format drift (D5). */
  drift: Record<string, KindDrift>;
  /** What an unrecognised header may be mapped to — the closed set of names the
   *  pipeline understands, from its own constants. */
  canonical_fields: string[];
  /** What `POST /jobs` will do with this window as it stands. */
  ready: boolean;
};

/**
 * One reference figure the team can supply (A3).
 *
 * Served by the API from `service/references.py` rather than defined here, so a key
 * the pipeline stopped comparing cannot leave a form field quietly collecting a
 * number nothing checks.
 */
export type ReferenceField = {
  key: string;
  label: string;
  help: string;
  /** Served, not translated here — see `service/references.py`. Putting the
   *  Vietnamese in this layer would re-create, one language later, exactly the
   *  drift the served spec exists to prevent. */
  label_vi: string;
  help_vi: string;
};

export type WindowReferences = {
  platform: string;
  period: string;
  refs: { grand?: Record<string, number>; grand_tolerance?: number };
  supplied_by: string;
  supplied_at: string;
  note: string | null;
};

export type WindowDetail = {
  platform: string;
  period: string;
  roster_declaration: RosterDeclaration | null;
  reference_fields: ReferenceField[];
  references: WindowReferences | null;
  references_summary: string;
  references_summary_vi: string;
};

/**
 * Which settled orders a window's own order exports do not cover.
 *
 * Counts only — no money crosses this boundary. `cross_window` is the half worth
 * acting on: an order whose lines sit in an EARLIER window's export is the shape
 * that understated July by 4,527,401,608 VND, while orders missing from every
 * window are the documented reconciling class and expected to have traffic.
 *
 * `indexed: false` means the question could not be answered for this window, which
 * must never be shown as "everything is covered".
 */
export type OrderCoverage = {
  platform: string;
  period: string;
  stores: { store: string; income_orders: number; unmatched_orders: number }[];
  cross_window: {
    store: string;
    holder_period: string;
    filename: string;
    upload_id: number;
    orders: number;
  }[];
  indexed: boolean;
  unindexed_files: string[];
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
  /** What this run queued next (A4): the month-master chain's outcome sentence,
   *  including a failure to queue. NULL = nothing chained. */
  chained: string | null;
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
  /** The standing decision on this fingerprint (D1). Annotates, never hides:
   *  a dispositioned row still appears on every run, badged. */
  disposition: "reviewed" | "expected" | null;
  disposition_reason: string | null;
  disposition_by: string | null;
  decided_at: string | null;
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
  /** The operations that were requested. Absent on an M5 proposal, which recorded
   *  only the resulting file — which is why one of those cannot be replayed. */
  edits?: ConfigRowEdit[] | null;
  /** Which editor produced `edits`. "path" is the pre-M8 dotted-path editor and its
   *  proposals can neither be applied nor replayed against the config tables. */
  edit_model?: "path" | "row" | null;
  rebased_from?: number | null;
  /** Computed by the database: the approver was the author. Permitted, recorded. */
  self_approved?: boolean;
};

/** One row operation. `table`, `key` and column names are WIRE FORMAT ONLY — no
 *  user ever sees one, which `test_a_wire_name_is_never_part_of_the_rendered_payload`
 *  holds on the server side. */
export type ConfigRowEdit = {
  table: string;
  op: "upsert" | "delete";
  key: Record<string, unknown>;
  values?: Record<string, unknown>;
  /** The reason, stored in the row's own `evidence` column. Required on a new row:
   *  it is what makes the entry defensible in six months, and unlike a comment it
   *  cannot end up captioning the entry below it. */
  evidence?: string;
};

/** One column of a config table: what it holds and which control draws it. */
export type ConfigColumn = {
  name: string;
  kind:
    | "text"
    | "bool"
    | "int"
    | "money_vnd"
    | "number"
    | "date"
    | "enum"
    | "json";
  label: string;
  help: string;
  nullable: boolean;
  options: { value: string; label: string }[];
  default: unknown;
};

/** One row, as served: its key, its values, and its own justification. */
export type ConfigRow = {
  key: Record<string, unknown>;
  values: Record<string, unknown>;
  /** The reason this row exists, from its own `evidence` column — not a comment
   *  block lifted off the container, which could only ever caption the group. */
  evidence: string;
  changed_by: string | null;
  changed_at: string | null;
  source: string | null;
  invalidates_goldens: boolean;
  /** Set only on the single-settings table, whose rows carry their own labels. */
  label: string | null;
  help: string | null;
  reader: string | null;
  locked: boolean;
  locked_reason: string;
};

export type ConfigTable = {
  table: string;
  title: string;
  blurb: string;
  key: ConfigColumn[];
  columns: ConfigColumn[];
  /** A key column to split the rows on, so a roster reads as three lists rather
   *  than one 42-row table with a platform column nobody scans. */
  grouped_by: string | null;
  may_insert: boolean;
  may_delete: boolean;
  /** Quoted verbatim when an insert or delete is refused. A table that is closed
   *  and cannot say why is a table nobody can argue with. */
  closed_reason: string;
  invalidates_goldens: boolean;
  require_evidence: boolean;
  min_evidence: number;
  rows: ConfigRow[];
};

/**
 * Whether this deployment can check a goldens-affecting edit AT ALL, answered
 * before anyone makes one (A2). In every container this system currently builds
 * the answer is no: the canary needs `tests/goldens/manifest.json` and no image
 * ships `tests/`. The editor used to present the gate as working right up to the
 * moment it silently could not run.
 */
export type VerificationCapability = {
  can_verify: boolean;
  reason: "ready" | "no_digests" | "no_inputs";
  detail: string;
  window?: string;
  strong?: boolean;
};

export type ConfigTables = {
  tables: ConfigTable[];
  sha256: string;
  operations: string[];
  verification: VerificationCapability;
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
