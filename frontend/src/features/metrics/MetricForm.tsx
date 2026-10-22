import { useState } from "react";

import { api } from "../../api/client";
import type { MetricDefinition, MetricFilter, MetricSpec } from "../../api/types";
import { Button, ErrorMessage, Field, inputClass } from "../../components/ui";
import { useAction } from "../../lib/useApi";

const FIELDS = ["status", "currency", "business_key", "source_record_id", "amount", "refund_amount"];
const OPS = ["eq", "ne", "in", "not_in", "is_null", "not_null", "gt", "gte", "lt", "lte"];

const defaults: MetricSpec = {
  aggregation: "sum",
  value_field: "amount",
  grain: "record",
  filters: [],
  window_start: null,
  window_end: null,
  window_end_inclusive: false,
  timezone: "UTC",
  refund_policy: "gross",
  null_policy: "exclude",
  currency: null,
};

type FilterDraft = { field: string; op: string; value: string };

function toFilter(f: FilterDraft): MetricFilter {
  if (f.op === "is_null" || f.op === "not_null") return { field: f.field, op: f.op };
  if (f.op === "in" || f.op === "not_in") return { field: f.field, op: f.op, value: f.value.split(",").map((v) => v.trim()).filter(Boolean) };
  return { field: f.field, op: f.op, value: f.value };
}

