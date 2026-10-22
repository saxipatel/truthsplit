import { Link, useParams, useSearchParams } from "react-router-dom";

import { api } from "../api/client";
import { DemoBadge, ErrorMessage, ExplanationBadge, Loading, PageHeader, SignedAmount, StatTile, StatusBadge, Tabs } from "../components/ui";
import Autopsy from "../features/investigation/Autopsy";
import Report from "../features/investigation/Report";
import Review from "../features/investigation/Review";
import Simulator from "../features/investigation/Simulator";
import Summary from "../features/investigation/Summary";
import { formatAmount, formatDateTime } from "../lib/format";
import { useApi } from "../lib/useApi";

const TABS = [
  { id: "comparison", label: "Comparison results" },
  { id: "autopsy", label: "Difference autopsy" },
  { id: "simulator", label: "What-if simulator" },
  { id: "review", label: "Review & history" },
  { id: "report", label: "Report" },
] as const;
type TabId = (typeof TABS)[number]["id"];

export default function InvestigationDetail() {
  const { id = "" } = useParams();
  const [params, setParams] = useSearchParams();
  const tab = (TABS.find((t) => t.id === params.get("tab"))?.id ?? "autopsy") as TabId;
  const inv = useApi(() => api.investigation(id), [id]);

  if (inv.error) {
    return (
      <div className="space-y-3">
        <ErrorMessage error={inv.error} title="Could not load the investigation" />
        <Link to="/history" className="text-sm text-blue-800 hover:underline">
          Back to history
        </Link>
      </div>
    );
  }
  if (!inv.data) return <Loading label="Loading investigation" />;
  const d = inv.data;
  const currency = d.config_json.currency ? ` ${d.config_json.currency}` : "";

  return (
    <div>
      <PageHeader
        title={d.title}
        subtitle={
          <span className="flex flex-wrap items-center gap-2">
            <span className="text-xs text-slate-500">Evidence:</span>
            <ExplanationBadge delta={d.baseline_delta} unexplained={d.unexplained_delta} />
            <span className="text-xs text-slate-500">Review:</span>
            <StatusBadge status={d.status} />
            {d.is_demo && <DemoBadge />}
            <span>
              Created {formatDateTime(d.created_at)} · {d.left_metric.name} v{d.left_metric.version} vs {d.right_metric.name} v{d.right_metric.version} · engine {d.engine_version}
            </span>
          </span>
        }
      />
      <div className="mb-5 grid grid-cols-2 gap-2 md:grid-cols-5">
        <StatTile label={`Left${currency}`} value={formatAmount(d.baseline_left)} />
        <StatTile label={`Right${currency}`} value={formatAmount(d.baseline_right)} />
        <StatTile label="Difference (right − left)" value={<SignedAmount value={d.baseline_delta} />} emphasis />
        <StatTile label="Explained (confirmed)" value={<SignedAmount value={d.explained_delta} />} />
        <StatTile label="Unexplained" value={<SignedAmount value={d.unexplained_delta} />} />
      </div>
      <Tabs label="Investigation sections" tabs={[...TABS]} active={tab} onChange={(t) => setParams({ tab: t }, { replace: true })} />
      <div role="tabpanel">
        {tab === "comparison" && <Summary inv={d} />}
        {tab === "autopsy" && <Autopsy inv={d} />}
        {tab === "simulator" && <Simulator inv={d} />}
        {tab === "review" && <Review inv={d} onChanged={inv.reload} />}
        {tab === "report" && <Report inv={d} />}
      </div>
    </div>
  );
}
