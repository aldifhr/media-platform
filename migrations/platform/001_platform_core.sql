-- Platform schema (shared). Domain schemas (manhwa, media) land in later phases.
CREATE SCHEMA IF NOT EXISTS platform;

CREATE TABLE IF NOT EXISTS platform.guilds (
    id          BIGINT PRIMARY KEY,          -- discord guild id
    name        TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS platform.guild_settings (
    guild_id    BIGINT PRIMARY KEY REFERENCES platform.guilds(id) ON DELETE CASCADE,
    settings    JSONB NOT NULL DEFAULT '{}',
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS platform.modules (
    name        TEXT PRIMARY KEY,            -- 'manhwa' | 'media'
    enabled     BOOLEAN NOT NULL DEFAULT true,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS platform.subscriptions (
    id          BIGSERIAL PRIMARY KEY,
    guild_id    BIGINT NOT NULL REFERENCES platform.guilds(id) ON DELETE CASCADE,
    module      TEXT NOT NULL,
    category    TEXT,                        -- optional source/category filter
    channel_id  BIGINT NOT NULL,
    enabled     BOOLEAN NOT NULL DEFAULT true,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (guild_id, module, category, channel_id)
);

CREATE TABLE IF NOT EXISTS platform.notification_routes (
    id          BIGSERIAL PRIMARY KEY,
    module      TEXT NOT NULL,
    event_type  TEXT NOT NULL,
    rule        JSONB NOT NULL DEFAULT '{}', -- ordered match rules; first match wins
    priority    INT NOT NULL DEFAULT 100,
    channel_id  BIGINT,
    thread_id   BIGINT,
    enabled     BOOLEAN NOT NULL DEFAULT true
);

CREATE TABLE IF NOT EXISTS platform.notification_outbox (
    id               BIGSERIAL PRIMARY KEY,
    event_id         UUID NOT NULL UNIQUE,
    event_type       TEXT NOT NULL,
    version          INT NOT NULL DEFAULT 1,
    source           TEXT NOT NULL,
    occurred_at      TIMESTAMPTZ NOT NULL,
    payload          JSONB NOT NULL DEFAULT '{}',
    status           TEXT NOT NULL DEFAULT 'pending',  -- pending|claimed|delivered|dead
    attempts         INT NOT NULL DEFAULT 0,
    max_attempts     INT NOT NULL DEFAULT 8,
    next_attempt_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_error       TEXT,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_outbox_claim
    ON platform.notification_outbox (status, next_attempt_at);

CREATE TABLE IF NOT EXISTS platform.notification_deliveries (
    id            BIGSERIAL PRIMARY KEY,
    event_id      UUID NOT NULL,
    guild_id      BIGINT,
    channel_id    BIGINT,
    status        TEXT NOT NULL,             -- sent|failed
    error         TEXT,
    delivered_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_deliveries_event
    ON platform.notification_deliveries (event_id);

CREATE TABLE IF NOT EXISTS platform.source_registry (
    id                TEXT PRIMARY KEY,
    module            TEXT NOT NULL,
    category          TEXT NOT NULL,
    enabled           BOOLEAN NOT NULL DEFAULT true,
    timeout_s         DOUBLE PRECISION NOT NULL DEFAULT 30,
    retry             INT NOT NULL DEFAULT 3,
    interval_minutes  INT NOT NULL DEFAULT 30,
    last_success_at   TIMESTAMPTZ,
    error_streak      INT NOT NULL DEFAULT 0,
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);
