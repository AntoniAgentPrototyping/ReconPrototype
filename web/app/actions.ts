"use server";

/**
 * Server actions — every mutation the UI can perform.
 *
 * Each one runs on the server, attaches the httpOnly token, and lets the API do
 * the authorization. The UI hides buttons a viewer cannot use; that is a
 * courtesy, **not** a control. The control is `service/auth.py`, and a viewer
 * who forges a request gets a 403 from the API rather than from here.
 */
import { revalidatePath } from "next/cache";
import { cookies } from "next/headers";

import { LANG_COOKIE } from "@/lib/lang";
import { redirect } from "next/navigation";

import {
  ApiError,
  SESSION_COOKIE,
  api,
  apiUpload,
  type SessionCreated,
  type UploadCreated,
} from "@/lib/api";

export type ActionResult = { ok: boolean; message: string };

function describe(error: unknown): string {
  if (error instanceof ApiError) return error.detail;
  return error instanceof Error ? error.message : String(error);
}

// ---------------------------------------------------------------------------
// Session
// ---------------------------------------------------------------------------

export async function signIn(_prev: ActionResult | null, form: FormData): Promise<ActionResult> {
  const username = String(form.get("username") ?? "").trim();
  const password = String(form.get("password") ?? "");
  if (!username || !password) {
    return { ok: false, message: "Enter your username and password." };
  }

  let session: SessionCreated;
  try {
    session = await api<SessionCreated>("/sessions", {
      method: "POST",
      body: { username, password },
      // No token: this IS the authentication.
      token: "",
    });
  } catch (error) {
    // The API's failure message is already uniform across unknown-user,
    // wrong-password and disabled-account. Pass it through rather than adding a
    // second vocabulary here that might accidentally distinguish the cases.
    return { ok: false, message: describe(error) };
  }

  const jar = await cookies();
  jar.set(SESSION_COOKIE, session.token, {
    // httpOnly is the whole point: script cannot read this, so an XSS cannot
    // steal a credential that queues settlement runs.
    httpOnly: true,
    sameSite: "lax",
    // Off on localhost, on everywhere else — a Secure cookie is simply not sent
    // over http and sign-in would silently never work in local development.
    secure: process.env.NODE_ENV === "production",
    path: "/",
    // Derived from the API's own expiry rather than a second hardcoded 12 hours:
    // two independently-maintained lifetimes drift, and the drift shows up as a
    // cookie that outlives the session it names.
    maxAge: Math.max(1, Math.floor((Date.parse(session.expires_at) - Date.now()) / 1000)),
  });
  // redirect() throws, so it stays outside the try.
  redirect(session.must_change_password ? "/account/password" : "/");
}

export async function signOut(): Promise<void> {
  // Revoke SERVER-SIDE, not just here. Dropping the cookie alone leaves a valid
  // session alive for the rest of the absolute window — a sign-out button that
  // does not sign you out. The bug looks fine in code and fine in a manual test,
  // because you do land on /login.
  try {
    await api("/sessions/current", { method: "DELETE" });
  } catch {
    // An already-invalid session must still clear the cookie.
  }
  const jar = await cookies();
  jar.delete(SESSION_COOKIE);
  redirect("/login");
}

export async function changeOwnPassword(
  _prev: ActionResult | null,
  form: FormData,
): Promise<ActionResult> {
  const current_password = String(form.get("current_password") ?? "");
  const new_password = String(form.get("new_password") ?? "");
  const confirm = String(form.get("confirm_password") ?? "");
  if (new_password !== confirm) {
    return { ok: false, message: "The two new passwords do not match." };
  }
  try {
    const result = await api<{ other_sessions_signed_out: number }>("/me/password", {
      method: "POST",
      body: { current_password, new_password },
    });
    const others = result.other_sessions_signed_out;
    return {
      ok: true,
      message:
        others > 0
          ? `Password changed. ${others} other session(s) signed out.`
          : "Password changed.",
    };
  } catch (error) {
    return { ok: false, message: describe(error) };
  }
}

// ---------------------------------------------------------------------------
// Accounts (admin)
//
// Each returns the generated password in its message, which necessarily puts it
// in a server-action response and therefore in the browser's DOM. Unavoidable if
// an admin is to hand it over — but never in a URL or a redirect, where it would
// land in history and in any proxy log.
// ---------------------------------------------------------------------------

