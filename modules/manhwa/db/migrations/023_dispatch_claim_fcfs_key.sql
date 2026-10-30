-- Migration 023: claim dispatch by FCFS key (not chapter_url) to stop
-- cross-source double-sends (ikiru + shinigami same title+chapter = 1 notify).
ALTER TABLE dispatch_claims ADD COLUMN IF NOT EXISTS fcfs_key text;
-- Unique on fcfs_key: the FIRST source to claim a title+chapter wins;
-- the second source (different URL, same fcfs_key) cannot claim -> no 2nd send.
CREATE UNIQUE INDEX IF NOT EXISTS dispatch_claims_fcfs_key_key ON dispatch_claims (fcfs_key)
  WHERE fcfs_key IS NOT NULL;
