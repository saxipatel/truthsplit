import { useState } from "react";

import { api, links } from "../../api/client";
import type { Finding, Investigation } from "../../api/types";
import { WaterfallChart, waterfallRows } from "../../components/charts/charts";
import { add, equal, sign } from "../../lib/decimal";
import Calculation from "./Calculation";
import { Amount, Button, Card, EmptyState, ErrorMessage, EvidenceBadge, LinkButton, Loading, SignedAmount, Table, td, tdNum, th } from "../../components/ui";
import { formatAmount, formatCount, humanize, shortHash } from "../../lib/format";
import { useApi } from "../../lib/useApi";

const PAGE = 25;

// Axis labels must tell apart two findings from the same rule (e.g. left-only and right-only cutoffs).
function shortLabel(f: Finding): string {
  const text = `${f.step_order}. ${f.title}`;
  return text.length > 34 ? `${text.slice(0, 33)}…` : text;
}

function rows(detail: Record<string, unknown>, side: "left" | "right"): string {
  const list = detail[`${side}_rows`] as { row_number: number }[] | undefined;
  if (list) return list.map((r) => r.row_number).join(", ");
  if (side === "left" && typeof detail.row_number === "number") return String(detail.row_number);
  return "";
}

function EvidenceTable({ inv, finding }: { inv: Investigation; finding: Finding }) {
  const [offset, setOffset] = useState(0);
  const page = useApi(() => api.evidence(inv.id, finding.id, offset, PAGE), [inv.id, finding.id, offset]);
  const definitionStep = finding.rule_id.startsWith("definition.");
  if (page.error) return <ErrorMessage error={page.error} />;
  if (!page.data) return <Loading label="Loading evidence" />;
  const { total, items } = page.data;
  if (!total) return <p className="text-sm text-slate-500">This finding has no record-level evidence rows.</p>;
  return (
    <div className={page.loading ? "opacity-60" : ""}>
      <Table label={`Evidence for ${finding.title}`}>
        <thead>
          <tr>
            <th className={th}>Side</th>
            <th className={th}>Business key</th>
            <th className={th}>{definitionStep ? "Before" : "Left"}</th>
            <th className={th}>{definitionStep ? "After" : "Right"}</th>
            <th className={th}>Contribution</th>
            <th className={th}>Source rows</th>
          </tr>
        </thead>
        <tbody>
          {items.map((e, i) => (
            <tr key={`${e.business_key}-${i}`}>
              <td className={td}>{e.side}</td>
              <td className={`${td} max-w-64 break-all whitespace-normal`}>{e.business_key ?? <span className="text-slate-400">no key</span>}</td>
              <td className={tdNum}>{formatAmount(e.left_amount)}</td>
              <td className={tdNum}>{formatAmount(e.right_amount)}</td>
              <td className={tdNum}>{e.contribution === null ? <span className="text-slate-400">not attributed</span> : formatAmount(e.contribution, { signed: true })}</td>
              <td className={`${td} text-xs text-slate-600`}>
                {definitionStep ? `row ${String(e.detail.row_number ?? "")}` : [rows(e.detail, "left") && `L: ${rows(e.detail, "left")}`, rows(e.detail, "right") && `R: ${rows(e.detail, "right")}`].filter(Boolean).join(" · ")}
                {e.detail.other_side ? <span className="block text-slate-500">other side: {String(e.detail.other_side)}</span> : null}
              </td>
            </tr>
          ))}
        </tbody>
      </Table>
      <div className="mt-2 flex flex-wrap items-center gap-2 text-xs text-slate-600">
        <span>
          {formatCount(offset + 1)}–{formatCount(Math.min(offset + PAGE, total))} of {formatCount(total)}
        </span>
        <Button variant="ghost" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE))}>
          Previous
        </Button>
        <Button variant="ghost" disabled={offset + PAGE >= total} onClick={() => setOffset(offset + PAGE)}>
          Next
        </Button>
      </div>
    </div>
  );
}

