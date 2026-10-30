-- dispatch_claims.created_at had no DEFAULT, so every INSERT that omitted the
-- column stored NULL.
--
-- That silently broke unclaim_stale(), which filtered `created_at < cutoff`:
-- NULL < cutoff is never true, so the statement deleted nothing. Claims were
-- therefore never released, and dispatch's already-claimed guard rejected every
-- item — cron reported sent:0 indefinitely while nothing reached Discord.
--
-- Setting the DEFAULT is the durable fix; the caller also sets the value
-- explicitly so this holds for any INSERT that bypasses this column.
alter table dispatch_claims
  alter column created_at set default now();

-- Backfill the NULLs already in the table. Without this the existing stuck rows
-- would stay invisible to the (corrected) expires_at filter only if their
-- expires_at were also in the future — they are not, so they are handled by the
-- expires_at filter anyway; backfilling is for consistency of the column itself.
update dispatch_claims set created_at = now() where created_at is null;
