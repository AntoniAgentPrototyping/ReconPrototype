import { redirect } from "next/navigation";

import { ProposalActions } from "./proposal-actions";
import Sections from "./sections";
import {
  api,
  whoami,
  type ConfigPin,
  type ConfigSchema,
  type ConfigVersion,
  type Proposal,
} from "@/lib/api";

export const dynamic = "force-dynamic";

/**
 * The config editor.
 *
 * **The old version of this file argued against itself, and it was right to.** It
 * said: "It does not render settings.yaml as a form. The file's in-line comments are
 * the audit trail... A form would show values stripped of the evidence for them."
 * That objection is correct and had to be answered rather than ignored.
 *
 * The answer is that **evidence is extracted, never dropped**. Each field carries
 * the comment block from the same bytes the form edits, rendered directly above its
 * control — so the four-line VAT block sits above the box you type `1.10` into.
 * That is strictly more evidence at the point of decision than the old `<pre>`, in
 * which the same comment sat 400 lines down where nobody scrolled. The verbatim file
 * is still on the page, underneath.
 *
 * Two things it still does not do:
 *
 * 1. **No dotted paths, no YAML, no brackets.** A user who does not know what a
 *    dotted path is could not use the old form at all. Each section gets
 *    purpose-built controls; the path exists only in the wire format.
 * 2. **It does not write.** Propose, approve, apply — three acts, separately
 *    recorded. Who may do which is now DECIDED rather than configurable
 *    ([open question 13](docs/11-OPEN-QUESTIONS.md) is closed): user or admin
 *    proposes, only admin decides, and self-approval is recorded rather than
 *    forbidden.
 */
