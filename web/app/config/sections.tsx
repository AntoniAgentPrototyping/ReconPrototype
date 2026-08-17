"use client";

import { useMemo, useState, useTransition } from "react";

import { proposeEdits, previewEdits, type ActionResult } from "../actions";
import type { ConfigField, ConfigSection } from "@/lib/api";

/**
 * The sectioned config editor.
 *
 * **Every field renders its own evidence.** The comment block from
 * `settings.yaml` sits directly above the control that changes the value — the
 * four-line VAT block above the box you type `1.10` into. That is the answer to the
 * objection the old page raised against itself ("a form would show values stripped
 * of the evidence for them"): the evidence is extracted from the same bytes the form
 * edits, so it is strictly more evidence at the point of decision than a `<pre>`
 * where the comment sits 400 lines down and nobody scrolls.
 *
 * **No user ever sees a bracket, a brace or a dotted path.** Each field names a
 * widget and gets a purpose-built control. The dotted path exists only in the wire
 * format.
 *
 * Nothing here writes. Edits accumulate locally, the diff is previewed against the
 * real file, and submitting creates a *proposal* — which an admin then approves and
 * applies as two separate acts.
 */
export type PendingEdit = {
  op: string;
  path: string[];
  value?: unknown;
  key?: string;
  comment?: string;
  comment_disposition?: "keep" | "remove";
  /** What to show the operator in the basket. Never sent. */
  describe: string;
};

