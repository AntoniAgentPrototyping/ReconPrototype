"use client";

import { useMemo, useState, useTransition } from "react";

import { previewEdits, proposeEdits, type ActionResult } from "../actions";
import type {
  ConfigColumn,
  ConfigRow,
  ConfigRowEdit,
  ConfigTable,
} from "@/lib/api";

/**
 * The config editor.
 *
 * **Every row renders its own evidence.** Through M6 this read comment blocks out
 * of `settings.yaml`, and a file can only caption a top-level key — so the roster's
 * justification appeared against all 42 storefronts in it and one alias's proof
 * could not be shown at all. Since M8/1.6 evidence is a column, so it sits beside
 * the entry it justifies and is deleted with it.
 *
 * **No user ever sees a table name, a column name or a bracket.** Those exist in
 * the wire format because the API has to name what is changing. Each column names
 * a `kind` and gets a purpose-built control.
 *
 * Nothing here writes. Edits accumulate locally, the diff is previewed against the
 * real contract, and submitting creates a *proposal* — which an admin then approves
 * and applies as two separate acts.
 */
export type PendingEdit = ConfigRowEdit & {
  /** What to show the operator in the basket. Never sent. */
  describe: string;
};

export default function Tables({
  tables,
  canEdit,
}: {
  tables: ConfigTable[];
  canEdit: boolean;
}) {
  const [open, setOpen] = useState<string | null>(tables[0]?.table ?? null);
  const [basket, setBasket] = useState<PendingEdit[]>([]);

  const add = (edit: PendingEdit) => setBasket((current) => [...current, edit]);
  const drop = (index: number) =>
    setBasket((current) => current.filter((_, i) => i !== index));

  return (
    <>
      {basket.length > 0 && canEdit && (
        <Basket edits={basket} onDrop={drop} onCleared={() => setBasket([])} />
      )}

      {tables.map((table) => (
        <section key={table.table}>
          <h2>
            <button
              type="button"
              className="secondary"
              onClick={() => setOpen(open === table.table ? null : table.table)}
              style={{ marginRight: 8 }}
              aria-expanded={open === table.table}
            >
              {open === table.table ? "−" : "+"}
            </button>
            {table.title}{" "}
            <span className="muted small">
              {table.rows.length} {table.rows.length === 1 ? "entry" : "entries"}
            </span>
          </h2>
          <p className="lede">{table.blurb}</p>
          {open === table.table && (
            <div className="panel">
              {table.invalidates_goldens && (
                <p className="muted small" style={{ marginTop: 0 }}>
                  A change here can move a cell in the finance workbook, so applying
                  it automatically re-runs a reference window and reports whether
                  anything moved.
                </p>
              )}
              {table.table === "config_scalars" ? (
                <SettingsList table={table} canEdit={canEdit} onAdd={add} />
              ) : (
                <RowGroups table={table} canEdit={canEdit} onAdd={add} />
              )}
            </div>
          )}
        </section>
      ))}
    </>
  );
}

// ---------------------------------------------------------------------------
// The basket: edits accumulate, then become ONE proposal
// ---------------------------------------------------------------------------