export default async function ConfigPage() {
  const me = await whoami();
  if (!me) redirect("/login");
  if (me.must_change_password) redirect("/account/password");

  const [config, schema, proposalsResponse, pinsResponse, versionsResponse] =
    await Promise.all([
      api<{ content: string; sha256: string; git_commit: string | null }>("/config"),
      api<ConfigSchema>("/config/schema"),
      api<{ proposals: Proposal[] }>("/config/proposals"),
      api<{ pins: ConfigPin[] }>("/config/pins"),
      api<{ versions: ConfigVersion[] }>("/config/versions"),
    ]);

  const pending = proposalsResponse.proposals.filter((p) => p.state === "pending");
  const decided = proposalsResponse.proposals.filter((p) => p.state !== "pending");
  const lastVerified = versionsResponse.versions.find((v) => v.verification_state);

  return (
    <>
      <h1>Configuration</h1>
      <p className="lede">
        <span className="mono">config/settings.yaml</span> is the contract the money math
        runs on. Git stays canonical — this proposes structured edits and commits them
        with their comments intact. Every field below shows the note from the file that
        explains its current value.
      </p>

      <div className="panel">
        <table>
          <tbody>
            <tr>
              <th style={{ width: 200 }}>Current</th>
              <td className="mono small">{config.sha256.slice(0, 16)}…</td>
            </tr>
            <tr>
              <th>Git commit</th>
              <td className="mono small">
                {config.git_commit ? (
                  config.git_commit.slice(0, 12)
                ) : (
                  <span className="muted">not a git checkout</span>
                )}
              </td>
            </tr>
            <tr>
              <th title="Who may propose and who may decide">Approval</th>
              <td className="small">
                Anyone but a viewer may propose. Only an admin approves or applies.
                Self-approval is permitted and <strong>recorded</strong> — a
                single-admin deployment has no second person, and refusing would only
                push the edit into a hand-edit with no audit trail at all.
              </td>
            </tr>
            {lastVerified && (
              <tr>
                <th title="Whether the last goldens-affecting change moved a workbook cell">
                  Last verification
                </th>
                <td className="small">
                  <VerificationBadge version={lastVerified} />{" "}
                  {lastVerified.verification?.message}
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      <Sections
        sections={schema.sections}
        canonicalFields={schema.canonical_fields}
        canEdit={me.role !== "recon.viewer"}
      />

      <h2>Pending proposals</h2>
      {pending.length === 0 ? (
        <div className="panel muted">Nothing awaiting a decision.</div>
      ) : (
        pending.map((p) => (
          <ProposalCard key={p.id} proposal={p} role={me.role} subject={me.subject} />
        ))
      )}

      <h2>Pinned windows</h2>
      <div className="panel" style={{ padding: 0 }}>
        <table>
          <thead>
            <tr>
              <th>Window</th>
              <th>Config</th>
              <th>Pinned</th>
              <th>Reason</th>
            </tr>
          </thead>
          <tbody>
            {pinsResponse.pins.length === 0 && (
              <tr>
                <td colSpan={4} className="muted">
                  No windows are pinned yet. A window is pinned by the first run that
                  produces a workbook, so a re-run cannot be changed by an edit made
                  since.
                </td>
              </tr>
            )}
            {pinsResponse.pins.map((pin) => (
              <tr key={`${pin.platform}/${pin.period}`}>
                <td className="mono">
                  {pin.platform} {pin.period}
                </td>
                <td className="mono small">
                  #{pin.config_version_id} · {pin.sha256.slice(0, 12)}
                </td>
                <td className="muted small">{pin.pinned_by ?? "—"}</td>
                <td className="muted small">{pin.reason ?? "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {decided.length > 0 && (
        <>
          <h2>History</h2>
          <div className="panel" style={{ padding: 0 }}>
            <table>
              <thead>
                <tr>
                  <th>#</th>
                  <th>Summary</th>
                  <th>State</th>
                  <th>Proposed by</th>
                  <th>Decided by</th>
                </tr>
              </thead>
              <tbody>
                {decided.map((p) => (
                  <tr key={p.id}>
                    <td>{p.id}</td>
                    <td>{p.summary}</td>
                    <td>
                      <span
                        className={`badge ${p.state === "applied" ? "ok" : "muted"}`}
                      >
                        {p.state}
                      </span>
                      {p.self_approved && (
                        <div
                          className="muted small"
                          title="The approver was also the author. Permitted, and recorded."
                        >
                          self-approved
                        </div>
                      )}
                    </td>
                    <td className="muted small">{p.proposed_by}</td>
                    <td className="muted small">{p.decided_by ?? "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}

      <h2>The file</h2>
      <div className="panel">
        <p className="small muted" style={{ marginTop: 0 }}>
          Still shown verbatim, comments included. The form above does not replace
          this — it extracts each value&apos;s comment block and puts it next to the
          control that changes it.
        </p>
        <pre className="diff">{config.content}</pre>
      </div>
    </>
  );
}

function VerificationBadge({ version }: { version: ConfigVersion }) {
  const state = version.verification_state;
  if (state === "verified") {
    return (
      <span className={`badge ${version.verified_window_is_real ? "ok" : "muted"}`}>
        {version.verified_window_is_real ? "verified" : "verified (synthetic)"}
      </span>
    );
  }
  if (state === "cells_moved") return <span className="badge variance">cells moved</span>;
  if (state === "failed") return <span className="badge hard_stop">check failed</span>;
  if (state === "unavailable") return <span className="badge muted">not verified</span>;
  return <span className="badge muted">no check needed</span>;
}

async function ProposalCard({
  proposal,
  role,
  subject,
}: {
  proposal: Proposal;
  role: string;
  subject: string;
}) {
  const full = await api<Proposal>(`/config/proposals/${proposal.id}`);
  return (
    <div className="panel">
      <p style={{ marginTop: 0 }}>
        <strong>#{proposal.id}</strong> {proposal.summary}
        <br />
        <span className="muted small">
          proposed by {proposal.proposed_by} ·{" "}
          {new Date(proposal.proposed_at).toLocaleString()}
          {full.rebased_from ? ` · replayed from #${full.rebased_from}` : ""}
        </span>
      </p>
      {(full.edits ?? []).length > 0 && (
        <ul className="small" style={{ paddingLeft: 18 }}>
          {(full.edits ?? []).map((edit, index) => (
            <li key={index} className="muted">
              {edit.op.replace(/_/g, " ")} · {edit.path.join(" → ")}
              {edit.key ? ` → ${edit.key}` : ""}
              {edit.value !== undefined ? ` = ${JSON.stringify(edit.value)}` : ""}
            </li>
          ))}
        </ul>
      )}
      <pre className="diff">
        {(full.diff ?? "").split("\n").map((line, index) => (
          <div
            key={index}
            className={
              line.startsWith("+") && !line.startsWith("+++")
                ? "add"
                : line.startsWith("-") && !line.startsWith("---")
                  ? "del"
                  : undefined
            }
          >
            {line || " "}
          </div>
        ))}
      </pre>
      <ProposalActions
        id={proposal.id}
        state={proposal.state}
        canDecide={role === "recon.admin"}
        isAuthor={proposal.proposed_by === subject}
      />
    </div>
  );
}