export default function Sections({
  sections,
  canonicalFields,
  canEdit,
}: {
  sections: ConfigSection[];
  canonicalFields: string[];
  canEdit: boolean;
}) {
  const [open, setOpen] = useState<string | null>(sections[0]?.key ?? null);
  const [basket, setBasket] = useState<PendingEdit[]>([]);

  const add = (edit: PendingEdit) =>
    setBasket((current) => [...current, edit]);
  const drop = (index: number) =>
    setBasket((current) => current.filter((_, i) => i !== index));

  return (
    <>
      {basket.length > 0 && canEdit && (
        <Basket edits={basket} onDrop={drop} onCleared={() => setBasket([])} />
      )}

      {sections.map((section) => (
        <section key={section.key}>
          <h2>
            <button
              type="button"
              className="secondary"
              onClick={() => setOpen(open === section.key ? null : section.key)}
              style={{ marginRight: 8 }}
            >
              {open === section.key ? "−" : "+"}
            </button>
            {section.title}
          </h2>
          <p className="lede">{section.blurb}</p>
          {open === section.key && (
            <div className="panel">
              {section.fields.map((field) => (
                <FieldRow
                  key={field.dotted}
                  field={field}
                  canonicalFields={canonicalFields}
                  canEdit={canEdit}
                  onAdd={add}
                />
              ))}
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
        These land as <strong>one</strong> proposal, so adding a store and its export
        spelling is one review rather than two.
      </p>

      <ul style={{ paddingLeft: 18 }}>
        {edits.map((edit, index) => (
          <li key={index} style={{ marginBottom: 4 }}>
            {edit.describe}{" "}
            <button type="button" className="secondary" onClick={() => onDrop(index)}>
              remove
            </button>
          </li>
        ))}
      </ul>

      {affects.length > 0 && (
        <div className="notice">
          This touches {affects.join(", ")}, which can move a cell in the finance
          workbook. Applying it will automatically re-run a reference window and
          report whether anything moved.
        </div>
      )}

      {diff && <pre className="diff">{diff}</pre>}
      {result && (
        <div className={`notice ${result.ok ? "good" : "bad"}`}>{result.message}</div>
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
// One field: its evidence, then its control
// ---------------------------------------------------------------------------

function FieldRow({
  field,
  canonicalFields,
  canEdit,
  onAdd,
}: {
  field: ConfigField;
  canonicalFields: string[];
  canEdit: boolean;
  onAdd: (edit: PendingEdit) => void;
}) {
  return (
    <div style={{ borderTop: "1px solid #2a2a2a", padding: "14px 0" }}>
      <div style={{ display: "flex", alignItems: "baseline", gap: 8, flexWrap: "wrap" }}>
        <strong>{field.label}</strong>
        {field.invalidates_goldens && (
          <span className="badge variance" title="A change here can move a cell in the finance workbook, so applying it triggers an automatic re-verification">
            affects the numbers
          </span>
        )}
        {!field.editable && (
          <span className="badge muted">
            {field.widget === "dead" ? "read by nothing" : "locked"}
          </span>
        )}
        <span className="muted small">read by {field.reader}</span>
      </div>

      {/* The comment block from the file itself — the evidence for this value. */}
      {field.evidence.length > 0 && (
        <blockquote
          className="muted small"
          style={{
            margin: "8px 0",
            paddingLeft: 10,
            borderLeft: "3px solid #444",
            whiteSpace: "pre-wrap",
          }}
        >
          {field.evidence.join("\n")}
        </blockquote>
      )}

      {field.help && (
        <p className="muted small" style={{ marginTop: 4 }}>
          {field.help}
        </p>
      )}

      {!field.editable ? (
        <Locked field={field} />
      ) : canEdit ? (
        <Control field={field} canonicalFields={canonicalFields} onAdd={onAdd} />
      ) : (
        <ReadOnlyValue field={field} />
      )}
    </div>
  );
}

function Locked({ field }: { field: ConfigField }) {
  return (
    <div>
      <ReadOnlyValue field={field} />
      {field.locked_reason && (
        <p className="notice" style={{ marginTop: 8 }}>
          {field.locked_reason}
        </p>
      )}
    </div>
  );
}

function ReadOnlyValue({ field }: { field: ConfigField }) {
  const value = field.value;
  if (value === null || value === undefined) return <span className="muted">not set</span>;
  if (Array.isArray(value)) {
    return (
      <div className="mono small">
        {value.length === 0 ? <span className="muted">empty</span> : value.join(" · ")}
      </div>
    );
  }
  if (typeof value === "object") {
    return (
      <table>
        <tbody>
          {Object.entries(value as Record<string, unknown>).map(([k, v]) => (
            <tr key={k}>
              <td className="mono small">{k}</td>
              <td className="mono small">{String(v)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    );
  }
  return <span className="mono">{String(value)}</span>;
}

// ---------------------------------------------------------------------------
// The controls
// ---------------------------------------------------------------------------

function Control({
  field,
  canonicalFields,
  onAdd,
}: {
  field: ConfigField;
  canonicalFields: string[];
  onAdd: (edit: PendingEdit) => void;
}) {
  switch (field.widget) {
    case "bool":
      return <BoolControl field={field} onAdd={onAdd} />;
    case "enum":
      return <EnumControl field={field} onAdd={onAdd} />;
    case "money_vnd":
      return <ScalarControl field={field} onAdd={onAdd} money />;
    case "number":
      return <ScalarControl field={field} onAdd={onAdd} />;
    case "store_roster":
    case "string_list":
      return <ListControl field={field} onAdd={onAdd} />;
    case "alias_map":
    case "column_map":
      return (
        <MapControl
          field={field}
          onAdd={onAdd}
          rightOptions={field.widget === "column_map" ? canonicalFields : undefined}
        />
      );
    case "pattern":
      return <PatternControl field={field} onAdd={onAdd} />;
    case "date_bounds":
      return <ReadOnlyValue field={field} />;
    default:
      return <ScalarControl field={field} onAdd={onAdd} text />;
  }
}

function ScalarControl({
  field,
  onAdd,
  money = false,
  text = false,
}: {
  field: ConfigField;
  onAdd: (edit: PendingEdit) => void;
  money?: boolean;
  text?: boolean;
}) {
  const [draft, setDraft] = useState(String(field.value ?? ""));
  const changed = draft !== String(field.value ?? "");

  return (
    <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
      <input
        value={draft}
        onChange={(e) => setDraft(e.target.value)}
        inputMode={text ? undefined : "decimal"}
        aria-label={field.label}
        style={{ width: money ? 160 : 220 }}
      />
      {money && <span className="muted small">VND</span>}
      {money && Number.isFinite(Number(draft)) && draft !== "" && (
        <span className="muted small">{Number(draft).toLocaleString()}</span>
      )}
      <button
        type="button"
        disabled={!changed || draft.trim() === ""}
        onClick={() =>
          onAdd({
            op: "set",
            path: field.path,
            value: text ? draft : Number(draft),
            describe: `${field.label}: ${field.value} → ${draft}`,
          })
        }
      >
        Stage change
      </button>
    </div>
  );
}

function BoolControl({
  field,
  onAdd,
}: {
  field: ConfigField;
  onAdd: (edit: PendingEdit) => void;
}) {
  const current = Boolean(field.value);
  return (
    <div>
      <p className="small" style={{ margin: "4px 0" }}>
        Currently <strong>{current ? "on" : "off"}</strong> —{" "}
        {current ? field.on_means : field.off_means}
      </p>
      <p className="muted small" style={{ margin: "4px 0" }}>
        Turning it {current ? "off" : "on"} would mean:{" "}
        {current ? field.off_means : field.on_means}
      </p>
      <button
        type="button"
        onClick={() =>
          onAdd({
            op: "set",
            path: field.path,
            value: !current,
            describe: `${field.label}: ${current ? "on" : "off"} → ${!current ? "on" : "off"}`,
          })
        }
      >
        Turn it {current ? "off" : "on"}
      </button>
    </div>
  );
}

function EnumControl({
  field,
  onAdd,
}: {
  field: ConfigField;
  onAdd: (edit: PendingEdit) => void;
}) {
  const current = String(field.value ?? "");
  const options =
    field.options.length > 0
      ? field.options
      : // reader_engine and friends carry no declared option list; offer what the
        // pipeline understands rather than a free text box.
        [
          { value: "openpyxl", label: "openpyxl — the default reader" },
          { value: "calamine", label: "calamine — ignores a broken <dimension> tag, and much faster on Shopee" },
        ];

  return (
    <div>
      {options.map((option) => (
        <label key={option.value} style={{ display: "block", margin: "4px 0" }}>
          <input
            type="radio"
            name={field.dotted}
            defaultChecked={option.value === current}
            onChange={() =>
              onAdd({
                op: "set",
                path: field.path,
                value: option.value,
                describe: `${field.label}: ${current || "unset"} → ${option.value}`,
              })
            }
            style={{ width: 16, marginRight: 6 }}
          />
          {option.label}
        </label>
      ))}
    </div>
  );
}

function ListControl({
  field,
  onAdd,
}: {
  field: ConfigField;
  onAdd: (edit: PendingEdit) => void;
}) {
  const items = Array.isArray(field.value) ? (field.value as string[]) : [];
  const [draft, setDraft] = useState("");
  const [note, setNote] = useState("");

  return (
    <div>
      <table>
        <tbody>
          {items.length === 0 && (
            <tr>
              <td className="muted small">
                Nothing here yet. Adding the first entry creates the list.
              </td>
            </tr>
          )}
          {items.map((item) => (
            <tr key={item}>
              <td className="mono">{item}</td>
              <td style={{ width: 90 }}>
                <button
                  type="button"
                  className="secondary"
                  onClick={() =>
                    onAdd({
                      op: "remove_list_item",
                      path: field.path,
                      value: item,
                      describe: `${field.label}: remove ${item}`,
                    })
                  }
                >
                  ✕
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>

      <div style={{ display: "flex", gap: 8, marginTop: 8, flexWrap: "wrap" }}>
        <input
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          placeholder="add an entry"
          aria-label={`Add to ${field.label}`}
        />
        <input
          value={note}
          onChange={(e) => setNote(e.target.value)}
          placeholder="why (written into the file as a comment)"
          aria-label={`Reason for adding to ${field.label}`}
          style={{ flex: 1, minWidth: 240 }}
        />
        <button
          type="button"
          disabled={!draft.trim()}
          onClick={() => {
            onAdd({
              op: "append_list_item",
              path: field.path,
              value: draft.trim(),
              comment: note.trim() || undefined,
              describe: `${field.label}: add ${draft.trim()}`,
            });
            setDraft("");
            setNote("");
          }}
        >
          Add
        </button>
      </div>
    </div>
  );
}

function MapControl({
  field,
  onAdd,
  rightOptions,
}: {
  field: ConfigField;
  onAdd: (edit: PendingEdit) => void;
  rightOptions?: string[];
}) {
  const entries = Object.entries((field.value ?? {}) as Record<string, string>);
  const [left, setLeft] = useState("");
  const [right, setRight] = useState(rightOptions?.[0] ?? "");
  const [note, setNote] = useState("");

  const leftLabel = rightOptions ? "Header in the export" : "Name as it appears in the file";
  const rightLabel = rightOptions ? "What the pipeline calls it" : "Real store";

  return (
    <div>
      <table>
        <thead>
          <tr>
            <th>{leftLabel}</th>
            <th>{rightLabel}</th>
            <th style={{ width: 90 }} />
          </tr>
        </thead>
        <tbody>
          {entries.length === 0 && (
            <tr>
              <td colSpan={3} className="muted small">
                Nothing mapped yet.
              </td>
            </tr>
          )}
          {entries.map(([k, v]) => (
            <tr key={k}>
              <td className="mono small">{k}</td>
              <td className="mono small">
                {v === "TODO-HUMAN" ? (
                  <span className="badge variance" title="Somebody still has to decide what this maps to">
                    undecided
                  </span>
                ) : (
                  v
                )}
              </td>
              <td>
                <button
                  type="button"
                  className="secondary"
                  onClick={() =>
                    onAdd({
                      op: "remove_map_entry",
                      path: field.path,
                      key: k,
                      describe: `${field.label}: remove ${k}`,
                    })
                  }
                >
                  ✕
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>

      <div style={{ display: "flex", gap: 8, marginTop: 8, flexWrap: "wrap" }}>
        <input
          value={left}
          onChange={(e) => setLeft(e.target.value)}
          placeholder={leftLabel.toLowerCase()}
          aria-label={leftLabel}
          style={{ minWidth: 220 }}
        />
        {rightOptions ? (
          <select value={right} onChange={(e) => setRight(e.target.value)} aria-label={rightLabel}>
            {rightOptions.map((option) => (
              <option key={option} value={option}>
                {option}
              </option>
            ))}
          </select>
        ) : (
          <input
            value={right}
            onChange={(e) => setRight(e.target.value)}
            placeholder={rightLabel.toLowerCase()}
            aria-label={rightLabel}
          />
        )}
        <input
          value={note}
          onChange={(e) => setNote(e.target.value)}
          placeholder="why (written into the file as a comment)"
          aria-label={`Reason for mapping ${left}`}
          style={{ flex: 1, minWidth: 200 }}
        />
        <button
          type="button"
          disabled={!left.trim() || !right.trim()}
          onClick={() => {
            onAdd({
              op: "set_map_entry",
              path: field.path,
              key: left.trim(),
              value: right.trim(),
              comment: note.trim() || undefined,
              describe: `${field.label}: ${left.trim()} → ${right.trim()}`,
            });
            setLeft("");
            setNote("");
          }}
        >
          Add mapping
        </button>
      </div>
      {rightOptions && (
        <p className="muted small">
          When a header drifts, add the new spelling as a <strong>parallel</strong> entry
          rather than replacing the old one — older windows still re-run.
        </p>
      )}
    </div>
  );
}

/**
 * A regex plus a live tester, pre-seeded with the real filenames from the field's
 * own comment block. A pattern with no way to check it is how a store name gets
 * truncated and two storefronts invoice as one.
 */
function PatternControl({
  field,
  onAdd,
}: {
  field: ConfigField;
  onAdd: (edit: PendingEdit) => void;
}) {
  const [draft, setDraft] = useState(String(field.value ?? ""));
  const seeds = useMemo(() => {
    const found = new Set<string>();
    for (const line of field.evidence) {
      for (const match of line.matchAll(/"([^"]*\.xlsx)"/g)) found.add(match[1]);
    }
    return [...found].slice(0, 6);
  }, [field.evidence]);
  const [sample, setSample] = useState(seeds[0] ?? "");

  const resolved = useMemo(() => {
    if (!sample) return null;
    try {
      const match = new RegExp(draft, "i").exec(sample);
      return match?.[1]?.trim() || "(no store found)";
    } catch (error) {
      return `(the pattern itself is invalid: ${(error as Error).message})`;
    }
  }, [draft, sample]);

  return (
    <div>
      <textarea
        value={draft}
        onChange={(e) => setDraft(e.target.value)}
        rows={2}
        aria-label={field.label}
        className="mono"
        style={{ width: "100%", fontSize: 12 }}
      />
      <div style={{ display: "flex", gap: 8, marginTop: 6, flexWrap: "wrap" }}>
        <input
          value={sample}
          onChange={(e) => setSample(e.target.value)}
          placeholder="paste a real export filename"
          aria-label="Filename to test"
          style={{ flex: 1, minWidth: 260 }}
        />
        <span className="small">
          resolves to <strong className="mono">{resolved ?? "—"}</strong>
        </span>
      </div>
      {seeds.length > 0 && (
        <p className="muted small">
          Try:{" "}
          {seeds.map((seed) => (
            <button
              key={seed}
              type="button"
              className="secondary"
              onClick={() => setSample(seed)}
              style={{ marginRight: 4, fontSize: 11 }}
            >
              {seed}
            </button>
          ))}
        </p>
      )}
      <button
        type="button"
        disabled={draft === String(field.value ?? "")}
        onClick={() =>
          onAdd({
            op: "set",
            path: field.path,
            value: draft,
            describe: `${field.label}: pattern changed`,
          })
        }
      >
        Stage change
      </button>
    </div>
  );
}