function FindingCard({ inv, finding }: { inv: Investigation; finding: Finding }) {
  const [open, setOpen] = useState(false);
  return (
    <article className="rounded-md border border-slate-200 bg-white">
      <button type="button" aria-expanded={open} onClick={() => setOpen(!open)} className="flex w-full flex-wrap items-start justify-between gap-2 px-4 py-3 text-left hover:bg-slate-50">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            {finding.step_order !== null && <span className="text-xs text-slate-500">#{finding.step_order}</span>}
            <h3 className="text-sm font-semibold">{finding.title}</h3>
            <EvidenceBadge status={finding.evidence_status} />
          </div>
          <p className="mt-0.5 text-xs text-slate-500">
            {humanize(finding.type)} · rule <code>{finding.rule_id}</code>
            {finding.affected_row_count > 0 && <> · {formatCount(finding.affected_row_count)} rows</>}
          </p>
        </div>
        <div className="text-right text-sm font-semibold">
          {finding.amount_impact === null ? <span className="text-xs font-normal text-slate-500">no amount attributed</span> : <SignedAmount value={finding.amount_impact} />}
          <span className="block text-xs font-normal text-slate-500">{open ? "Hide evidence" : "Show evidence"}</span>
        </div>
      </button>
      {open && (
        <div className="space-y-3 border-t border-slate-100 px-4 py-3 text-sm">
          <p>{finding.description}</p>
          <p className="text-slate-600">
            <span className="font-medium text-slate-800">Why {finding.evidence_status}:</span> {finding.classification_reason}
          </p>
          {finding.assumptions_json.length > 0 && (
            <p className="text-slate-600">
              <span className="font-medium text-slate-800">Assumptions:</span> {finding.assumptions_json.join(" ")}
            </p>
          )}
          {finding.overlap_note && (
            <p className="rounded border border-amber-300 bg-amber-50 px-2 py-1.5 text-amber-900">
              <span className="font-medium">Overlap:</span> {finding.overlap_note}
            </p>
          )}
          <div>
            <p className="mb-1 font-medium text-slate-800">Calculation</p>
            <Calculation calculation={finding.calculation_json} />
            <p className="mt-1 text-xs text-slate-500">Proof hash {shortHash(finding.proof_hash, 16)}</p>
          </div>
          <p className="font-medium text-slate-800">Supporting records</p>
          <EvidenceTable inv={inv} finding={finding} />
        </div>
      )}
    </article>
  );
}

const GROUPS = [
  { status: "confirmed" as const, title: "Confirmed", text: "Recomputed exactly from the listed records under the stated assumptions. These amounts are attributed." },
  { status: "suspected" as const, title: "Suspected", text: "A pattern fits but the evidence to confirm it is missing. No amount is attributed; the difference stays with the other findings." },
  { status: "unexplained" as const, title: "Unexplained", text: "The part of the difference the available data cannot attribute." },
];

function indicative(f: Finding): string | null {
  const c = f.calculation_json as Record<string, unknown>;
  const v = c.indicative_amount ?? c.excess;
  return typeof v === "string" ? v : null;
}

export default function Autopsy({ inv }: { inv: Investigation }) {
  if (inv.findings.length === 0) {
    return <EmptyState title="No differences found">Both metrics are equal and every record matches; there is nothing to attribute.</EmptyState>;
  }
  const confirmed = inv.findings.filter((f) => f.evidence_status === "confirmed" && f.amount_impact !== null);
  const steps = [
    ...confirmed.map((f) => ({ label: shortLabel(f), title: f.title, amount: f.amount_impact!, kind: "cause" as const, status: "confirmed" })),
    ...(sign(inv.unexplained_delta) !== 0 ? [{ label: "Unexplained remainder", title: "Unexplained remainder", amount: inv.unexplained_delta!, kind: "unexplained" as const, status: "unexplained" }] : []),
  ];
  const rows = waterfallRows(steps, "Difference (right − left)");
  const reconciles = equal(rows[rows.length - 1].end, inv.baseline_delta);

  return (
    <div className="space-y-4">
      <Card title="Reconciliation waterfall" actions={<LinkButton href={links.evidenceCsv(inv.id)}>Download evidence CSV</LinkButton>}>
        <p className="mb-3 text-sm text-slate-600">
          How the difference of <Amount value={inv.baseline_delta} signed /> (right − left) is made up: each verified cause in attribution order, then the unexplained remainder. The bars end exactly at the difference.
        </p>
        {!reconciles && (
          <ErrorMessage title="Totals do not reconcile" error={`Attributed amounts sum to ${formatAmount(rows[rows.length - 1].end, { signed: true })}, not ${formatAmount(inv.baseline_delta, { signed: true })}.`} />
        )}
        <WaterfallChart rows={rows} />
        <div className="mt-4">
          <Table label="Waterfall steps">
            <thead>
              <tr>
                <th className={th}>Step</th>
                <th className={th}>Evidence</th>
                <th className={th}>Running total before</th>
                <th className={th}>Change</th>
                <th className={th}>Running total after</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.label} className={r.kind === "total" ? "font-semibold" : ""}>
                  <td className={`${td} max-w-md whitespace-normal`}>{r.title}</td>
                  <td className={td}>{r.status ? <EvidenceBadge status={r.status as "confirmed" | "unexplained"} /> : null}</td>
                  <td className={tdNum}>{r.kind === "total" ? "" : formatAmount(r.start, { signed: true })}</td>
                  <td className={tdNum}>{r.kind === "total" ? "" : formatAmount(r.change, { signed: true })}</td>
                  <td className={tdNum}>{formatAmount(r.end, { signed: true })}</td>
                </tr>
              ))}
            </tbody>
          </Table>
        </div>
      </Card>

      <Card title="Evidence breakdown">
        <div className="grid grid-cols-1 gap-2 md:grid-cols-3">
          {GROUPS.map((g) => {
            const list = inv.findings.filter((f) => f.evidence_status === g.status);
            const total = add(...list.map((f) => f.amount_impact));
            return (
              <div key={g.status} role="group" aria-label={`${g.title} findings`} className="rounded-md border border-slate-200 p-3">
                <div className="flex items-center justify-between gap-2">
                  <EvidenceBadge status={g.status} />
                  <span className="text-xs text-slate-500">
                    {list.length} finding{list.length === 1 ? "" : "s"}
                  </span>
                </div>
                <p className="mt-2 text-lg font-semibold">{g.status === "suspected" ? "Not attributed" : <SignedAmount value={total} />}</p>
                <p className="mt-1 text-xs text-slate-500">{g.text}</p>
              </div>
            );
          })}
        </div>
        <p className="mt-2 text-xs text-slate-500">
          Confirmed <Amount value={inv.explained_delta} signed /> + unexplained <Amount value={inv.unexplained_delta} signed /> = difference <Amount value={inv.baseline_delta} signed />.
        </p>
      </Card>

      {GROUPS.map((g) => {
        const list = inv.findings.filter((f) => f.evidence_status === g.status);
        if (!list.length) return null;
        return (
          <section key={g.status} aria-labelledby={`group-${g.status}`} className="space-y-2">
            <h2 id={`group-${g.status}`} className="flex items-center gap-2 text-sm font-semibold">
              {g.title} findings <span className="font-normal text-slate-500">({list.length})</span>
            </h2>
            {g.status === "suspected" && list.some(indicative) && (
              <p className="text-xs text-slate-500">Indicative amounts shown in their calculations are not part of any total.</p>
            )}
            {list.map((f) => (
              <FindingCard key={f.id} inv={inv} finding={f} />
            ))}
          </section>
        );
      })}
    </div>
  );
}