export async function createUser(
  _prev: ActionResult | null,
  form: FormData,
): Promise<ActionResult> {
  const username = String(form.get("username") ?? "").trim();
  const role = String(form.get("role") ?? "recon.user");
  const display_name = String(form.get("display_name") ?? "").trim() || null;
  try {
    const created = await api<{ username: string; password: string }>("/users", {
      method: "POST",
      body: { username, role, display_name },
    });
    revalidatePath("/admin/users");
    return {
      ok: true,
      message: `${created.username} created. Password (shown once): ${created.password}`,
    };
  } catch (error) {
    return { ok: false, message: describe(error) };
  }
}

export async function resetUserPassword(userId: number): Promise<ActionResult> {
  try {
    const result = await api<{ username: string; password: string }>(
      `/users/${userId}/password`,
      { method: "POST" },
    );
    revalidatePath("/admin/users");
    return {
      ok: true,
      message: `New password for ${result.username} (shown once): ${result.password}`,
    };
  } catch (error) {
    return { ok: false, message: describe(error) };
  }
}

export async function setUserRole(userId: number, role: string): Promise<ActionResult> {
  try {
    await api(`/users/${userId}/role`, { method: "POST", body: { role } });
    revalidatePath("/admin/users");
    return { ok: true, message: `Role changed to ${role}.` };
  } catch (error) {
    return { ok: false, message: describe(error) };
  }
}

export async function setUserDisabled(
  userId: number,
  disabled: boolean,
): Promise<ActionResult> {
  try {
    await api(`/users/${userId}/${disabled ? "disable" : "enable"}`, { method: "POST" });
    revalidatePath("/admin/users");
    return { ok: true, message: disabled ? "Account disabled." : "Account enabled." };
  } catch (error) {
    return { ok: false, message: describe(error) };
  }
}

export async function revokeUserSessions(userId: number): Promise<ActionResult> {
  try {
    const result = await api<{ sessions_signed_out: number }>(`/users/${userId}/sessions`, {
      method: "DELETE",
    });
    revalidatePath("/admin/users");
    return {
      ok: true,
      message: `${result.sessions_signed_out} session(s) signed out.`,
    };
  } catch (error) {
    return { ok: false, message: describe(error) };
  }
}

// ---------------------------------------------------------------------------
// Runs
// ---------------------------------------------------------------------------

export async function queueRun(_prev: ActionResult | null, form: FormData): Promise<ActionResult> {
  const platform = String(form.get("platform") ?? "");
  const period = String(form.get("period") ?? "").trim();

  // No `partial_roster`. It was a per-run checkbox that relaxed the store-count
  // hard stop, ticked by whoever was in a hurry, with no reason recorded and
  // invisible to whoever reviewed the numbers. The hard stop is unchanged; the
  // override is now a per-window declaration with a mandatory reason
  // (`declareRoster` below).
  try {
    await api("/jobs", { method: "POST", body: { platform, period } });
  } catch (error) {
    // 409 is the double-run guard, not a failure — one window may have only one
    // live job, because two concurrent runs of a settlement window is the
    // double-invoicing shape.
    return { ok: false, message: describe(error) };
  }
  revalidatePath("/");
  return { ok: true, message: `Queued ${platform} ${period}.` };
}

export async function queueMonthMaster(
  _prev: ActionResult | null,
  form: FormData,
): Promise<ActionResult> {
  const month = String(form.get("month") ?? "").trim();
  // A4: rebuild the month summary without re-running any window. The automatic
  // chain covers the normal case; this covers a late reference total or a
  // corrected window. A 409 means one is already waiting, which is fine — it
  // will read every window finished by the time it runs.
  try {
    await api(`/months/${encodeURIComponent(month)}/master`, { method: "POST" });
  } catch (error) {
    return { ok: false, message: describe(error) };
  }
  revalidatePath("/");
  return { ok: true, message: `Queued the month summary for ${month}.` };
}

// ---------------------------------------------------------------------------
// Exception dispositions (D1)
// ---------------------------------------------------------------------------

