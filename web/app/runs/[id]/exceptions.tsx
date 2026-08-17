import { api, type ExceptionRow, type ExceptionSheet } from "@/lib/api";

/**
 * The exception queue for one run.
 *
 * The single most important thing on this screen is `truncated`. TikTok's
 * unmatched-settlement class alone is ~11,765 orders, so the queue stores a
 * bounded slice — and a capped queue that looks complete is a lie with a UI on
 * it. Where rows were dropped, it says so, with the real total.
 */
export async function ExceptionQueue({
  runId,
  sheets,
}: {
  runId: number;
  sheets: ExceptionSheet[];
}) {
  if (sheets.length === 0) {
    return (
      <div className="panel muted">
        No exceptions were recorded for this run. Every fee name mapped and every settlement
        line reached the invoice.
      </div>
    );
  }

  const { exceptions } = await api<{ exceptions: ExceptionRow[] }>(
    `/runs/${runId}/exceptions?limit=200`,
  );

  const columnsOf = (rows: ExceptionRow[]) => {
    const seen = new Set<string>();
    for (const row of rows) for (const key of Object.keys(row.payload)) seen.add(key);
    return [...seen].slice(0, 7);
  };

  return (
    <>
      {sheets.map((sheet) => {
        const rows = exceptions.filter((e) => e.sheet === sheet.sheet);
        const columns = columnsOf(rows);
        return (
          <div className="panel" key={sheet.sheet} style={{ padding: 0 }}>
            <div style={{ padding: "12px 16px" }}>
              <strong className="mono">{sheet.sheet}</strong>{" "}
              <span className="muted small">
                {sheet.total_rows.toLocaleString()} row
                {sheet.total_rows === 1 ? "" : "s"}
              </span>
              {sheet.truncated && (
                <div className="notice" style={{ marginTop: 8, marginBottom: 0 }}>
                  Showing {sheet.stored_rows.toLocaleString()} of{" "}
                  {sheet.total_rows.toLocaleString()}. The rest are in{" "}
                  <span className="mono">exceptions.xlsx</span> — this queue is capped, and
                  saying so is the point.
                </div>
              )}
            </div>
            <table>
              <thead>
                <tr>
                  <th>Fingerprint</th>
                  {columns.map((c) => (
                    <th key={c}>{c}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {rows.slice(0, 50).map((row) => (
                  <tr key={row.id}>
                    <td
                      className="mono small muted"
                      title="Stable identity for this exception across runs — how recurrence becomes visible"
                    >
                      {row.fingerprint.slice(0, 10)}
                    </td>
                    {columns.map((c) => (
                      <td key={c} className="small">
                        {format(row.payload[c])}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        );
      })}
    </>
  );
}

function format(value: unknown): string {
  if (value === null || value === undefined) return "—";
  if (typeof value === "number") return value.toLocaleString();
  return String(value);
}