function Basket({
  edits,
  onDrop,
  onCleared,
}: {
  edits: PendingEdit[];
  onDrop: (index: number) => void;
  onCleared: () => void;
}) {
  const [summary, setSummary] = useState("");
  const [pending, start] = useTransition();
  const [result, setResult] = useState<ActionResult | null>(null);
  const [diff, setDiff] = useState<string | null>(null);
  const [affects, setAffects] = useState<string[]>([]);

  const wire = useMemo(
    () => edits.map(({ describe, ...rest }) => rest),
    [edits],
  );

  return (
    <div className="panel" style={{ borderColor: "#888" }}>
      <h3 style={{ marginTop: 0 }}>
        {edits.length} change{edits.length === 1 ? "" : "s"} ready to propose
      </h3>
      <p className="muted small">
        These land as <strong>one</strong> proposal, so adding a storefront and its
        export spelling is one review rather than two. They are applied in the order
        below — which matters, because an alias may only point at a storefront that
        is already in the roster.
      </p>

      <ol style={{ paddingLeft: 18 }}>
        {edits.map((edit, index) => (
          <li key={index} style={{ marginBottom: 4 }}>
            {edit.describe}{" "}
            <button type="button" className="secondary" onClick={() => onDrop(index)}>
              remove
            </button>
          </li>
        ))}
      </ol>

      {affects.length > 0 && (
        <div className="notice" role="status">
          This touches {affects.join(", ")}, which can move a cell in the finance
          workbook. Applying it will automatically re-run a reference window and
          report whether anything moved.
        </div>
      )}

      {diff && <pre className="diff">{diff}</pre>}
      {result && (
        <div className={`notice ${result.ok ? "good" : "bad"}`} role="alert">
          {result.message}
        </div>
      )}

      <label htmlFor="summary">Why are you making this change?</label>
      <input
        id="summary"
        value={summary}
        onChange={(e) => setSummary(e.target.value)}
        placeholder="e.g. the 8% VAT concession ended on 2026-07-01"
        style={{ width: "100%", marginBottom: 10 }}
      />

      <div style={{ display: "flex", gap: 8 }}>
        <button
          type="button"
          className="secondary"
          disabled={pending}
          onClick={() =>
            start(async () => {
              const preview = await previewEdits(wire);
              setResult(preview.ok ? null : preview);
              setDiff(preview.diff ?? null);
              setAffects(preview.invalidates ?? []);
            })
          }
        >
          Show me the diff
        </button>
        <button
          type="button"
          disabled={pending || summary.trim().length < 8}
          onClick={() =>
            start(async () => {
              const created = await proposeEdits(wire, summary.trim());
              setResult(created);
              if (created.ok) {
                onCleared();
                setDiff(null);
                setSummary("");
              }
            })
          }
        >
          {pending ? "Proposing…" : "Propose these changes"}
        </button>
      </div>
      {summary.trim().length < 8 && (
        <p className="muted small" style={{ marginBottom: 0 }}>
          A sentence of reason is required — it is what makes this change defensible
          in six months.
        </p>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// The single settings: one value each, each with its own label and evidence
// ---------------------------------------------------------------------------

function SettingsList({
  table,
  canEdit,
  onAdd,
}: {
  table: ConfigTable;
  canEdit: boolean;
  onAdd: (edit: PendingEdit) => void;
}) {
  return (
    <>
      {table.rows.map((row) => (
        <Setting
          key={String(row.key.key)}
          table={table}
          row={row}
          canEdit={canEdit}
          onAdd={onAdd}
        />
      ))}
    </>
  );
}

function Setting({
  table,
  row,
  canEdit,
  onAdd,
}: {
  table: ConfigTable;
  row: ConfigRow;
  canEdit: boolean;
  onAdd: (edit: PendingEdit) => void;
}) {
  const label = row.label ?? String(row.key.key);
  const value = row.values.value;

  return (
    <div style={{ borderTop: "1px solid #2a2a2a", padding: "14px 0" }}>
      <div style={{ display: "flex", alignItems: "baseline", gap: 8, flexWrap: "wrap" }}>
        <strong>{label}</strong>
        {row.invalidates_goldens && (
          <span
            className="badge variance"
            title="A change here can move a cell in the finance workbook, so applying it triggers an automatic re-verification"
          >
            affects the numbers
          </span>
        )}
        {row.locked && <span className="badge muted">locked</span>}
      </div>

      <Evidence text={row.evidence} />
      {row.help && (
        <p className="muted small" style={{ marginTop: 4 }}>
          {row.help}
        </p>
      )}

      {row.locked ? (
        <div>
          <ReadOnlyValue value={value} />
          <p className="notice" style={{ marginTop: 8 }}>
            {row.locked_reason}
          </p>
        </div>
      ) : canEdit ? (
        <ScalarControl
          label={label}
          value={value}
          onStage={(next, describe) =>
            onAdd({
              table: table.table,
              op: "upsert",
              key: row.key,
              values: { value: next },
              describe,
            })
          }
        />
      ) : (
        <ReadOnlyValue value={value} />
      )}
    </div>
  );
}

/**
 * A setting keeps the shape it already has: a switch for a flag, a list editor for
 * a list, a box for anything else. The server enforces the same rule — a scalar
 * cannot change type through this form, because `dedupe_rows` becoming the string
 * "false" would be truthy in Python and silently invert the flag.
 */
function ScalarControl({
  label,
  value,
  onStage,
}: {
  label: string;
  value: unknown;
  onStage: (next: unknown, describe: string) => void;
}) {
  if (typeof value === "boolean") {
    return (
      <div>
        <p className="small" style={{ margin: "4px 0" }}>
          Currently <strong>{value ? "on" : "off"}</strong>.
        </p>
        <button
          type="button"
          onClick={() =>
            onStage(!value, `${label}: ${value ? "on" : "off"} → ${!value ? "on" : "off"}`)
          }
        >
          Turn it {value ? "off" : "on"}
        </button>
      </div>
    );
  }
  if (Array.isArray(value)) {
    return <ListControl label={label} items={value as string[]} onStage={onStage} />;
  }
  return <TextControl label={label} value={value} onStage={onStage} />;
}

function TextControl({
  label,
  value,
  onStage,
}: {
  label: string;
  value: unknown;
  onStage: (next: unknown, describe: string) => void;
}) {
  const current = value === null || value === undefined ? "" : String(value);
  const [draft, setDraft] = useState(current);
  const numeric = typeof value === "number";

  return (
    <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
      <input
        value={draft}
        onChange={(e) => setDraft(e.target.value)}
        inputMode={numeric ? "decimal" : undefined}
        aria-label={label}
        style={{ width: numeric ? 160 : 320 }}
      />
      <button
        type="button"
        disabled={draft === current || draft.trim() === ""}
        onClick={() =>
          onStage(
            numeric ? Number(draft) : draft,
            `${label}: ${current} → ${draft}`,
          )
        }
      >
        Stage change
      </button>
    </div>
  );
}

function ListControl({
  label,
  items,
  onStage,
}: {
  label: string;
  items: string[];
  onStage: (next: unknown, describe: string) => void;
}) {
  const [draft, setDraft] = useState("");
  return (
    <div>
      <div className="mono small" style={{ marginBottom: 6 }}>
        {items.length === 0 ? <span className="muted">empty</span> : items.join(" · ")}
      </div>
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
        <input
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          placeholder="add an entry"
          aria-label={`Add to ${label}`}
        />
        <button
          type="button"
          disabled={!draft.trim() || items.includes(draft.trim())}
          onClick={() => {
            onStage([...items, draft.trim()], `${label}: add ${draft.trim()}`);
            setDraft("");
          }}
        >
          Add
        </button>
        {items.map((item) => (
          <button
            key={item}
            type="button"
            className="secondary"
            onClick={() =>
              onStage(
                items.filter((i) => i !== item),
                `${label}: remove ${item}`,
              )
            }
          >
            remove {item}
          </button>
        ))}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Every other table: rows, grouped where a grouping was declared
// ---------------------------------------------------------------------------

function RowGroups({
  table,
  canEdit,
  onAdd,
}: {
  table: ConfigTable;
  canEdit: boolean;
  onAdd: (edit: PendingEdit) => void;
}) {
  const groups = useMemo(() => {
    if (!table.grouped_by) return [{ name: null as string | null, rows: table.rows }];
    const column = table.grouped_by;
    const seen = new Map<string, ConfigRow[]>();
    for (const option of table.key.find((c) => c.name === column)?.options ?? []) {
      seen.set(String(option.value), []);
    }
    for (const row of table.rows) {
      const name = String(row.key[column] ?? "—");
      seen.set(name, [...(seen.get(name) ?? []), row]);
    }
    return [...seen.entries()].map(([name, rows]) => ({ name, rows }));
  }, [table]);

  return (
    <>
      {groups.map((group) => (
        <div key={group.name ?? "all"} style={{ marginBottom: 18 }}>
          {group.name && <h4 style={{ marginBottom: 4 }}>{group.name}</h4>}
          <RowTable
            table={table}
            rows={group.rows}
            fixed={table.grouped_by ? { [table.grouped_by]: group.name } : {}}
            canEdit={canEdit}
            onAdd={onAdd}
          />
        </div>
      ))}
    </>
  );
}

function RowTable({
  table,
  rows,
  fixed,
  canEdit,
  onAdd,
}: {
  table: ConfigTable;
  rows: ConfigRow[];
  fixed: Record<string, unknown>;
  canEdit: boolean;
  onAdd: (edit: PendingEdit) => void;
}) {
  const keyColumns = table.key.filter((c) => !(c.name in fixed));

  return (
    <>
      <table>
        <thead>
          <tr>
            {keyColumns.map((c) => (
              <th key={c.name}>{c.label}</th>
            ))}
            {table.columns.map((c) => (
              <th key={c.name} title={c.help}>
                {c.label}
              </th>
            ))}
            <th>Why</th>
            {canEdit && table.may_delete && <th style={{ width: 60 }} />}
          </tr>
        </thead>
        <tbody>
          {rows.length === 0 && (
            <tr>
              <td colSpan={keyColumns.length + table.columns.length + 2} className="muted small">
                Nothing here yet.
              </td>
            </tr>
          )}
          {rows.map((row) => (
            <tr key={JSON.stringify(row.key)}>
              {keyColumns.map((c) => (
                <td key={c.name} className="mono small">
                  {display(row.key[c.name])}
                </td>
              ))}
              {table.columns.map((c) => (
                <td key={c.name}>
                  {canEdit ? (
                    <CellControl
                      column={c}
                      value={row.values[c.name]}
                      onStage={(next) =>
                        onAdd({
                          table: table.table,
                          op: "upsert",
                          key: row.key,
                          values: { [c.name]: next },
                          describe: `${table.title}: ${describeKey(row.key)} — ${c.label.toLowerCase()} → ${display(next)}`,
                        })
                      }
                    />
                  ) : (
                    <span className="mono small">{display(row.values[c.name])}</span>
                  )}
                </td>
              ))}
              <td className="muted small" style={{ maxWidth: 320, whiteSpace: "pre-wrap" }}>
                {row.evidence || <span className="muted">—</span>}
              </td>
              {canEdit && table.may_delete && (
                <td>
                  <button
                    type="button"
                    className="secondary"
                    aria-label={`Remove ${describeKey(row.key)}`}
                    onClick={() =>
                      onAdd({
                        table: table.table,
                        op: "delete",
                        key: row.key,
                        describe: `${table.title}: remove ${describeKey(row.key)}`,
                      })
                    }
                  >
                    ✕
                  </button>
                </td>
              )}
            </tr>
          ))}
        </tbody>
      </table>

      {canEdit &&
        (table.may_insert ? (
          <AddRow table={table} fixed={fixed} onAdd={onAdd} />
        ) : (
          <p className="muted small">{table.closed_reason}</p>
        ))}
    </>
  );
}

function CellControl({
  column,
  value,
  onStage,
}: {
  column: ConfigColumn;
  value: unknown;
  onStage: (next: unknown) => void;
}) {
  if (column.kind === "bool") {
    return (
      <label style={{ display: "flex", alignItems: "center", gap: 6 }}>
        <input
          type="checkbox"
          checked={Boolean(value)}
          onChange={(e) => onStage(e.target.checked)}
          style={{ width: 16 }}
          aria-label={column.label}
        />
        <span className="small">{value ? "yes" : "no"}</span>
      </label>
    );
  }
  return <EditableCell column={column} value={value} onStage={onStage} />;
}

function EditableCell({
  column,
  value,
  onStage,
}: {
  column: ConfigColumn;
  value: unknown;
  onStage: (next: unknown) => void;
}) {
  const current = value === null || value === undefined ? "" : String(value);
  const [draft, setDraft] = useState(current);

  if (column.kind === "enum" && column.options.length > 0) {
    return (
      <select
        value={current}
        aria-label={column.label}
        onChange={(e) => onStage(e.target.value)}
      >
        {column.nullable && <option value="">—</option>}
        {column.options.map((option) => (
          <option key={option.value} value={option.value}>
            {option.label}
          </option>
        ))}
      </select>
    );
  }

  return (
    <span style={{ display: "inline-flex", gap: 4 }}>
      <input
        type={column.kind === "date" ? "date" : "text"}
        value={draft}
        onChange={(e) => setDraft(e.target.value)}
        inputMode={
          column.kind === "int" || column.kind === "number" || column.kind === "money_vnd"
            ? "decimal"
            : undefined
        }
        aria-label={column.label}
        style={{ width: column.kind === "text" ? 200 : 130 }}
      />
      {draft !== current && (
        <button type="button" onClick={() => onStage(coerce(column, draft))}>
          stage
        </button>
      )}
    </span>
  );
}

/**
 * Adding a row. The reason is a required field rather than an optional note,
 * because evidence lives in the row's own column and a new entry without one is an
 * entry nobody can defend later.
 */
function AddRow({
  table,
  fixed,
  onAdd,
}: {
  table: ConfigTable;
  fixed: Record<string, unknown>;
  onAdd: (edit: PendingEdit) => void;
}) {
  const keyColumns = table.key.filter((c) => !(c.name in fixed));
  const [draft, setDraft] = useState<Record<string, string>>({});
  const [evidence, setEvidence] = useState("");

  const set = (name: string, value: string) =>
    setDraft((current) => ({ ...current, [name]: value }));

  const complete = keyColumns.every((c) => c.nullable || (draft[c.name] ?? "").trim());
  const enoughReason = evidence.trim().length >= table.min_evidence;

  return (
    <div style={{ marginTop: 10 }}>
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "flex-end" }}>
        {[...keyColumns, ...table.columns].map((column) => (
          <div key={column.name}>
            <label htmlFor={`${table.table}-${column.name}`} className="small">
              {column.label}
            </label>
            {column.kind === "enum" && column.options.length > 0 ? (
              <select
                id={`${table.table}-${column.name}`}
                value={draft[column.name] ?? ""}
                onChange={(e) => set(column.name, e.target.value)}
              >
                <option value="">—</option>
                {column.options.map((option) => (
                  <option key={option.value} value={option.value}>
                    {option.label}
                  </option>
                ))}
              </select>
            ) : (
              <input
                id={`${table.table}-${column.name}`}
                type={column.kind === "date" ? "date" : "text"}
                value={draft[column.name] ?? ""}
                onChange={(e) => set(column.name, e.target.value)}
                placeholder={column.default === null ? "" : String(column.default ?? "")}
                style={{ width: column.kind === "text" ? 200 : 130 }}
              />
            )}
          </div>
        ))}
      </div>

      <label htmlFor={`${table.table}-why`} className="small" style={{ marginTop: 6 }}>
        Why is this entry here?
      </label>
      <textarea
        id={`${table.table}-why`}
        value={evidence}
        onChange={(e) => setEvidence(e.target.value)}
        rows={2}
        placeholder={`the reason, stored against this entry — at least ${table.min_evidence} characters`}
        style={{ width: "100%" }}
      />
      {!enoughReason && (
        <p className="muted small" style={{ margin: "2px 0" }}>
          At least {table.min_evidence} characters. It travels with this entry and is
          deleted with it, so it cannot end up describing the one below.
        </p>
      )}

      <button
        type="button"
        disabled={!complete || !enoughReason}
        onClick={() => {
          const key: Record<string, unknown> = { ...fixed };
          for (const column of keyColumns) {
            const raw = (draft[column.name] ?? "").trim();
            key[column.name] = raw ? coerce(column, raw) : null;
          }
          const values: Record<string, unknown> = {};
          for (const column of table.columns) {
            const raw = (draft[column.name] ?? "").trim();
            if (raw) values[column.name] = coerce(column, raw);
          }
          onAdd({
            table: table.table,
            op: "upsert",
            key,
            values,
            evidence: evidence.trim(),
            describe: `${table.title}: add ${describeKey(key)}`,
          });
          setDraft({});
          setEvidence("");
        }}
      >
        Add
      </button>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Small shared pieces
// ---------------------------------------------------------------------------

function Evidence({ text }: { text: string }) {
  if (!text.trim()) return null;
  return (
    <blockquote
      className="muted small"
      style={{
        margin: "8px 0",
        paddingLeft: 10,
        borderLeft: "3px solid #444",
        whiteSpace: "pre-wrap",
      }}
    >
      {text}
    </blockquote>
  );
}

function ReadOnlyValue({ value }: { value: unknown }) {
  if (value === null || value === undefined) return <span className="muted">not set</span>;
  if (Array.isArray(value)) {
    return (
      <div className="mono small">
        {value.length === 0 ? <span className="muted">empty</span> : value.join(" · ")}
      </div>
    );
  }
  return <span className="mono">{display(value)}</span>;
}

function display(value: unknown): string {
  if (value === null || value === undefined || value === "") return "—";
  if (typeof value === "boolean") return value ? "yes" : "no";
  return String(value);
}

function describeKey(key: Record<string, unknown>): string {
  return Object.values(key)
    .filter((v) => v !== null && v !== undefined && v !== "")
    .map(String)
    .join(" · ");
}

function coerce(column: ConfigColumn, raw: string): unknown {
  if (column.kind === "int") return Number.parseInt(raw, 10);
  if (column.kind === "number" || column.kind === "money_vnd") return Number(raw);
  if (column.kind === "bool") return raw === "true" || raw === "yes";
  return raw;
}
