-- meet-transcribe initial schema
-- 与 docs/design-v2.md 第 4.4 节一致
-- 在 PostgreSQL 16+ 上运行；需要 pgvector 扩展

\set ON_ERROR_STOP on

CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE IF NOT EXISTS tenants (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name                    TEXT NOT NULL UNIQUE,
    quota_concurrent        INTEGER NOT NULL DEFAULT 1,
    quota_minutes_per_day   INTEGER NOT NULL DEFAULT 60,
    data_retention_days     INTEGER NOT NULL DEFAULT 90,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    deleted_at              TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS api_keys (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id   UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    key_hash    BYTEA NOT NULL UNIQUE,
    label       TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    revoked_at  TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_api_keys_tenant ON api_keys(tenant_id);

CREATE TABLE IF NOT EXISTS speakers (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    name            TEXT NOT NULL,
    embedding       vector(192) NOT NULL,
    sample_count    INTEGER NOT NULL DEFAULT 1,
    snr_db_avg      REAL,
    consent_at      TIMESTAMPTZ,
    consent_source  TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    deleted_at      TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_speakers_tenant ON speakers(tenant_id) WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_speakers_embedding
    ON speakers USING hnsw (embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 64);

CREATE TABLE IF NOT EXISTS hotwords (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id   UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    scope       TEXT NOT NULL CHECK (scope IN ('tenant','session')),
    scope_id    TEXT,
    word        TEXT NOT NULL,
    weight      REAL NOT NULL DEFAULT 1.0,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_hotwords_lookup
    ON hotwords(tenant_id, scope, scope_id);

CREATE TABLE IF NOT EXISTS sessions (
    id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id          UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    started_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    ended_at           TIMESTAMPTZ,
    status             TEXT NOT NULL CHECK (status IN ('active','paused','ended','aborted')),
    speaker_set_ref    TEXT
);
CREATE INDEX IF NOT EXISTS idx_sessions_tenant ON sessions(tenant_id, started_at DESC);

CREATE TABLE IF NOT EXISTS transcripts (
    id                      BIGSERIAL PRIMARY KEY,
    session_id              UUID NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    start_sec               REAL NOT NULL,
    end_sec                 REAL NOT NULL,
    speaker_internal_id     TEXT,
    speaker_resolved_id     UUID REFERENCES speakers(id) ON DELETE SET NULL,
    text_encrypted          BYTEA NOT NULL,
    text_iv                 BYTEA NOT NULL,
    text_tag                BYTEA NOT NULL,
    is_final                BOOLEAN NOT NULL,
    seq                     INTEGER NOT NULL,
    stable_until            REAL,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_transcripts_session ON transcripts(session_id, seq);

CREATE TABLE IF NOT EXISTS audit_logs (
    id              BIGSERIAL PRIMARY KEY,
    ts              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    tenant_id       UUID,
    actor           TEXT,
    action          TEXT NOT NULL,
    resource_type   TEXT,
    resource_id     TEXT,
    detail_json     JSONB,
    ip              INET
);
CREATE INDEX IF NOT EXISTS idx_audit_ts ON audit_logs(ts DESC);
CREATE INDEX IF NOT EXISTS idx_audit_tenant ON audit_logs(tenant_id, ts DESC);
