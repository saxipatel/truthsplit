import { useState } from "react";

import { api } from "../../api/client";
import type { Investigation, Simulation } from "../../api/types";
import { DumbbellChart, SimulationChart } from "../../components/charts/charts";
import { Amount, Button, Card, ErrorMessage, Field, inputClass, Loading, Notice, SignedAmount, StatTile, Table, td, tdNum, th } from "../../components/ui";
import { add } from "../../lib/decimal";
import { formatAmount, formatCount, formatDateTime, humanize } from "../../lib/format";
import { useAction, useApi } from "../../lib/useApi";

type Rule = "remove_duplicates" | "align_window" | "refund_policy" | "align_definition" | "adjust_mapping";

const RULES: { id: Rule; label: string; help: string }[] = [
  { id: "remove_duplicates", label: "Remove duplicates", help: "Drop exact duplicate rows, or join copies (same amount repeated on otherwise different rows)." },
  { id: "align_window", label: "Align date window", help: "Give one side the other side's date window, time zone and end bound." },
  { id: "refund_policy", label: "Change refund treatment", help: "Switch one side to gross, net of refunds, or excluding refunded rows." },
  { id: "align_definition", label: "Use the other side's definition", help: "Evaluate one side with the other side's whole metric definition." },
  { id: "adjust_mapping", label: "Re-map columns", help: "Re-read a file dataset with a different column for the amount, refund or key." },
];

function describe(sim: Simulation): string {
  const p = sim.parameters_json as Record<string, unknown>;
  const parts = [humanize(sim.rule_type), String(p.side ?? "")];
  if (p.kind) parts.push(String(p.kind).replace("_", " "));
  if (p.policy) parts.push(`→ ${String(p.policy).replace(/_/g, " ")}`);
  return parts.filter(Boolean).join(" · ");
}

function Result({ sim }: { sim: Simulation }) {
  const base = sim.result_json.baseline.findings.filter((f) => f.amount !== null);
  const after = sim.result_json.simulated.findings.filter((f) => f.amount !== null);
  const rules = [...new Set([...base.map((f) => f.rule_id), ...after.map((f) => f.rule_id)])];
  const amountFor = (list: typeof base, rule: string) => {
    const hits = list.filter((f) => f.rule_id === rule);
    return hits.length ? hits.map((f) => f.amount!).reduce((a, b) => add(a, b)) : null;
  };
  const items = rules.map((r) => ({ label: humanize(r), baseline: amountFor(base, r), simulated: amountFor(after, r) }));
  const remap = sim.result_json.remap;
  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 gap-2 md:grid-cols-4">
        <StatTile label="Difference before" value={<SignedAmount value={sim.before_delta} />} />
        <StatTile label="Difference after" value={<SignedAmount value={sim.after_delta} />} emphasis />
        <StatTile label="Gap closed" value={<SignedAmount value={sim.resolved_amount} />} note="|before| − |after|; negative widens the gap" />
        <StatTile label="Affected rows" value={formatCount(sim.affected_rows)} note={`Unexplained after: ${formatAmount(sim.result_json.simulated.unexplained)}`} />
      </div>
      <Table label="Simulated totals">
        <thead>
          <tr>
            <th className={th}></th>
            <th className={th}>Left</th>
            <th className={th}>Right</th>
            <th className={th}>Difference</th>
            <th className={th}>Unexplained</th>
          </tr>
        </thead>
        <tbody>
          <tr>
            <td className={td}>Original</td>
            <td className={tdNum}>{formatAmount(sim.before_left)}</td>
            <td className={tdNum}>{formatAmount(sim.before_right)}</td>
            <td className={tdNum}>{formatAmount(sim.before_delta, { signed: true })}</td>
            <td className={tdNum}>{formatAmount(sim.result_json.baseline.unexplained, { signed: true })}</td>
          </tr>
          <tr>
            <td className={td}>Simulated</td>
            <td className={tdNum}>{formatAmount(sim.after_left)}</td>
            <td className={tdNum}>{formatAmount(sim.after_right)}</td>
            <td className={tdNum}>{formatAmount(sim.after_delta, { signed: true })}</td>
            <td className={tdNum}>{formatAmount(sim.result_json.simulated.unexplained, { signed: true })}</td>
          </tr>
        </tbody>
      </Table>
      {remap && (
        <Notice tone={remap.rows_rejected ? "warning" : "info"}>
          Re-mapped file: {formatCount(remap.rows_read)} rows read, {formatCount(remap.rows_accepted)} accepted, {formatCount(remap.rows_rejected)} rejected
          {remap.issues[0] ? ` (first: row ${remap.issues[0].row_number}, ${remap.issues[0].column_name}: ${remap.issues[0].message})` : ""}.
        </Notice>
      )}
      {items.length > 0 && <SimulationChart items={items} />}
      <Table label="Attributed amount per rule, baseline and simulated">
        <thead>
          <tr>
            <th className={th}>Rule</th>
            <th className={th}>Baseline</th>
            <th className={th}>Simulated</th>
          </tr>
        </thead>
        <tbody>
          {items.map((i) => (
            <tr key={i.label}>
              <td className={td}>{i.label}</td>
              <td className={tdNum}>{formatAmount(i.baseline, { signed: true })}</td>
              <td className={tdNum}>{formatAmount(i.simulated, { signed: true })}</td>
            </tr>
          ))}
        </tbody>
      </Table>
      {sim.result_json.sides.map((s) =>
        s.samples.length ? (
          <details key={s.side} className="text-sm">
            <summary className="cursor-pointer text-slate-600">
              {s.side === "left" ? "Left" : "Right"}: {formatCount(s.changed_contributions)} records changed ({formatCount(s.records_before)} → {formatCount(s.records_after)} contributing)
            </summary>
            <div className="mt-2">
              <Table label={`Changed ${s.side} records`}>
                <thead>
                  <tr>
                    <th className={th}>Row</th>
                    <th className={th}>Business key</th>
                    <th className={th}>Before</th>
                    <th className={th}>After</th>
                  </tr>
                </thead>
                <tbody>
                  {s.samples.slice(0, 50).map((r) => (
                    <tr key={r.row_number}>
                      <td className={tdNum}>{r.row_number}</td>
                      <td className={td}>{r.business_key}</td>
                      <td className={tdNum}>{formatAmount(r.before)}</td>
                      <td className={tdNum}>{r.after === null ? "removed" : formatAmount(r.after)}</td>
                    </tr>
                  ))}
                </tbody>
              </Table>
            </div>
          </details>
        ) : null,
      )}
    </div>
  );
}

