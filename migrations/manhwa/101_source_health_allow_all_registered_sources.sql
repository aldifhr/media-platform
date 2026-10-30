-- 101: source_health.source was pinned to a hardcoded manhwa-only list
-- (shinigami/komiku/voratoon/ikiru), so the media scheduler could not record
-- health for pahe/ovagames/skidrow/game3rb/drakorasia — every media source
-- showed "never" on the ops dashboard. Replace the CHECK with a foreign key to
-- platform.source_registry so any registered source works, and a source
-- removed from the registry takes its health row with it.
ALTER TABLE public.source_health DROP CONSTRAINT IF EXISTS chk_source_health_source;

DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.tables
        WHERE table_schema = 'platform' AND table_name = 'source_registry'
    ) THEN
        IF NOT EXISTS (
            SELECT 1 FROM pg_constraint
            WHERE conname = 'source_health_source_fkey'
              AND conrelid = 'public.source_health'::regclass
        ) THEN
            ALTER TABLE public.source_health
                ADD CONSTRAINT source_health_source_fkey
                FOREIGN KEY (source)
                REFERENCES platform.source_registry (id) ON DELETE CASCADE;
        END IF;
    END IF;
END $$;