export async function setExceptionDisposition(
  _prev: ActionResult | null,
  form: FormData,
): Promise<ActionResult> {
  const fingerprint = String(form.get("fingerprint") ?? "");
  const disposition = String(form.get("disposition") ?? "");
  const reason = String(form.get("reason") ?? "").trim();
  const runId = String(form.get("run_id") ?? "");
  // The decision annotates the fingerprint across runs; it never hides the row.
  // The actor is the session — the API refuses one supplied in the body.
  try {
    await api(`/exceptions/${encodeURIComponent(fingerprint)}/disposition`, {
      method: "POST",
      body: { disposition, reason },
    });
  } catch (error) {
    return { ok: false, message: describe(error) };
  }
  revalidatePath(`/runs/${runId}`);
  return { ok: true, message: "" };
}

export async function clearExceptionDisposition(
  _prev: ActionResult | null,
  form: FormData,
): Promise<ActionResult> {
  const fingerprint = String(form.get("fingerprint") ?? "");
  const reason = String(form.get("reason") ?? "").trim();
  const runId = String(form.get("run_id") ?? "");
  try {
    await api(`/exceptions/${encodeURIComponent(fingerprint)}/disposition`, {
      method: "DELETE",
      body: { reason },
    });
  } catch (error) {
    return { ok: false, message: describe(error) };
  }
  revalidatePath(`/runs/${runId}`);
  return { ok: true, message: "" };
}

// ---------------------------------------------------------------------------
// Uploads and the window roster declaration (M6)
// ---------------------------------------------------------------------------

export async function uploadExport(
  _prev: ActionResult | null,
  form: FormData,
): Promise<ActionResult> {
  const platform = String(form.get("platform") ?? "");
  const period = String(form.get("period") ?? "").trim();
  const kind = String(form.get("kind") ?? "");
  const files = form.getAll("file").filter((f): f is File => f instanceof File && f.size > 0);

  if (files.length === 0) return { ok: false, message: "Choose at least one export." };

  const ok: string[] = [];
  const failed: string[] = [];
  for (const file of files) {
    const one = new FormData();
    one.set("file", file);
    one.set("platform", platform);
    one.set("period", period);
    one.set("kind", kind);
    // The store the operator confirmed, if they corrected it. Sent per file, so a
    // batch where one name is wrong does not need re-picking the rest.
    const store = String(form.get(`store:${file.name}`) ?? "").trim();
    if (store) one.set("store", store);

    try {
      const created = await apiUpload<UploadCreated>("/uploads", one);
      ok.push(`${created.filename} → ${created.uniform_name_preview}`);
    } catch (error) {
      // One file at a time, and a failure does not abandon the rest: at month end
      // an operator uploading twelve exports should not lose eleven because the
      // fourth was the wrong kind.
      failed.push(`${file.name}: ${describe(error)}`);
    }
  }

  revalidatePath(`/windows/${platform}/${period}`);
  revalidatePath("/");
  if (failed.length === 0) {
    return { ok: true, message: `Uploaded ${ok.length} file(s). ${ok.join("; ")}` };
  }
  return {
    ok: false,
    message:
      `${ok.length} uploaded, ${failed.length} refused. ` +
      failed.join(" · ") +
      (ok.length ? ` (accepted: ${ok.join("; ")})` : ""),
  };
}

export async function rejectUpload(
  uploadId: number,
  platform: string,
  period: string,
  reason: string,
): Promise<ActionResult> {
  try {
    await api(`/uploads/${uploadId}/reject`, { method: "POST", body: { reason } });
  } catch (error) {
    return { ok: false, message: describe(error) };
  }
  revalidatePath(`/windows/${platform}/${period}`);
  return { ok: true, message: "Removed from the window. The record of it stays." };
}

export async function declareRoster(
  _prev: ActionResult | null,
  form: FormData,
): Promise<ActionResult> {
  const platform = String(form.get("platform") ?? "");
  const period = String(form.get("period") ?? "").trim();
  const partial = form.get("partial") === "on";
  const reason = String(form.get("reason") ?? "").trim();
  // D3: which stores the declaration covers. An empty selection is sent as
  // null — the blanket — because "partial, but I cannot name the stores yet"
  // is a legitimate early-month state; the run log names it as a blanket.
  const stores = form.getAll("stores").map(String).filter(Boolean);

  if (partial && reason.length < 8) {
    return {
      ok: false,
      message:
        "Say why this window is incomplete. The reason is the whole difference " +
        "between this and a checkbox — somebody reviewing these numbers later " +
        "reads it.",
    };
  }
  try {
    await api("/windows/roster", {
      method: "POST",
      body: {
        platform,
        period,
        partial,
        reason: reason || null,
        stores: partial && stores.length > 0 ? stores : null,
      },
    });
  } catch (error) {
    return { ok: false, message: describe(error) };
  }
  revalidatePath(`/windows/${platform}/${period}`);
  revalidatePath("/");
  return {
    ok: true,
    message: partial
      ? "Declared partial. The run will proceed and the board shows the caveat."
      : "Declared complete. An incomplete window will hard-stop.",
  };
}

