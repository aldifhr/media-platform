-- 065_voratoon_domain_update.sql
-- One-shot: update all voratoon URLs from v1 → v2

BEGIN;
UPDATE recent_chapters SET series_url = REPLACE(series_url, 'v1.voratoon.com', 'v2.voratoon.com'), chapter_url = REPLACE(chapter_url, 'v1.voratoon.com', 'v2.voratoon.com') WHERE series_url LIKE '%v1.voratoon%' OR chapter_url LIKE '%v1.voratoon%';
UPDATE whitelist SET series_url = REPLACE(series_url, 'v1.voratoon.com', 'v2.voratoon.com'), url = REPLACE(url, 'v1.voratoon.com', 'v2.voratoon.com') WHERE series_url LIKE '%v1.voratoon%' OR url LIKE '%v1.voratoon%';
UPDATE dispatch_history SET series_url = REPLACE(series_url, 'v1.voratoon.com', 'v2.voratoon.com'), chapter_url = REPLACE(chapter_url, 'v1.voratoon.com', 'v2.voratoon.com') WHERE series_url LIKE '%v1.voratoon%' OR chapter_url LIKE '%v1.voratoon%';
UPDATE chapter_bookmarks SET chapter_url = REPLACE(chapter_url, 'v1.voratoon.com', 'v2.voratoon.com') WHERE chapter_url LIKE '%v1.voratoon%';
COMMIT;
