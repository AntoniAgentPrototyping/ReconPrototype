import { redirect } from "next/navigation";

import { api, whoami, KINDS_BY_PLATFORM, type WindowPlan } from "@/lib/api";

import RosterForm from "./roster-form";
import UploadForm from "./upload-form";
import FileRow from "./file-row";

export const dynamic = "force-dynamic";

/**
 * One settlement window: what is in it, what it will be called, what is missing.
 *
 * This page replaces the step that used to happen outside the system — someone
 * copying exports into `input/<period>/<platform>/` by hand and then hoping. It
 * answers three questions the old flow could only answer by starting a run and
 * reading a hard stop:
 *
 *  * which files are here, and who put them there;
 *  * what each will be **named** when the pipeline reads it, because store
 *    identity is derived from the filename ([D6](docs/06-DECISIONS.md#d6)) and a
 *    rename that got that wrong would reassign a storefront's revenue;
 *  * which expected stores have nothing, which is the check that once caught a
 *    real Shopee window arriving with 16 of 17 stores absent.
 *
 * The ordinal is shown greyed as `NNN` until a run assigns it. That is not
 * coyness: the ordinal is a property of the whole window and is computed per run
 * from the window's sorted names, because deciding it at upload time would let two
 * concurrent uploads race to determine workbook row order.
 */
export default async function WindowPage({
  params,
}: {
  params: Promise<{ platform: string; period: string }>;
}) {
  const me = await whoami();
  if (!me) redirect("/login");
  if (me.must_change_password) redirect("/account/password");

  const { platform, period } = await params;
  const plan = await api<WindowPlan>(
    `/uploads/plan?platform=${encodeURIComponent(platform)}&period=${encodeURIComponent(period)}`,
  );

  const kinds = KINDS_BY_PLATFORM[platform] ?? [];
  const total = kinds.reduce((n, k) => n + (plan.files[k]?.length ?? 0), 0);
  const canEdit = me.role !== "recon.viewer";
  const declaration = plan.roster_declaration;

  return (
    <>
      <h1>
        <span className="mono">{platform}</span> · <span className="mono">{period}</span>
      </h1>
      <p className="lede">
        {total} file{total === 1 ? "" : "s"} uploaded
        {plan.expected_store_count > 0 && (
          <>
            {" "}
            · {plan.stores_present.length} of {plan.expected_store_count} expected stores
            present
          </>
        )}
        . Customer names, phone numbers and addresses are stripped as each file
        arrives, using the pipeline&apos;s own column map — the file kept here is
        already reduced to the columns the reconciliation reads.
      </p>

      {plan.problems.length > 0 && (
        <div className="notice bad">
          <strong>These files cannot be named yet.</strong> A run would stop on them.
          <ul style={{ margin: "6px 0 0", paddingLeft: 18 }}>
            {plan.problems.map((p) => (
              <li key={p}>{p}</li>
            ))}
          </ul>
        </div>
      )}

      {plan.unexpected_stores.length > 0 && (
        <div className="notice bad">
          {plan.unexpected_stores.length} store(s) here are not on the {platform} roster:{" "}
          <span className="mono">{plan.unexpected_stores.join(", ")}</span>. A run will stop
          on this — add them through a config change rather than working around it.
        </div>
      )}

      {plan.missing_stores.length > 0 ? (
        <div className={`notice ${declaration?.roster_declared_partial ? "" : "bad"}`}>
          <strong>
            {plan.missing_stores.length} expected store
            {plan.missing_stores.length === 1 ? "" : "s"} ha
            {plan.missing_stores.length === 1 ? "s" : "ve"} no file:
          </strong>{" "}
          <span className="mono">{plan.missing_stores.join(", ")}</span>
          <div className="muted small" style={{ marginTop: 6 }}>
            {declaration?.roster_declared_partial
              ? `Declared partial by ${declaration.declared_by} — “${declaration.reason}”. The run will proceed and its totals are a subset of the month.`
              : "Without a declaration the run will stop here. That is deliberate: a window quietly missing a store produces a workbook that looks complete and under-invoices."}
          </div>
        </div>
      ) : (
        plan.ready && (
          <div className="notice good">
            Every expected store has at least one file. This window is ready to run.
          </div>
        )
      )}

      {canEdit && <UploadForm platform={platform} period={period} kinds={kinds} />}

      {kinds.map((kind) => {
        const files = plan.files[kind] ?? [];
        return (
          <section key={kind}>
            <h2>{kind}</h2>
            {files.length === 0 ? (
              <div className="panel muted">Nothing uploaded for {kind} yet.</div>
            ) : (
              <div className="panel" style={{ padding: 0 }}>
                <table>
                  <thead>
                    <tr>
                      <th>Uploaded as</th>
                      <th>The pipeline will read it as</th>
                      <th>Store</th>
                      <th className="num">Size</th>
                      <th>By</th>
                      <th />
                    </tr>
                  </thead>
                  <tbody>
                    {files.map((file) => (
                      <FileRow
                        key={file.upload_id}
                        file={file}
                        platform={platform}
                        period={period}
                        canEdit={canEdit}
                      />
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </section>
        );
      })}

      {canEdit && (
        <>
          <h2>Roster</h2>
          <RosterForm
            platform={platform}
            period={period}
            declaration={declaration}
            missingCount={plan.missing_stores.length}
          />
        </>
      )}
    </>
  );
}