export async function clearRosterDeclaration(
  platform: string,
  period: string,
): Promise<ActionResult> {
  try {
    await api(`/windows/${platform}/${period}/roster`, { method: "DELETE" });
  } catch (error) {
    return { ok: false, message: describe(error) };
  }
  revalidatePath(`/windows/${platform}/${period}`);
  revalidatePath("/");
  return { ok: true, message: "Declaration withdrawn. An incomplete window hard-stops again." };
}

/**
 * Record the team's own totals for a window (A3).
 *
 * A run with no reference figures exits UNVERIFIED — it completed and nothing
 * corroborated it. Every browser-driven run has been in that state since M6,
 * because the API accepted reference totals on a job and no screen ever sent any.
 *
 * Blank fields are dropped rather than sent as zero. A zero would compare the
 * window against 0 VND and report the whole thing as a variance; a blank means the
 * team did not give us that figure, and the check skips it.
 */
export async function setReferences(
  _prev: ActionResult | null,
  form: FormData,
): Promise<ActionResult> {
  const platform = String(form.get("platform") ?? "");
  const period = String(form.get("period") ?? "").trim();
  const note = String(form.get("note") ?? "").trim();

  const values: Record<string, string> = {};
  for (const [name, raw] of form.entries()) {
    if (!name.startsWith("ref.")) continue;
    const text = String(raw).trim();
    if (text) values[name.slice(4)] = text;
  }
  try {
    await api(`/windows/${platform}/${period}/references`, {
      method: "PUT",
      body: { values, note: note || null },
    });
  } catch (error) {
    return { ok: false, message: describe(error) };
  }
  revalidatePath(`/windows/${platform}/${period}`);
  revalidatePath("/");
  const n = Object.keys(values).length;
  return {
    ok: true,
    message:
      n === 0
        ? "Saved with no figures. Runs of this window will report UNVERIFIED."
        : `Saved ${n} reference figure${n === 1 ? "" : "s"}. The next run of this window will be checked against them.`,
  };
}

export async function clearReferences(
  platform: string,
  period: string,
): Promise<ActionResult> {
  try {
    await api(`/windows/${platform}/${period}/references`, { method: "DELETE" });
  } catch (error) {
    return { ok: false, message: describe(error) };
  }
  revalidatePath(`/windows/${platform}/${period}`);
  revalidatePath("/");
  return {
    ok: true,
    message: "Reference totals withdrawn. Runs of this window report UNVERIFIED again.",
  };
}

/**
 * Switch language (**B7**).
 *
 * A cookie rather than a URL segment or a user column: it takes effect on the next
 * render with no route rewriting, it survives sign-out — a person who cannot read
 * the login page needs the toggle to work *before* they authenticate — and it is
 * not a preference worth a database migration.
 *
 * Not `httpOnly`, unlike the session cookie: nothing here is a credential, and a
 * client component reading the current language is a reasonable thing to want later.
 */
export async function setLanguage(lang: "en" | "vi"): Promise<void> {
  const jar = await cookies();
  jar.set(LANG_COOKIE, lang, {
    sameSite: "lax",
    secure: process.env.NODE_ENV === "production",
    path: "/",
    maxAge: 60 * 60 * 24 * 365,
  });
  revalidatePath("/", "layout");
}

export async function cancelJob(jobId: number): Promise<void> {
  await api(`/jobs/${jobId}/cancel`, { method: "POST" });
  revalidatePath("/");
}

/**
 * Cancel from the run page (**B4**) — same endpoint as the board's, reported back
 * rather than silent, and revalidating the run as well as the board so the badge
 * on the page you are looking at is the one that changes.
 */