export default function Simulator({ inv }: { inv: Investigation }) {
  const history = useApi(() => api.simulations(inv.id), [inv.id]);
  const [rule, setRule] = useState<Rule>("remove_duplicates");
  const [side, setSide] = useState<"left" | "right" | "both">("right");
  const [kind, setKind] = useState<"exact" | "join_copies">("exact");
  const [policy, setPolicy] = useState("gross");
  const [mappingText, setMappingText] = useState("");
  const [selected, setSelected] = useState<Simulation>();
  const [validation, setValidation] = useState<string>();
  const run = useAction(api.simulate);

  const fileSide = useApi(async () => {
    const out: Record<string, { source_type: string; mapping: unknown }> = {};
    for (const i of inv.inputs) {
      const d = await api.dataset(i.dataset_id);
      out[i.side] = { source_type: d.source_type, mapping: d.column_mapping };
    }
    return out;
  }, [inv.id]);

  const prepareMapping = (s: "left" | "right") => setMappingText(JSON.stringify(fileSide.data?.[s]?.mapping ?? {}, null, 2));

  const submit = async () => {
    const body: Record<string, unknown> = { rule_type: rule };
    if (rule === "remove_duplicates") Object.assign(body, { side, kind });
    else {
      if (side === "both") return setValidation("Choose the left or the right side for this correction.");
      body.side = side;
      if (rule === "refund_policy") body.policy = policy;
      if (rule === "adjust_mapping") {
        if (fileSide.data?.[side]?.source_type === "postgres_query") return setValidation("Query snapshots keep their registered mapping; choose a file dataset side.");
        try {
          body.mapping = JSON.parse(mappingText);
        } catch {
          return setValidation("The mapping must be valid JSON.");
        }
      }
    }
    setValidation(undefined);
    const sim = await run.run(inv.id, body);
    if (sim) {
      setSelected(sim);
      history.reload();
    }
  };

  return (
    <div className="space-y-4">
      <Notice>
        Simulations run on in-memory copies of this investigation's stored snapshots. They never change the source data, the datasets or the stored findings; only the simulation itself is saved.
      </Notice>
      <Card title="Simulate a correction">
        <div className="grid grid-cols-1 gap-3 md:grid-cols-3">
          <Field label="Correction" htmlFor="sim-rule" hint={RULES.find((r) => r.id === rule)?.help}>
            <select
              id="sim-rule"
              className={inputClass}
              value={rule}
              onChange={(e) => {
                const r = e.target.value as Rule;
                setRule(r);
                if (r !== "remove_duplicates" && side === "both") setSide("right");
                if (r === "adjust_mapping") prepareMapping(side === "left" ? "left" : "right");
              }}
            >
              {RULES.map((r) => (
                <option key={r.id} value={r.id}>
                  {r.label}
                </option>
              ))}
            </select>
          </Field>
          <Field label="Side" htmlFor="sim-side">
            <select
              id="sim-side"
              className={inputClass}
              value={side}
              onChange={(e) => {
                const s = e.target.value as typeof side;
                setSide(s);
                if (rule === "adjust_mapping" && s !== "both") prepareMapping(s);
              }}
            >
              <option value="left">Left</option>
              <option value="right">Right</option>
              {rule === "remove_duplicates" && <option value="both">Both</option>}
            </select>
          </Field>
          {rule === "remove_duplicates" && (
            <Field label="Which rows" htmlFor="sim-kind">
              <select id="sim-kind" className={inputClass} value={kind} onChange={(e) => setKind(e.target.value as typeof kind)}>
                <option value="exact">Exact duplicates</option>
                <option value="join_copies">Join copies</option>
              </select>
            </Field>
          )}
          {rule === "refund_policy" && (
            <Field label="New refund treatment" htmlFor="sim-policy">
              <select id="sim-policy" className={inputClass} value={policy} onChange={(e) => setPolicy(e.target.value)}>
                <option value="gross">Gross</option>
                <option value="net_of_refunds">Net of refunds</option>
                <option value="exclude_refunded">Exclude refunded rows</option>
              </select>
            </Field>
          )}
        </div>
        {rule === "adjust_mapping" && (
          <div className="mt-3">
            <Field label="Column mapping (JSON)" htmlFor="sim-mapping" hint="Starts from the dataset's current mapping; change e.g. the amount column.">
              <textarea id="sim-mapping" rows={8} className={`${inputClass} font-mono text-xs`} value={mappingText} onChange={(e) => setMappingText(e.target.value)} />
            </Field>
          </div>
        )}
        <div className="mt-3 flex flex-wrap gap-2">
          <Button variant="primary" pending={run.pending} onClick={submit}>
            Run simulation
          </Button>
        </div>
        <div className="mt-3 space-y-2">
          <ErrorMessage error={validation} title="Check the correction" />
          <ErrorMessage error={run.error} title="Simulation refused" />
        </div>
      </Card>

      {selected && (
        <Card title={`Result: ${describe(selected)}`}>
          <Result sim={selected} />
        </Card>
      )}

      {history.data && history.data.length > 0 && (
        <Card title="Saved simulations: difference before and after">
          <DumbbellChart
            items={history.data.map((sim, i) => ({
              label: `${i + 1}. ${describe(sim)}`,
              title: `${i + 1}. ${describe(sim)}`,
              before: sim.before_delta,
              after: sim.after_delta,
            }))}
          />
        </Card>
      )}

      <Card title="Saved simulations">
        {history.error && <ErrorMessage error={history.error} />}
        {!history.data && !history.error && <Loading />}
        {history.data && history.data.length === 0 && <p className="text-sm text-slate-500">No simulations yet.</p>}
        {history.data && history.data.length > 0 && (
          <Table label="Saved simulations">
            <thead>
              <tr>
                <th className={th}>#</th>
                <th className={th}>When</th>
                <th className={th}>Correction</th>
                <th className={th}>Difference before</th>
                <th className={th}>After</th>
                <th className={th}>Gap closed</th>
                <th className={th}>Rows</th>
                <th className={th}></th>
              </tr>
            </thead>
            <tbody>
              {history.data.map((s, i) => (
                <tr key={s.id} className={selected?.id === s.id ? "bg-blue-50" : ""}>
                  <td className={tdNum}>{i + 1}</td>
                  <td className={td}>{formatDateTime(s.created_at)}</td>
                  <td className={td}>{describe(s)}</td>
                  <td className={tdNum}>
                    <Amount value={s.before_delta} signed />
                  </td>
                  <td className={tdNum}>
                    <Amount value={s.after_delta} signed />
                  </td>
                  <td className={tdNum}>
                    <SignedAmount value={s.resolved_amount} />
                  </td>
                  <td className={tdNum}>{formatCount(s.affected_rows)}</td>
                  <td className={td}>
                    <Button variant="ghost" onClick={() => setSelected(s)}>
                      View
                    </Button>
                  </td>
                </tr>
              ))}
            </tbody>
          </Table>
        )}
      </Card>
    </div>
  );
}
