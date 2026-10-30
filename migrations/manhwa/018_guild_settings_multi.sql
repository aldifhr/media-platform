-- Per-guild notification preferences (multi-server support).
ALTER TABLE guild_settings ADD COLUMN IF NOT EXISTS origin_filter text DEFAULT '';
ALTER TABLE guild_settings ADD COLUMN IF NOT EXISTS excluded_titles text[] DEFAULT '{}';
ALTER TABLE guild_settings ADD COLUMN IF NOT EXISTS label text DEFAULT '';