export async function cancelRun(jobId: number, runId: number): Promise<ActionResult> {
  try {
    await api(`/jobs/${jobId}/cancel`, { method: "POST" });
  } catch (error) {
    return { ok: false, message: describe(error) };
  }
  revalidatePath(`/runs/${runId}`);
  revalidatePath("/");
  return { ok: true, message: "Cancelled. No finance file was written." };
}

/**
 * Queue the same window again (**B4**).
 *
 * Goes through `POST /jobs` exactly as the board's queue form does — no separate
 * "re-run" concept, because a second run of a window IS just a run of that window,
 * and inventing a distinct path would give it different guards. The 409 that comes
 * back when a live job already exists is the double-run control ([D30]) and is
 * reported as-is rather than retried.
 */
export async function requeueRun(platform: string, period: string): Promise<ActionResult> {
  try {
    await api("/jobs", { method: "POST", body: { platform, period } });
  } catch (error) {
    return { ok: false, message: describe(error) };
  }
  revalidatePath("/");
  return {
    ok: true,
    message: `Queued ${platform} ${period} again. It appears on the board as a new run.`,
  };
}

/**
 * Close out jobs whose worker died (**C1**). Admin only, enforced by the API.
 *
 * Reports what it did rather than succeeding silently: "nothing to reclaim" and
 * "closed out three jobs" are completely different answers to "is something
 * stuck", and a button that looks the same either way teaches nothing.
 */
export async function reclaimJobs(): Promise<ActionResult> {
  try {
    const result = await api<{ message: string }>("/jobs/reclaim", { method: "POST" });
    revalidatePath("/");
    return { ok: true, message: result.message };
  } catch (error) {
    return { ok: false, message: describe(error) };
  }
}

// ---------------------------------------------------------------------------
// Config
// ---------------------------------------------------------------------------

export async function previewEdits(
  edits: unknown[],
): Promise<ActionResult & { diff?: string; invalidates?: string[] }> {
  try {
    const body = await api<{
      diff: string;
      changed: boolean;
      invalidates_goldens: string[];
    }>("/config/preview", {
      method: "POST",
      body: { edits, summary: "preview only, nothing is proposed" },
    });
    if (!body.changed) {
      return { ok: false, message: "Those values are already what the contract says." };
    }
    return { ok: true, message: "", diff: body.diff, invalidates: body.invalidates_goldens };
  } catch (error) {
    return { ok: false, message: describe(error) };
  }
}

export async function proposeEdits(
  edits: unknown[],
  summary: string,
): Promise<ActionResult> {
  try {
    const created = await api<{ id: number }>("/config/proposals", {
      method: "POST",
      body: { edits, summary },
    });
    revalidatePath("/config");
    return {
      ok: true,
      message: `Proposal #${created.id} created. It changes nothing until an admin approves and applies it.`,
    };
  } catch (error) {
    return { ok: false, message: describe(error) };
  }
}

export async function rebaseProposal(id: number): Promise<ActionResult> {
  try {
    const created = await api<{ id: number }>(`/config/proposals/${id}/rebase`, {
      method: "POST",
    });
    revalidatePath("/config");
    return {
      ok: true,
      message: `Replayed against the current contract as proposal #${created.id}. Review its diff — this is a replay of the stated intent, not a merge.`,
    };
  } catch (error) {
    return { ok: false, message: describe(error) };
  }
}

// `proposeChange` is DELETED, along with `config/propose-form.tsx`. It asked for a
// dotted path in a text box, a value in another, and guessed the value's type by
// parsing the string — so it required the operator to already know the pipeline's
// internal key names, and `1.10` versus `"1.10"` was decided by a heuristic. The user
// who asked for this revamp put it plainly: non-technical people do not know what a
// JSON is. `proposeEdits` above takes typed row operations from purpose-built
// controls, and since M8/1.6 the type of a value is decided by the column it is
// going into rather than by parsing what somebody typed.

export async function decideProposal(
  id: number,
  decision: "approve" | "reject",
  note: string,
): Promise<void> {
  await api(`/config/proposals/${id}/${decision}`, { method: "POST", body: { note } });
  revalidatePath("/config");
}

export async function applyProposal(id: number): Promise<void> {
  await api(`/config/proposals/${id}/apply`, { method: "POST" });
  revalidatePath("/config");
}

export async function withdrawProposal(id: number): Promise<void> {
  await api(`/config/proposals/${id}/withdraw`, { method: "POST" });
  revalidatePath("/config");
}
