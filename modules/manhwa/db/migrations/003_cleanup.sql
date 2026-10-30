-- 003_cleanup.sql
-- Hapus data invalid + cek tabel yang ga dipake code

-- ============================================================
-- 1. HAPUS DATA INVALID
-- ============================================================

-- recent_chapters: hapus rows dari pipeline lawas (sebelum fix)
-- yang chapter_num-nya null (ga akan terisi ulang)
delete from recent_chapters
where chapter_num is null;

-- dispatch_history: hapus rows dengan sent_at literal 'now()'
-- (dari bug code lama yang kirim string, bukan timestamp)
delete from dispatch_history
where sent_at::text = 'now()';

-- dispatch_history: hapus rows dengan chapter_url kosong
delete from dispatch_history
where chapter_url is null or chapter_url = '';

-- ============================================================
-- 2. CEK TABEL YANG MUNGKIN GA DIPAKAI
-- ============================================================
-- Jalanin query ini INJECT ke SQL Editor untuk liat semua tabel
select
  table_name,
  table_type,
  obj_description((table_schema || '.' || table_name)::regclass, 'pg_class') as table_comment
from information_schema.tables
where table_schema = 'public'
  and table_type = 'BASE TABLE'
order by table_name;

-- ============================================================
-- 3. DROP TABEL YANG GA ADA DI CODE (kalau ada)
-- ============================================================
-- Yang DIPAKAI code: recent_chapters, dispatch_history, whitelist,
--                     manga_metadata, source_health, cron_run_status, guild_settings
-- Yang MUNGKIN SISA dari Node.js: incidents, subscriptions, dll
--
-- Contoh kalau ada tabel incidents yang ga kepakai:
-- drop table if exists incidents cascade;
--
-- UNCOMMENT sesuai hasil query di step 2:
-- drop table if exists <nama_table> cascade;
