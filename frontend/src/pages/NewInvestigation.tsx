import { useState } from "react";
import { useNavigate } from "react-router-dom";

import { api } from "../api/client";
import type { Dataset, MetricDefinition } from "../api/types";
import { Button, Card, ErrorMessage, Field, inputClass, Loading, Notice, PageHeader, Tabs } from "../components/ui";
import UploadAndMap from "../features/files/UploadAndMap";
import MetricForm from "../features/metrics/MetricForm";
import QuerySnapshot from "../features/sources/QuerySnapshot";
import { DnaDifferences, DnaTable } from "../features/investigation/Summary";
import { formatCount, formatDateTime } from "../lib/format";
import { useAction, useApi } from "../lib/useApi";

type Mode = "existing" | "file" | "query";

function datasetLabel(d: Dataset) {
  const kind = d.source_type === "postgres_query" ? "query snapshot" : d.source_type.toUpperCase();
  return `${d.label} · ${kind} · ${formatCount(d.row_count)} rows${d.is_demo ? " · demo" : ""}`;
}

function InputPicker({ side, datasets, onDatasets, value, onChange }: { side: "left" | "right"; datasets: Dataset[]; onDatasets: () => void; value?: Dataset; onChange: (d: Dataset) => void }) {
  const [mode, setMode] = useState<Mode>(datasets.length ? "existing" : "file");
  const queries = useApi(() => api.queries(), []);
  const chosen = (d: Dataset) => {
    onDatasets();
    onChange(d);
  };
  return (
    <div>
      <Tabs
        label={`${side} input source`}
        active={mode}
        onChange={setMode}
        tabs={[
          { id: "existing", label: "Existing dataset" },
          { id: "file", label: "Upload a file" },
          { id: "query", label: "Database query" },
        ]}
      />
      {mode === "existing" &&
        (datasets.length ? (
          <Field label="Dataset" htmlFor={`${side}-dataset`}>
            <select id={`${side}-dataset`} className={inputClass} value={value?.id ?? ""} onChange={(e) => onChange(datasets.find((d) => d.id === e.target.value)!)}>
              <option value="">— choose a dataset —</option>
              {datasets.map((d) => (
                <option key={d.id} value={d.id}>
                  {datasetLabel(d)}
                </option>
              ))}
            </select>
          </Field>
        ) : (
          <p className="text-sm text-slate-500">No datasets yet. Upload a file or snapshot a database query.</p>
        ))}
      {mode === "file" && <UploadAndMap idPrefix={`${side}-up`} onImported={chosen} />}
      {mode === "query" && (queries.data ? <QuerySnapshot idPrefix={`${side}-q`} queries={queries.data} onSnapshot={chosen} /> : queries.error ? <ErrorMessage error={queries.error} /> : <Loading />)}
      {value && (
        <p className="mt-3 rounded bg-slate-50 px-2 py-1.5 text-sm">
          Selected: <span className="font-medium">{value.label}</span> · {formatCount(value.row_count)} rows
          {value.rejected_row_count ? ` · ${formatCount(value.rejected_row_count)} rejected at import` : ""}
          {value.source_schema.executed_at ? ` · snapshot ${formatDateTime(value.source_schema.executed_at)}` : ""}
        </p>
      )}
    </div>
  );
}

function MetricPicker({ side, metrics, value, onChange, onCreated }: { side: "left" | "right"; metrics: MetricDefinition[]; value?: MetricDefinition; onChange: (m?: MetricDefinition) => void; onCreated: (m: MetricDefinition) => void }) {
  const [creating, setCreating] = useState(false);
  return (
    <div className="space-y-3">
      <Field label={`${side === "left" ? "Left" : "Right"} metric definition`} htmlFor={`${side}-metric`}>
        <select id={`${side}-metric`} className={inputClass} value={value?.id ?? ""} onChange={(e) => onChange(metrics.find((m) => m.id === e.target.value))}>
          <option value="">— choose a definition —</option>
          {metrics.map((m) => (
            <option key={m.id} value={m.id}>
              {m.name} v{m.version} · {m.aggregation} · {m.refund_policy.replace(/_/g, " ")}
            </option>
          ))}
        </select>
      </Field>
      <Button variant="ghost" onClick={() => setCreating(!creating)}>
        {creating ? "Cancel new definition" : "+ New definition"}
      </Button>
      {creating && (
        <div className="rounded border border-slate-200 p-3">
          <MetricForm
            idPrefix={`${side}-mf`}
            onCreated={(m) => {
              setCreating(false);
              onCreated(m);
            }}
          />
        </div>
      )}
    </div>
  );
}

