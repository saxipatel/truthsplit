import { links } from "../../api/client";
import type { Investigation } from "../../api/types";
import { Card, LinkButton } from "../../components/ui";

export default function Report({ inv }: { inv: Investigation }) {
  return (
    <div className="space-y-4">
      <Card
        title="Investigation report"
        actions={
          <div className="flex flex-wrap gap-2">
            <LinkButton href={links.report(inv.id, "html", true)}>Download HTML</LinkButton>
            <LinkButton href={links.report(inv.id, "json", true)}>Download JSON</LinkButton>
            <LinkButton href={links.evidenceCsv(inv.id)}>Download evidence CSV</LinkButton>
          </div>
        }
      >
        <p className="mb-3 text-sm text-slate-600">
          Generated from the stored investigation each time it is opened: totals, inputs and fingerprints, metric definitions, findings by evidence status with sample evidence, simulations, review notes, recommended
          next steps and proof queries.
        </p>
        {/* The report is rendered by the backend with every value escaped and no scripts; the sandbox adds a second barrier. */}
        <iframe title="Report preview" src={links.report(inv.id, "html")} sandbox="" className="h-[70vh] w-full rounded border border-slate-200 bg-white" />
      </Card>
    </div>
  );
}
