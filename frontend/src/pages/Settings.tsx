import { api } from "../api/client";
import { Card, ErrorMessage, KeyValue, Loading, PageHeader } from "../components/ui";
import { formatCount } from "../lib/format";
import { useApi } from "../lib/useApi";

export default function Settings() {
  const settings = useApi(() => api.settings(), []);
  const readiness = useApi(() => api.readiness(), []);
  return (
    <div className="space-y-4">
      <PageHeader title="Settings" subtitle="Limits and safety settings in effect. They are set through environment variables (see .env.example); credentials are never shown." />
      <Card title="Limits">
        {settings.error && <ErrorMessage error={settings.error} />}
        {!settings.data && !settings.error && <Loading />}
        {settings.data && (
          <KeyValue
            items={[
              ["Upload size limit", `${settings.data.max_upload_mb} MB`],
              ["Rows per dataset", formatCount(settings.data.max_rows)],
              ["Source statement timeout", `${formatCount(settings.data.statement_timeout_ms)} ms`],
              ["File types", settings.data.supported_file_types.join(", ")],
              ["CSV encodings", settings.data.csv_encodings.join(", ")],
            ]}
          />
        )}
      </Card>
      <Card title="Source databases">
        {settings.data && (
          <KeyValue
            items={[
              ["Configured connections", settings.data.source_connections.join(", ") || "none"],
              ["Allowed source hosts", settings.data.allowed_source_hosts.join(", ")],
              ["Access", "Registered queries only, in read-only transactions; a login that can write is refused."],
            ]}
          />
        )}
      </Card>
      <Card title="Application">
        {readiness.error && <ErrorMessage error={readiness.error} title="API unavailable" />}
        {readiness.data && (
          <KeyValue
            items={[
              ["API", readiness.data.status],
              ["Metadata database", readiness.data.database],
              ["Schema revision", readiness.data.schema_revision ?? "—"],
            ]}
          />
        )}
      </Card>
    </div>
  );
}
