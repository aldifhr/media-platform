-- 2026-09-12: ikiru domain migration 07 → 08
UPDATE recent_chapters SET cover = REPLACE(cover, '07.ikiru.wtf', '08.ikiru.wtf') WHERE source='ikiru' AND cover LIKE '%07.ikiru.wtf%';
UPDATE recent_chapters SET chapter_url = REPLACE(chapter_url, '07.ikiru.wtf', '08.ikiru.wtf') WHERE source='ikiru' AND chapter_url LIKE '%07.ikiru.wtf%';
UPDATE recent_chapters SET series_url = REPLACE(series_url, '07.ikiru.wtf', '08.ikiru.wtf') WHERE source='ikiru' AND series_url LIKE '%07.ikiru.wtf%';
UPDATE whitelist SET series_url = REPLACE(series_url, '07.ikiru.wtf', '08.ikiru.wtf') WHERE source='ikiru' AND series_url LIKE '%07.ikiru.wtf%';
UPDATE whitelist SET url = REPLACE(url, '07.ikiru.wtf', '08.ikiru.wtf') WHERE source='ikiru' AND url LIKE '%07.ikiru.wtf%';
UPDATE dispatch_history SET chapter_url = REPLACE(chapter_url, '07.ikiru.wtf', '08.ikiru.wtf') WHERE source='ikiru' AND chapter_url LIKE '%07.ikiru.wtf%';
