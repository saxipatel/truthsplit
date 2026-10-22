import { useState } from "react";

import { api } from "../../api/client";
import type { ColumnMapping, Dataset, Preview, Upload } from "../../api/types";
import { Button, ErrorMessage, Field, inputClass, Notice, Table, td, th } from "../../components/ui";
import { formatCount } from "../../lib/format";
import { useAction } from "../../lib/useApi";

const ENCODINGS = ["utf-8", "cp1252", "latin-1"];
const ZONES = ["UTC", "America/New_York", "America/Chicago", "America/Los_Angeles", "Europe/London", "Europe/Berlin", "Asia/Kolkata", "Asia/Tokyo", "Australia/Sydney"];

const emptyMapping = (): ColumnMapping => ({
  source_record_id: null,
  business_key: [],
  occurred_at: null,
  timezone: "UTC",
  date_format: null,
  amount: null,
  refund_amount: null,
  currency: null,
  default_currency: null,
  status: null,
  attributes: [],
});

function guess(header: string[]): ColumnMapping {
  const find = (...names: string[]) => header.find((h) => names.some((n) => h.toLowerCase().replace(/[^a-z]/g, "").includes(n))) ?? null;
  const key = find("ordernumber", "orderid", "orderref", "id");
  return {
    ...emptyMapping(),
    business_key: key ? [key] : [],
    amount: find("amount", "gross", "total", "revenue", "value"),
    refund_amount: find("refund"),
    currency: find("currency", "ccy"),
    occurred_at: find("date", "time", "at", "posted", "placed", "booked"),
    status: find("status", "state"),
  };
}

function ColumnSelect({ id, label, header, value, onChange, hint }: { id: string; label: string; header: string[]; value: string | null | undefined; onChange: (v: string | null) => void; hint?: string }) {
  return (
    <Field label={label} htmlFor={id} hint={hint}>
      <select id={id} className={inputClass} value={value ?? ""} onChange={(e) => onChange(e.target.value || null)}>
        <option value="">— not mapped —</option>
        {header.map((h) => (
          <option key={h} value={h}>
            {h}
          </option>
        ))}
      </select>
    </Field>
  );
}

