// Shapes returned by the FastAPI backend. Money values arrive as decimal strings and stay strings.

export type Money = string;

export type EvidenceStatus = "confirmed" | "suspected" | "unexplained";
export type InvestigationStatus = "open" | "in_review" | "resolved" | "archived";
export type Side = "left" | "right" | "both";

export interface ColumnProfile {
  name: string;
  inferred_type: string;
  empty_count: number;
  sample: (string | null)[];
}

export interface Preview {
  sheet: string | null;
  header: string[];
  row_numbers: number[];
  rows: (string | null)[][];
  columns: ColumnProfile[];
  truncated: boolean;
}

export interface Upload {
  id: string;
  original_filename: string;
  file_type: "csv" | "xlsx" | "xls";
  size_bytes: number;
  sha256: string;
  sheet_names: string[];
  created_at: string;
}

export interface UploadResponse {
  upload: Upload;
  already_uploaded: boolean;
  preview: Preview | null;
  preview_error: string | null;
}

export interface ColumnMapping {
  source_record_id?: string | null;
  business_key: string[];
  occurred_at?: string | null;
  timezone: string;
  date_format?: string | null;
  amount?: string | null;
  refund_amount?: string | null;
  currency?: string | null;
  default_currency?: string | null;
  status?: string | null;
  attributes: string[];
}

export interface Dataset {
  id: string;
  label: string;
  source_type: "csv" | "xlsx" | "xls" | "postgres_query";
  upload_id: string | null;
  sheet_name: string | null;
  column_mapping: ColumnMapping;
  source_schema: Record<string, unknown> & { executed_at?: string; header?: string[] };
  fingerprint: string;
  row_count: number;
  rejected_row_count: number;
  is_demo: boolean;
  created_at: string;
}

export interface Page<T> {
  total: number;
  offset: number;
  items: T[];
}

export interface ImportIssue {
  row_number: number | null;
  column_name: string | null;
  raw_value: string | null;
  message: string;
}

export interface MetricFilter {
  field: string;
  op: string;
  value?: string | number | (string | number)[] | null;
}

export interface MetricSpec {
  aggregation: "sum" | "count" | "count_distinct";
  value_field: "amount" | "refund_amount";
  grain: "record" | "business_key";
  filters: MetricFilter[];
  window_start: string | null;
  window_end: string | null;
  window_end_inclusive: boolean;
  timezone: string;
  refund_policy: "gross" | "net_of_refunds" | "exclude_refunded";
  null_policy: "exclude" | "reject";
  currency: string | null;
}

export interface MetricDefinition extends MetricSpec {
  id: string;
  name: string;
  version: number;
  description: string | null;
  created_at: string;
}

export interface DnaField {
  field: string;
  left: unknown;
  right: unknown;
  differs: boolean;
}

export interface DnaComparison {
  fields: DnaField[];
  filters_left_only: string[];
  filters_right_only: string[];
  sources?: { left: Record<string, unknown>; right: Record<string, unknown> };
}

export interface Finding {
  id: string;
  step_order: number | null;
  type: string;
  evidence_status: EvidenceStatus;
  title: string;
  description: string;
  amount_impact: Money | null;
  rule_id: string;
  classification_reason: string;
  assumptions_json: string[];
  calculation_json: Record<string, unknown>;
  proof_hash: string | null;
  affected_row_count: number;
  overlap_note: string | null;
}

export interface InvestigationSummary {
  id: string;
  title: string;
  status: InvestigationStatus;
  baseline_left: Money | null;
  baseline_right: Money | null;
  baseline_delta: Money | null;
  explained_delta: Money | null;
  unexplained_delta: Money | null;
  is_demo: boolean;
  created_at: string;
}

export interface ReplayStep {
  step: string;
  finding_type: string;
  status: string;
  metric_before: string | null;
  metric_after: string | null;
  contribution: string | null;
  standalone: string | null;
  detail: string | null;
}

export interface Investigation extends InvestigationSummary {
  left_metric: MetricDefinition;
  right_metric: MetricDefinition;
  inputs: { side: "left" | "right"; dataset_id: string; dataset_fingerprint: string; row_count: number }[];
  comparison: {
    matched_keys: number;
    left_only_keys: number;
    right_only_keys: number;
    left_duplicate_keys: number;
    right_duplicate_keys: number;
    left_null_keys: number;
    right_null_keys: number;
    mismatched_keys: number;
  } | null;
  findings: Finding[];
  config_json: {
    currency?: string | null;
    metric_dna?: DnaComparison;
    replay?: ReplayStep[];
    attribution_order?: string[];
    excluded?: { left: Record<string, number>; right: Record<string, number> };
    proof_queries?: Record<string, { sql: string; query_hash: string }>;
  };
  engine_version: string | null;
}

