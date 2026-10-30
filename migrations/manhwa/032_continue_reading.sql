-- Continue reading per-user (session-based)
CREATE TABLE IF NOT EXISTS public.continue_reading (
    id bigserial PRIMARY KEY,
    session_hash text NOT NULL UNIQUE,
    entries jsonb NOT NULL DEFAULT '{}'::jsonb,
    updated_at double precision NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_continue_reading_session ON public.continue_reading (session_hash);
