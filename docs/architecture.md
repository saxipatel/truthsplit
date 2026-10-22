# Architecture

## Components

```mermaid
flowchart LR
    user([Browser])
    subgraph host["Docker Compose on localhost (all ports bound to 127.0.0.1)"]
        web["frontend<br/>nginx 1.30 (non-root)<br/>static React build<br/>:8080"]
        api["backend<br/>FastAPI + Uvicorn (non-root)<br/>:8000"]
        db[("db<br/>PostgreSQL 16<br/>metadata, records,<br/>findings, evidence")]
        src[("source-db<br/>PostgreSQL 16<br/>demo 'shop' database")]
        vol[/"storage volume<br/>uploaded files<br/>(content-addressed)"/]
    end
    user -- "/ and /assets/*" --> web
    user -- "/api/*, /health/*" --> web
    web -- "reverse proxy" --> api
    api -- "read/write (app role)" --> db
    api -- "registered queries only<br/>read-only role ts_reader,<br/>READ ONLY transaction" --> src
    api --> vol
```

The browser talks to one origin. nginx serves the compiled frontend and proxies `/api/` and `/health/` to the backend, so there is no CORS configuration. For frontend development, `docker-compose.dev.yml` swaps nginx for the Vite dev server on port 5173.

## Backend modules

```mermaid
flowchart TB
    subgraph ingest["Ingestion"]
        files["adapters/files.py<br/>CSV / XLSX / XLS reading,<br/>signature and size checks"]
        pg["adapters/postgres.py<br/>allowlist, privilege inspection,<br/>read-only execution"]
        rq["adapters/registered_queries.py<br/>fixed SQL + typed params"]
        norm["normalization/<br/>column mapping, Decimal, UTC,<br/>rejected values"]
    end
    subgraph engine["Engine (pure Python, Decimal)"]
        metrics["comparison/<br/>Metric DNA evaluation,<br/>key matching, SQL parity"]
        autopsy["diagnosis/autopsy.py<br/>ordered replay, join amplification,<br/>cutoffs, refund identity"]
        whatif["simulation/whatif.py<br/>in-memory corrections"]
        memory["memory/<br/>patterns, similarity"]
    end
    report["reporting/report.py<br/>JSON, HTML, evidence CSV"]
    api["api/ (FastAPI routers)"]
    dbmod["db/ (SQLAlchemy, COPY bulk load)"]
    files --> norm
    rq --> pg --> norm
    norm --> dbmod
    api --> files & rq & metrics & whatif & memory & report
    metrics --> autopsy
    whatif --> autopsy
    autopsy --> dbmod
```

An investigation runs in this order:

1. Load both datasets' records.
2. Evaluate each side's Metric DNA: window → filters → refund policy → grain → null policy → aggregation.
3. Reconcile per business key: extra, missing, duplicate, mismatch, no key.
4. Run the Difference Autopsy:
   - replay the definition differences in a fixed order, attributing each step's change;
   - apply the data rules;
   - check that the confirmed amounts plus the unexplained remainder sum exactly to the difference.
5. Store findings with record-level evidence and proof SQL.

## Database (metadata)

Schema created by Alembic migrations `0001`–`0004` (`backend/migrations/versions/`). Amounts are `numeric(18,4)` per record and `numeric(24,4)` for totals and evidence; timestamps are `timestamptz` in UTC.

```mermaid
erDiagram
    file_uploads ||--o{ datasets : "imported as"
    source_connections ||--o{ datasets : "snapshotted from"
    datasets ||--o{ dataset_records : contains
    datasets ||--o{ import_issues : "rejected values"
    datasets ||--o{ investigation_inputs : "used by"
    metric_definitions ||--o{ investigations : "left_metric_id"
    metric_definitions ||--o{ investigations : "right_metric_id"
    investigations ||--|{ investigation_inputs : "left + right"
    investigations ||--o| comparison_results : "key statistics"
    investigations ||--o{ findings : explains
    findings ||--o{ evidence_records : "supported by"
    dataset_records ||--o{ evidence_records : "cited by"
    investigations ||--o{ simulations : "what-if"
    investigations ||--o{ resolution_notes : review
    findings ||--o{ resolution_notes : "accept / reject"

    file_uploads {
        uuid id PK
        varchar sha256 "content address"
        varchar file_type "csv | xlsx | xls"
        bigint size_bytes
        jsonb sheet_names
    }
    source_connections {
        uuid id PK
        varchar label
        varchar config_reference "env profile name, never credentials"
    }
    datasets {
        uuid id PK
        varchar source_type "csv | xlsx | xls | postgres_query"
        uuid upload_id FK
        uuid source_connection_id FK
        varchar query_key
        jsonb query_params
        varchar query_hash
        jsonb column_mapping
        varchar fingerprint "SHA-256 of normalized records"
        int row_count
        int rejected_row_count
        bool is_demo
    }
    dataset_records {
        bigint id PK
        uuid dataset_id FK
        int row_number "source row"
        varchar business_key
        varchar source_record_id
        timestamptz occurred_at
        numeric amount
        numeric refund_amount
        varchar currency
        varchar status
        jsonb attributes
        varchar raw_payload_hash
    }
    import_issues {
        bigint id PK
        uuid dataset_id FK
        int row_number
        varchar column_name
        text raw_value
        text message
    }
    metric_definitions {
        uuid id PK
        varchar name
        int version
        varchar aggregation
        varchar value_field
        varchar grain
        jsonb filters
        timestamp window_start
        timestamp window_end
        varchar timezone
        varchar refund_policy
        varchar null_policy
    }
    investigations {
        uuid id PK
        uuid left_metric_id FK
        uuid right_metric_id FK
        varchar status "open | in_review | resolved | archived"
        numeric baseline_left
        numeric baseline_right
        numeric baseline_delta
        numeric explained_delta
        numeric unexplained_delta
        jsonb config_json "DNA, replay, proof SQL"
        varchar engine_version
        bool is_demo
    }
    investigation_inputs {
        uuid id PK
        uuid investigation_id FK
        varchar side
        uuid dataset_id FK
        varchar dataset_fingerprint
        int row_count
    }
    comparison_results {
        uuid investigation_id PK
        int matched_keys
        int left_only_keys
        int right_only_keys
        int mismatched_keys
        jsonb summary_json
    }
    findings {
        uuid id PK
        uuid investigation_id FK
        int step_order
        varchar type
        varchar evidence_status "confirmed | suspected | unexplained"
        numeric amount_impact "NULL when suspected"
        varchar rule_id
        jsonb calculation_json
        varchar proof_hash
        int affected_row_count
    }
    evidence_records {
        bigint id PK
        uuid finding_id FK
        bigint dataset_record_id FK
        varchar side
        varchar business_key
        numeric left_amount
        numeric right_amount
        numeric contribution
        jsonb detail
    }
    simulations {
        uuid id PK
        uuid investigation_id FK
        varchar rule_type
        jsonb parameters_json
        numeric before_delta
        numeric after_delta
        numeric resolved_amount
        jsonb result_json
    }
    resolution_notes {
        uuid id PK
        uuid investigation_id FK
        uuid finding_id FK
        varchar decision "accepted | rejected | comment"
        text note
        varchar reviewer
    }
    audit_events {
        bigint id PK
        varchar actor
        varchar action
        varchar target_type
        varchar target_id
        timestamptz occurred_at
        jsonb metadata_json
    }
```

`audit_events` refers to its target by type and id rather than a foreign key, so the history outlives the rows it describes. Every column of every table is listed in the migrations; the diagram leaves out timestamps and a few descriptive columns.