export default function MetricForm({ onCreated, idPrefix = "m", defaultName = "" }: { onCreated: (m: MetricDefinition) => void; idPrefix?: string; defaultName?: string }) {
  const [name, setName] = useState(defaultName);
  const [description, setDescription] = useState("");
  const [spec, setSpec] = useState<MetricSpec>(defaults);
  const [start, setStart] = useState("");
  const [end, setEnd] = useState("");
  const [filters, setFilters] = useState<FilterDraft[]>([]);
  const [validation, setValidation] = useState<string>();
  const create = useAction(api.createMetric);

  const set = (patch: Partial<MetricSpec>) => setSpec((s) => ({ ...s, ...patch }));
  const id = (s: string) => `${idPrefix}-${s}`;

  const submit = async () => {
    if (!name.trim()) return setValidation("Name is required.");
    if (start && end && end <= start) return setValidation("Window end must be after the start.");
    if (spec.refund_policy === "net_of_refunds" && spec.aggregation !== "sum") return setValidation("Net of refunds only applies to sums.");
    for (const f of filters) {
      if (!["is_null", "not_null"].includes(f.op) && !f.value.trim()) return setValidation(`Filter on ${f.field} needs a value.`);
    }
    setValidation(undefined);
    const created = await create.run({
      ...spec,
      name: name.trim(),
      description: description.trim() || null,
      window_start: start ? `${start}T00:00:00` : null,
      window_end: end ? `${end}T00:00:00` : null,
      currency: spec.currency || null,
      filters: filters.map(toFilter),
    });
    if (created) onCreated(created);
  };

  return (
    <div className="space-y-3">
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        <Field label="Name" htmlFor={id("name")} hint="Saving an existing name creates its next version">
          <input id={id("name")} className={inputClass} value={name} maxLength={120} required onChange={(e) => setName(e.target.value)} />
        </Field>
        <Field label="Description" htmlFor={id("desc")}>
          <input id={id("desc")} className={inputClass} value={description} onChange={(e) => setDescription(e.target.value)} />
        </Field>
        <Field label="Aggregation" htmlFor={id("agg")}>
          <select id={id("agg")} className={inputClass} value={spec.aggregation} onChange={(e) => set({ aggregation: e.target.value as MetricSpec["aggregation"] })}>
            <option value="sum">Sum</option>
            <option value="count">Count of rows</option>
            <option value="count_distinct">Count of distinct business keys</option>
          </select>
        </Field>
        <Field label="Refund treatment" htmlFor={id("refund")}>
          <select id={id("refund")} className={inputClass} value={spec.refund_policy} onChange={(e) => set({ refund_policy: e.target.value as MetricSpec["refund_policy"] })}>
            <option value="gross">Gross (ignore refunds)</option>
            <option value="net_of_refunds">Net of refunds</option>
            <option value="exclude_refunded">Exclude refunded rows</option>
          </select>
        </Field>
        <Field label="Grain" htmlFor={id("grain")}>
          <select id={id("grain")} className={inputClass} value={spec.grain} onChange={(e) => set({ grain: e.target.value as MetricSpec["grain"] })}>
            <option value="record">Every row</option>
            <option value="business_key">One row per business key</option>
          </select>
        </Field>
        <Field label="Missing values" htmlFor={id("null")}>
          <select id={id("null")} className={inputClass} value={spec.null_policy} onChange={(e) => set({ null_policy: e.target.value as MetricSpec["null_policy"] })}>
            <option value="exclude">Exclude rows with no value</option>
            <option value="reject">Fail if any value is missing</option>
          </select>
        </Field>
        <Field label="Window start (local date, inclusive)" htmlFor={id("start")}>
          <input id={id("start")} type="date" className={inputClass} value={start} onChange={(e) => setStart(e.target.value)} />
        </Field>
        <Field label="Window end (local date)" htmlFor={id("end")}>
          <input id={id("end")} type="date" className={inputClass} value={end} onChange={(e) => setEnd(e.target.value)} />
        </Field>
        <Field label="Window time zone" htmlFor={id("tz")}>
          <input id={id("tz")} className={inputClass} value={spec.timezone} onChange={(e) => set({ timezone: e.target.value })} />
        </Field>
        <Field label="Currency (optional)" htmlFor={id("ccy")} hint="Rows in other currencies make the evaluation fail">
          <input id={id("ccy")} className={inputClass} maxLength={3} value={spec.currency ?? ""} onChange={(e) => set({ currency: e.target.value.toUpperCase() || null })} />
        </Field>
      </div>
      <label className="flex items-center gap-2 text-sm">
        <input type="checkbox" checked={spec.window_end_inclusive} onChange={(e) => set({ window_end_inclusive: e.target.checked })} />
        Include the window end instant
      </label>
      <div>
        <p className="mb-1 text-xs font-medium text-slate-700">Filters (all must match; a comparison with an empty field never matches)</p>
        {filters.map((f, i) => (
          <div key={i} className="mb-2 grid grid-cols-1 gap-2 sm:grid-cols-[1fr_8rem_1fr_auto]">
            <input
              aria-label={`Filter ${i + 1} field`}
              className={inputClass}
              list={id("fields")}
              value={f.field}
              onChange={(e) => setFilters((fs) => fs.map((x, j) => (j === i ? { ...x, field: e.target.value } : x)))}
            />
            <select aria-label={`Filter ${i + 1} operator`} className={inputClass} value={f.op} onChange={(e) => setFilters((fs) => fs.map((x, j) => (j === i ? { ...x, op: e.target.value } : x)))}>
              {OPS.map((o) => (
                <option key={o}>{o}</option>
              ))}
            </select>
            <input
              aria-label={`Filter ${i + 1} value`}
              className={inputClass}
              placeholder={f.op === "in" || f.op === "not_in" ? "comma-separated" : "value"}
              disabled={f.op === "is_null" || f.op === "not_null"}
              value={f.value}
              onChange={(e) => setFilters((fs) => fs.map((x, j) => (j === i ? { ...x, value: e.target.value } : x)))}
            />
            <Button variant="ghost" onClick={() => setFilters((fs) => fs.filter((_, j) => j !== i))}>
              Remove
            </Button>
          </div>
        ))}
        <datalist id={id("fields")}>
          {FIELDS.map((f) => (
            <option key={f} value={f} />
          ))}
          <option value="attributes.region" />
        </datalist>
        <Button variant="ghost" onClick={() => setFilters((fs) => [...fs, { field: "status", op: "eq", value: "" }])}>
          + Add filter
        </Button>
      </div>
      <Button variant="primary" pending={create.pending} onClick={submit}>
        Save definition
      </Button>
      <ErrorMessage error={validation} title="Check the definition" />
      <ErrorMessage error={create.error} title="Could not save" />
    </div>
  );
}
