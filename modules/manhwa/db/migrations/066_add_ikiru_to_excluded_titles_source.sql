-- Add ikiru to excluded_titles source CHECK constraint
ALTER TABLE excluded_titles DROP CONSTRAINT IF EXISTS chk_excluded_titles_source;
ALTER TABLE excluded_titles ADD CONSTRAINT chk_excluded_titles_source
  CHECK (source = ANY (ARRAY['shinigami'::text, 'komiku'::text, 'voratoon'::text, 'ikiru'::text, 'all'::text, ''::text]));