function PreviewTable({ preview }: { preview: Preview }) {
  return (
    <>
      <Table label="File preview">
        <thead>
          <tr>
            <th className={th}>Row</th>
            {preview.columns.map((c) => (
              <th key={c.name} className={th}>
                <span className="block">{c.name}</span>
                <span className="block font-normal text-slate-500">
                  {c.inferred_type}
                  {c.empty_count ? ` · ${c.empty_count} empty` : ""}
                </span>
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {preview.rows.slice(0, 10).map((row, i) => (
            <tr key={preview.row_numbers[i]}>
              <td className={`${td} text-slate-500 tabular-nums`}>{preview.row_numbers[i]}</td>
              {row.map((cell, j) => (
                <td key={j} className={`${td} max-w-56 truncate`} title={cell ?? ""}>
                  {cell ?? <span className="text-slate-400">empty</span>}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </Table>
      <p className="mt-1 text-xs text-slate-500">
        Showing {Math.min(10, preview.rows.length)} of the first {preview.rows.length} rows{preview.truncated ? " (file has more)" : ""}. Types are inferred from up to 1,000 rows and do not change how values are parsed.
      </p>
    </>
  );
}

export default function UploadAndMap({ onImported, idPrefix = "up" }: { onImported: (dataset: Dataset) => void; idPrefix?: string }) {
  const [file, setFile] = useState<File | null>(null);
  const [upload, setUpload] = useState<Upload>();
  const [preview, setPreview] = useState<Preview | null>(null);
  const [previewError, setPreviewError] = useState<string | null>(null);
  const [sheet, setSheet] = useState<string | null>(null);
  const [encoding, setEncoding] = useState("utf-8");
  const [mapping, setMapping] = useState<ColumnMapping>(emptyMapping());
  const [label, setLabel] = useState("");
  const [result, setResult] = useState<{ dataset: Dataset; already: boolean }>();
  const [formError, setFormError] = useState<string>();

  const uploadAction = useAction(async (f: File) => {
    const resp = await api.upload(f);
    setUpload(resp.upload);
    setPreview(resp.preview);
    setPreviewError(resp.preview_error);
    setSheet(resp.preview?.sheet ?? resp.upload.sheet_names[0] ?? null);
    setMapping(resp.preview ? guess(resp.preview.header) : emptyMapping());
    setResult(undefined);
    return resp;
  });
  const previewAction = useAction(async (s: string | null, enc: string) => {
    if (!upload) return;
    setPreviewError(null);
    try {
      const p = await api.preview(upload.id, s, enc);
      setPreview(p);
      setMapping(guess(p.header));
    } catch (err) {
      setPreview(null);
      setPreviewError((err as Error).message);
    }
  });
  const importAction = useAction(async () => {
    if (!upload) return;
    const resp = await api.importDataset({ upload_id: upload.id, sheet, encoding, label: label || undefined, mapping });
    setResult({ dataset: resp.dataset, already: resp.already_imported });
    onImported(resp.dataset);
  });

  const header = preview?.header ?? [];
  const set = (patch: Partial<ColumnMapping>) => setMapping((m) => ({ ...m, ...patch }));
  const toggle = (key: "business_key" | "attributes", col: string) =>
    setMapping((m) => ({ ...m, [key]: m[key].includes(col) ? m[key].filter((c) => c !== col) : [...m[key], col] }));

  const submit = () => {
    if (!mapping.business_key.length && !mapping.amount && !mapping.source_record_id) {
      setFormError("Map at least a business key, an amount or a record id.");
      return;
    }
    if (mapping.currency && mapping.default_currency) {
      setFormError("Use either a currency column or a default currency, not both.");
      return;
    }
    if (mapping.default_currency && !/^[A-Za-z]{3}$/.test(mapping.default_currency)) {
      setFormError("Default currency must be a 3-letter code such as USD.");
      return;
    }
    setFormError(undefined);
    void importAction.run();
  };

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-end gap-3">
        <Field label="File (CSV, XLSX or XLS)" htmlFor={`${idPrefix}-file`}>
          <input
            id={`${idPrefix}-file`}
            type="file"
            accept=".csv,.xlsx,.xls"
            className="block max-w-full text-sm"
            onChange={(e) => setFile(e.target.files?.[0] ?? null)}
          />
        </Field>
        <Button variant="primary" disabled={!file} pending={uploadAction.pending} onClick={() => file && uploadAction.run(file)}>
          Upload and preview
        </Button>
      </div>
      <ErrorMessage error={uploadAction.error} title="Upload failed" />

      {upload && (
        <div className="space-y-4">
          <p className="text-sm text-slate-600">
            <span className="font-medium text-slate-800">{upload.original_filename}</span> · {upload.file_type.toUpperCase()} · {formatCount(upload.size_bytes)} bytes · sha256{" "}
            <code className="text-xs">{upload.sha256.slice(0, 12)}</code>
          </p>
          <div className="flex flex-wrap items-end gap-3">
            {upload.sheet_names.length > 0 && (
              <Field label="Worksheet" htmlFor={`${idPrefix}-sheet`}>
                <select
                  id={`${idPrefix}-sheet`}
                  className={inputClass}
                  value={sheet ?? ""}
                  onChange={(e) => {
                    setSheet(e.target.value);
                    void previewAction.run(e.target.value, encoding);
                  }}
                >
                  {upload.sheet_names.map((s) => (
                    <option key={s}>{s}</option>
                  ))}
                </select>
              </Field>
            )}
            {upload.file_type === "csv" && (
              <Field label="Encoding" htmlFor={`${idPrefix}-enc`}>
                <select
                  id={`${idPrefix}-enc`}
                  className={inputClass}
                  value={encoding}
                  onChange={(e) => {
                    setEncoding(e.target.value);
                    void previewAction.run(null, e.target.value);
                  }}
                >
                  {ENCODINGS.map((e) => (
                    <option key={e}>{e}</option>
                  ))}
                </select>
              </Field>
            )}
          </div>
          {previewError && <Notice tone="warning">{previewError}</Notice>}
          {preview && <PreviewTable preview={preview} />}
        </div>
      )}

      {preview && (
        <fieldset className="space-y-3 rounded-md border border-slate-200 p-3">
          <legend className="px-1 text-sm font-semibold">Column mapping</legend>
          <p className="text-xs text-slate-500">Map the file's columns onto the normalized record. Rows with invalid values in mapped columns are rejected and listed, never changed.</p>
          <div>
            <p className="mb-1 text-xs font-medium text-slate-700">Business key (one column, or several for a composite key)</p>
            <div className="flex flex-wrap gap-x-4 gap-y-1">
              {header.map((h) => (
                <label key={h} className="flex items-center gap-1.5 text-sm">
                  <input type="checkbox" checked={mapping.business_key.includes(h)} onChange={() => toggle("business_key", h)} />
                  {h}
                </label>
              ))}
            </div>
          </div>
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
            <ColumnSelect id={`${idPrefix}-amount`} label="Amount" header={header} value={mapping.amount} onChange={(v) => set({ amount: v })} />
            <ColumnSelect id={`${idPrefix}-refund`} label="Refund amount" header={header} value={mapping.refund_amount} onChange={(v) => set({ refund_amount: v })} />
            <ColumnSelect id={`${idPrefix}-occurred`} label="Timestamp" header={header} value={mapping.occurred_at} onChange={(v) => set({ occurred_at: v })} />
            <Field label="Time zone for timestamps without an offset" htmlFor={`${idPrefix}-tz`}>
              <input id={`${idPrefix}-tz`} className={inputClass} list={`${idPrefix}-zones`} value={mapping.timezone} onChange={(e) => set({ timezone: e.target.value })} />
              <datalist id={`${idPrefix}-zones`}>
                {ZONES.map((z) => (
                  <option key={z} value={z} />
                ))}
              </datalist>
            </Field>
            <ColumnSelect id={`${idPrefix}-currency`} label="Currency column" header={header} value={mapping.currency} onChange={(v) => set({ currency: v })} />
            <Field label="Or default currency" htmlFor={`${idPrefix}-defccy`} hint="ISO code applied to every row">
              <input
                id={`${idPrefix}-defccy`}
                className={inputClass}
                maxLength={3}
                value={mapping.default_currency ?? ""}
                onChange={(e) => set({ default_currency: e.target.value.toUpperCase() || null })}
              />
            </Field>
            <ColumnSelect id={`${idPrefix}-status`} label="Status" header={header} value={mapping.status} onChange={(v) => set({ status: v })} />
            <ColumnSelect id={`${idPrefix}-recid`} label="Record id" header={header} value={mapping.source_record_id} onChange={(v) => set({ source_record_id: v })} hint="Defaults to row:<number>" />
            <Field label="Date format (optional)" htmlFor={`${idPrefix}-fmt`} hint="strptime, e.g. %d/%m/%Y; ISO 8601 when empty">
              <input id={`${idPrefix}-fmt`} className={inputClass} value={mapping.date_format ?? ""} onChange={(e) => set({ date_format: e.target.value || null })} />
            </Field>
          </div>
          <div>
            <p className="mb-1 text-xs font-medium text-slate-700">Extra columns to keep for filters</p>
            <div className="flex flex-wrap gap-x-4 gap-y-1">
              {header.map((h) => (
                <label key={h} className="flex items-center gap-1.5 text-sm">
                  <input type="checkbox" checked={mapping.attributes.includes(h)} onChange={() => toggle("attributes", h)} />
                  {h}
                </label>
              ))}
            </div>
          </div>
          <div className="flex flex-wrap items-end gap-3">
            <Field label="Dataset label (optional)" htmlFor={`${idPrefix}-label`}>
              <input id={`${idPrefix}-label`} className={inputClass} value={label} maxLength={200} onChange={(e) => setLabel(e.target.value)} />
            </Field>
            <Button variant="primary" pending={importAction.pending} onClick={submit}>
              Import dataset
            </Button>
          </div>
          <ErrorMessage error={formError} title="Check the mapping" />
          <ErrorMessage error={importAction.error} title="Import failed" />
        </fieldset>
      )}

      {result && (
        <Notice tone={result.dataset.rejected_row_count ? "warning" : "success"}>
          {result.already ? "This file was already imported with the same settings; using the existing dataset. " : "Imported. "}
          <strong>{formatCount(result.dataset.row_count)}</strong> rows accepted
          {result.dataset.rejected_row_count ? (
            <>
              , <strong>{formatCount(result.dataset.rejected_row_count)}</strong> rejected. Rejected rows are excluded from every metric; see the dataset's issues on the Sources page.
            </>
          ) : (
            ", none rejected."
          )}
        </Notice>
      )}
    </div>
  );
}
