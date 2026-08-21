import { notFound, redirect } from "next/navigation";

import {
  api,
  ApiError,
  whoami,
  KINDS_BY_PLATFORM,
  type OrderCoverage,
  type WindowDetail,
  type WindowPlan,
} from "@/lib/api";
import { currentLang } from "@/lib/lang";
import { t } from "@/lib/words";

import ReferencesForm from "./references-form";
import RosterForm from "./roster-form";
import UploadForm from "./upload-form";
import FileRow from "./file-row";
import DriftPanel from "./drift-panel";

export const dynamic = "force-dynamic";

export const metadata = { title: "Kỳ đối soát" };

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
  const [me, lang] = await Promise.all([whoami(), currentLang()]);
  const vi = lang === "vi";
  if (!me) redirect("/login");
  if (me.must_change_password) redirect("/account/password");

  const { platform, period } = await params;
  let plan: WindowPlan;
  let detail: WindowDetail;
  try {
    [plan, detail] = await Promise.all([
      api<WindowPlan>(
        `/uploads/plan?platform=${encodeURIComponent(platform)}&period=${encodeURIComponent(period)}`,
      ),
      // A3: the team's own totals live on the window, not on a job, so a re-run is
      // checked against the same figures the first run was.
      api<WindowDetail>(
        `/windows/${encodeURIComponent(platform)}/${encodeURIComponent(period)}`,
      ),
    ]);
  } catch (error) {
    // B2: 422 is what the API returns for an unknown platform or a period that
    // fails `_safe_period` — an address that is not a window, which is a 404 to
    // the person looking at it. Everything else still reaches `error.tsx`.
    if (error instanceof ApiError && (error.status === 404 || error.status === 422)) {
      notFound();
    }
    throw error;
  }

  // Fetched separately and tolerantly: a deployment with no order index answers
  // 501, and a coverage panel failing must not take the whole window page with it.
  // The page's job is to let someone add files; this is advice on top of that.
  let coverage: OrderCoverage | null = null;
  try {
    coverage = await api<OrderCoverage>(
      `/windows/${encodeURIComponent(platform)}/${encodeURIComponent(period)}/order-coverage`,
    );
  } catch {
    coverage = null;
  }

  const kinds = KINDS_BY_PLATFORM[platform] ?? [];
  const total = kinds.reduce((n, k) => n + (plan.files[k]?.length ?? 0), 0);
  const canEdit = me.role !== "recon.viewer";
  const declaration = plan.roster_declaration;
  // D3: a named declaration covers only the stores it names; NULL is the
  // blanket and covers everything. A missing store outside the list means the
  // run hard-stops, and this page must not promise otherwise.
  const declaredList = declaration?.declared_absent_stores ?? null;
  const uncovered =
    declaredList === null
      ? []
      : plan.missing_stores.filter((s) => !declaredList.includes(s));
  const declarationCovers =
    (declaration?.roster_declared_partial ?? false) && uncovered.length === 0;

  return (
    <>
      <h1>
        <span className="mono">{platform}</span> · <span className="mono">{period}</span>
      </h1>
      <p className="lede">
        {vi ? `Đã tải lên ${total} file` : `${total} file${total === 1 ? "" : "s"} uploaded`}
        {plan.expected_store_count > 0 && (
          <>
            {" "}
            ·{" "}
            {vi
              ? `có ${plan.stores_present.length}/${plan.expected_store_count} cửa hàng dự kiến`
              : `${plan.stores_present.length} of ${plan.expected_store_count} expected stores present`}
          </>
        )}
        .{" "}
        {vi
          ? "Tên, số điện thoại và địa chỉ khách hàng được loại bỏ ngay khi file được tải lên, theo đúng danh sách cột mà hệ thống dùng — file lưu ở đây chỉ còn các cột phục vụ đối soát."
          : "Customer names, phone numbers and addresses are removed as each file arrives, using the same column list the run uses — the file kept here is already reduced to the columns the reconciliation reads."}
      </p>

      {plan.problems.length > 0 && (
        <div className="notice bad">
          <strong>
            {vi
              ? "Chưa xác định được các file này."
              : "These files cannot be identified yet."}
          </strong>{" "}
          {vi ? "Chạy bây giờ sẽ dừng ở đây." : "A run would stop on them."}
          <ul style={{ margin: "6px 0 0", paddingLeft: 18 }}>
            {plan.problems.map((p) => (
              <li key={p}>{p}</li>
            ))}
          </ul>
        </div>
      )}

      {plan.unexpected_stores.length > 0 && (
        <div className="notice bad">
          {vi
            ? `${plan.unexpected_stores.length} cửa hàng ở đây không có trong danh sách ${platform}:`
            : `${plan.unexpected_stores.length} store(s) here are not on the ${platform} list:`}{" "}
          <span className="mono">{plan.unexpected_stores.join(", ")}</span>.{" "}
          {vi
            ? "Chạy sẽ dừng ở đây — hãy thêm cửa hàng vào quy tắc thay vì tìm cách đi vòng."
            : "A run will stop on this — add them in the rules rather than working around it."}
        </div>
      )}

      {coverage && coverage.cross_window.length > 0 && (
        <div className="notice bad">
          <strong>
            {vi
              ? "Có đơn được đối soát ở kỳ này nhưng dòng hàng lại nằm ở file của kỳ trước."
              : "Some orders settled here have their line items in an earlier period's file."}
          </strong>{" "}
          {vi
            ? "Nếu chạy như hiện tại, doanh thu của những đơn này sẽ không vào hoá đơn."
            : "Run as it stands, the revenue for those orders will not reach the invoice."}
          <ul style={{ margin: "6px 0 0", paddingLeft: 18 }}>
            {coverage.cross_window.map((row) => (
              <li key={`${row.store}-${row.upload_id}`}>
                <span className="mono">{row.store}</span>
                {vi
                  ? ` — ${row.orders.toLocaleString("vi-VN")} đơn nằm trong `
                  : ` — ${row.orders.toLocaleString("en-US")} order${row.orders === 1 ? "" : "s"} in `}
                <span className="mono">{row.holder_period}</span> (
                <span className="mono">{row.filename}</span>)
              </li>
            ))}
          </ul>
          <div className="muted small" style={{ marginTop: 6 }}>
            {vi
              ? "Cách xử lý là xin sàn xuất lại file đơn hàng cho kỳ này, chứ không phải gộp file của nhiều kỳ — gộp sẽ tính trùng."
              : "The fix is asking the platform to re-export this period's order file, not combining several periods' files — combining double-counts."}
          </div>
        </div>
      )}

      {coverage && !coverage.indexed && coverage.unindexed_files.length > 0 && (
        <div className="notice">
          {vi
            ? `${coverage.unindexed_files.length} file ở kỳ này chưa được lập chỉ mục, nên chưa thể kiểm tra xem đơn được đối soát có đủ dòng hàng hay không. Đây không phải là “đã kiểm tra và không có vấn đề”.`
            : `${coverage.unindexed_files.length} file(s) here are not indexed yet, so whether the settled orders have their line items has not been checked. This is not the same as “checked and fine”.`}
        </div>
      )}

      {/* D3's re-evaluation nudge: this page re-renders after every upload
          action, so it IS the upload-time hook. The figures are included either
          way — the record just no longer describes the window. */}
      {plan.declared_absent_present.length > 0 && (
        <div className="notice">
          {vi
            ? `Cửa hàng đã khai báo vắng nay đã có file: ${plan.declared_absent_present.join(", ")}. Số liệu của các cửa hàng này VẪN được tính; hãy rút lại hoặc khai báo lại để hồ sơ khớp với kỳ.`
            : `Declared-absent store(s) now have files: ${plan.declared_absent_present.join(", ")}. Their figures ARE included; withdraw or re-declare so the record matches the period.`}
        </div>
      )}

      {plan.missing_stores.length > 0 ? (
        <div className={`notice ${declarationCovers ? "" : "bad"}`}>
          <strong>
            {vi
              ? `${plan.missing_stores.length} cửa hàng dự kiến chưa có file:`
              : `${plan.missing_stores.length} expected store${plan.missing_stores.length === 1 ? " has" : "s have"} no file:`}
          </strong>{" "}
          <span className="mono">{plan.missing_stores.join(", ")}</span>
          <div className="muted small" style={{ marginTop: 6 }}>
            {declarationCovers && declaration
              ? vi
                ? `${declaration.declared_by} đã xác nhận kỳ này chỉ có một phần cửa hàng — “${declaration.reason}”. Hệ thống sẽ chạy tiếp, và tổng số ở đây chỉ là một phần của tháng.`
                : `Declared partial by ${declaration.declared_by} — “${declaration.reason}”. The run will proceed and its totals are only part of the month.`
              : declaration?.roster_declared_partial
                ? vi
                  ? `Xác nhận thiếu cửa hàng chưa bao gồm: ${uncovered.join(", ")}. Hệ thống sẽ dừng — thêm cửa hàng vào xác nhận bên dưới, hoặc tải file lên.`
                  : `The declaration does not cover: ${uncovered.join(", ")}. The run will stop — add them to the declaration below, or upload their files.`
                : vi
                  ? "Nếu không có xác nhận, hệ thống sẽ dừng ở đây. Đó là cố ý: một kỳ thiếu cửa hàng mà không ai biết sẽ cho ra file trông đầy đủ nhưng xuất thiếu hoá đơn."
                  : "Without a declaration the run will stop here. That is deliberate: a period quietly missing a store produces a file that looks complete and under-invoices."}
          </div>
        </div>
      ) : (
        plan.ready && (
          <div className="notice good">
            {vi
              ? "Mọi cửa hàng dự kiến đều đã có ít nhất một file. Kỳ này sẵn sàng chạy."
              : "Every expected store has at least one file. This period is ready to run."}
          </div>
        )
      )}

      {canEdit && (
        <UploadForm platform={platform} period={period} kinds={kinds} lang={lang} />
      )}

      {kinds.map((kind) => {
        const files = plan.files[kind] ?? [];
        return (
          <section key={kind}>
            <h2>{kind}</h2>
            {files.length === 0 ? (
              <div className="panel muted">
                {vi ? `Chưa có file ${kind} nào.` : `Nothing uploaded for ${kind} yet.`}
              </div>
            ) : (
              <div className="panel" style={{ padding: 0 }}>
                <table>
                  <thead>
                    <tr>
                      <th>{t(lang, "uploadedAs")}</th>
                      <th>{t(lang, "readAs")}</th>
                      <th>{t(lang, "store")}</th>
                      <th className="num">{t(lang, "size")}</th>
                      <th>{t(lang, "uploadedBy")}</th>
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

            {/* D5: what these files' own headers say. Rendered per kind because
                the required-field arithmetic is per kind — `read_parts` checks the
                concatenation of a kind's part files, not each file. */}
            {files.length > 0 && plan.drift?.[kind] && (
              <DriftPanel
                platform={platform}
                period={period}
                kind={kind}
                drift={plan.drift[kind]}
                canonicalFields={plan.canonical_fields ?? []}
                canEdit={canEdit}
                lang={lang}
              />
            )}
          </section>
        );
      })}

      {!detail.references && (
        <div className="notice">
          {vi ? (
            <>
              <strong>Kỳ này chưa có số của team.</strong> Chạy vẫn xong, nhưng kết quả
              sẽ là <em>chưa đối chiếu</em> — không phải sai, chỉ là chưa có gì bên ngoài
              hệ thống xác nhận.{" "}
              {canEdit ? "Nhập số của team ở bên dưới." : "Nhờ người có quyền nhập số của team."}
            </>
          ) : (
            <>
              <strong>No figures from the team for this period.</strong> A run will
              still finish, but its result will be <em>not checked</em> — not wrong,
              just unconfirmed by anything outside this system.{" "}
              {canEdit ? "Enter the team's totals below." : "Ask someone to enter the team's totals."}
            </>
          )}
        </div>
      )}

      {canEdit && (
        <>
          <h2>{vi ? "Đối chiếu với số của team" : "Checking against the team's numbers"}</h2>
          <ReferencesForm
            platform={platform}
            period={period}
            fields={detail.reference_fields}
            references={detail.references}
            summary={vi ? detail.references_summary_vi : detail.references_summary}
            lang={lang}
          />

          <h2>{vi ? "Danh sách cửa hàng" : "Store list"}</h2>
          <RosterForm
            platform={platform}
            period={period}
            declaration={declaration}
            missingStores={plan.missing_stores}
            expectedStores={plan.expected_stores}
          />
        </>
      )}
    </>
  );
}
