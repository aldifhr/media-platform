-- Add origin to series_meta (static metadata: type, rating, genres, description, cover, origin)
ALTER TABLE series_meta ADD COLUMN IF NOT EXISTS origin TEXT DEFAULT '';
COMMENT ON COLUMN series_meta.origin IS 'Origin country code (KR/CN/JP) derived from source API or type';
