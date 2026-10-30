ALTER TABLE recent_chapters
  ADD COLUMN IF NOT EXISTS scan_status TEXT DEFAULT 'new',
  ADD COLUMN IF NOT EXISTS scan_reason TEXT,
  ADD COLUMN IF NOT EXISTS confidence_score SMALLINT DEFAULT 100;

CREATE INDEX IF NOT EXISTS idx_rc_scan_status ON recent_chapters(scan_status);