export default function NewInvestigation() {
  const navigate = useNavigate();
  const datasets = useApi(() => api.datasets(), []);
  const metrics = useApi(() => api.metrics(), []);
  const [title, setTitle] = useState("");
  const [left, setLeft] = useState<Dataset>();
  const [right, setRight] = useState<Dataset>();
  const [leftMetric, setLeftMetric] = useState<MetricDefinition>();
  const [rightMetric, setRightMetric] = useState<MetricDefinition>();
  const [errors, setErrors] = useState<Record<string, string>>({});
  const dna = useApi(async () => (leftMetric && rightMetric ? api.compareMetrics(leftMetric.id, rightMetric.id) : undefined), [leftMetric?.id, rightMetric?.id]);
  const create = useAction(api.createInvestigation);

  const submit = async () => {
    const e: Record<string, string> = {};
    if (!title.trim()) e.title = "Give the investigation a title.";
    if (!left) e.left = "Choose or create the left dataset.";
    if (!right) e.right = "Choose or create the right dataset.";
    if (!leftMetric) e.leftMetric = "Choose the left metric definition.";
    if (!rightMetric) e.rightMetric = "Choose the right metric definition.";
    setErrors(e);
    if (Object.keys(e).length) return;
    const inv = await create.run({
      title: title.trim(),
      left: { dataset_id: left!.id, metric_definition_id: leftMetric!.id },
      right: { dataset_id: right!.id, metric_definition_id: rightMetric!.id },
    });
    if (inv) navigate(`/investigations/${inv.id}`);
  };

  if (datasets.error || metrics.error) return <ErrorMessage error={datasets.error ?? metrics.error} />;
  if (!datasets.data || !metrics.data) return <Loading />;

  const addMetric = (side: "left" | "right") => (m: MetricDefinition) => {
    metrics.reload();
    if (side === "left") setLeftMetric(m);
    else setRightMetric(m);
  };

  return (
    <div className="space-y-4">
      <PageHeader title="New investigation" subtitle="Compare one metric computed two ways. The difference is reported as right − left." />
      <Card title="1. Title">
        <Field label="Title" htmlFor="inv-title" error={errors.title}>
          <input id="inv-title" className={inputClass} value={title} maxLength={200} onChange={(e) => setTitle(e.target.value)} placeholder="e.g. March revenue: Finance vs Sales" />
        </Field>
      </Card>
      <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
        <Card title="2. Left input">
          <InputPicker side="left" datasets={datasets.data} onDatasets={datasets.reload} value={left} onChange={setLeft} />
          <ErrorMessage error={errors.left} title="Left input" />
        </Card>
        <Card title="3. Right input">
          <InputPicker side="right" datasets={datasets.data} onDatasets={datasets.reload} value={right} onChange={setRight} />
          <ErrorMessage error={errors.right} title="Right input" />
        </Card>
      </div>
      <Card title="4. Metric definitions">
        <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
          <div>
            <MetricPicker side="left" metrics={metrics.data} value={leftMetric} onChange={setLeftMetric} onCreated={addMetric("left")} />
            <ErrorMessage error={errors.leftMetric} title="Left metric" />
          </div>
          <div>
            <MetricPicker side="right" metrics={metrics.data} value={rightMetric} onChange={setRightMetric} onCreated={addMetric("right")} />
            <ErrorMessage error={errors.rightMetric} title="Right metric" />
          </div>
        </div>
        {dna.data && (
          <div className="mt-4">
            <p className="mb-2 text-sm font-medium">Metric DNA</p>
            <div className="mb-3">
              <DnaDifferences dna={dna.data} />
            </div>
            <DnaTable dna={dna.data} leftLabel={`${leftMetric?.name} v${leftMetric?.version}`} rightLabel={`${rightMetric?.name} v${rightMetric?.version}`} />
          </div>
        )}
      </Card>
      <Card title="5. Run">
        <Notice>
          The investigation evaluates both metrics, replays definition differences, matches records by business key and stores every finding with its evidence. Sources are only read.
        </Notice>
        <div className="mt-3 flex flex-wrap gap-2">
          <Button variant="primary" pending={create.pending} onClick={submit}>
            Run investigation
          </Button>
        </div>
        <div className="mt-2">
          <ErrorMessage error={create.error} title="The inputs cannot be compared" />
        </div>
      </Card>
    </div>
  );
}