export interface Evidence {
  side: Side;
  business_key: string | null;
  source_record_id: string | null;
  left_amount: Money | null;
  right_amount: Money | null;
  contribution: Money | null;
  detail: Record<string, unknown>;
}

export interface SimulationSummary {
  left: Money;
  right: Money;
  delta: Money;
  explained: Money;
  unexplained: Money;
  findings: { type: string; rule_id: string; status: EvidenceStatus; amount: Money | null; title: string }[];
}

export interface Simulation {
  id: string;
  investigation_id: string;
  rule_type: string;
  parameters_json: Record<string, unknown>;
  before_left: Money;
  before_right: Money;
  before_delta: Money;
  after_left: Money;
  after_right: Money;
  after_delta: Money;
  resolved_amount: Money;
  affected_rows: number;
  result_json: {
    delta_change: Money;
    baseline: SimulationSummary;
    simulated: SimulationSummary;
    sides: { side: string; records_before: number; records_after: number; changed_contributions: number; samples: { row_number: number; business_key: string | null; before: string | null; after: string | null }[] }[];
    remap?: { rows_read: number; rows_accepted: number; rows_rejected: number; issues: ImportIssue[] } | null;
  };
  created_at: string;
}

export interface Note {
  id: string;
  finding_id: string | null;
  decision: "accepted" | "rejected" | "comment";
  note: string;
  reviewer: string | null;
  reviewed_at: string;
}

export interface AuditEvent {
  actor: string;
  action: string;
  occurred_at: string;
  metadata_json: Record<string, unknown>;
}

export interface SimilarMatch {
  investigation_id: string;
  title: string;
  score: number;
  reasons: string[];
  accepted_diagnoses: { rule_id: string; finding_title: string; note: string; reviewer: string | null; reviewed_at: string; applies_here: boolean }[];
}

export interface Pattern {
  finding_type: string;
  rule_id: string;
  investigations: number;
  recurring: boolean;
  confirmed: number;
  suspected: number;
  unexplained: number;
  total_absolute_amount: Money;
  last_seen: string | null;
}

export interface Connection {
  name: string;
  host: string | null;
  port: number | null;
  database: string | null;
  host_allowed: boolean;
}

export interface ConnectionTest {
  name: string;
  ok: boolean;
  user: string;
  server_version: string;
  role_read_only_default: boolean;
  problems: string[];
}

export interface CatalogTable {
  schema: string;
  table: string;
  columns: { name: string; type: string }[];
}

export interface RegisteredQuery {
  key: string;
  title: string;
  description: string;
  connection: string;
  sql: string;
  sql_sha256: string;
  params: { name: string; type: "date" | "text" | "integer"; description: string; choices: string[] }[];
  mapping: ColumnMapping;
  tables: string[];
  joins: { left: string; right: string; cardinality: string }[];
  grain: string;
  notes: string[];
}

export interface Freshness {
  dataset_id: string;
  status: "current" | "source_changed" | "query_changed" | "query_removed";
  detail: string | null;
  snapshot_executed_at: string | null;
  snapshot_rows: number | null;
  current_rows: number | null;
  checked_at: string;
}

export interface SettingsSummary {
  max_upload_mb: number;
  max_rows: number;
  statement_timeout_ms: number;
  allowed_source_hosts: string[];
  source_connections: string[];
  supported_file_types: string[];
  csv_encodings: string[];
}

export interface DatasetProfile {
  rows: number;
  business_keys: { distinct: number; rows_without_key: number; repeated_keys: number };
  exact_duplicate_rows: number;
  amount: { values: number; missing: number; sum: Money | null; min: Money | null; max: Money | null; negative: number; zero: number };
  refund: { values: number; refunded_rows: number; sum: Money | null };
  occurred_at: { values: number; missing: number; first: string | null; last: string | null };
  currencies: { value: string | null; rows: number }[];
  statuses: { value: string | null; rows: number }[];
  attributes: { name: string; distinct_values: number; empty: number }[];
  rejected_by_column: { column: string | null; rows: number }[];
}
