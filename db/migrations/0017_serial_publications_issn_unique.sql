-- ISSN values must be unique when present.
-- If existing duplicate non-empty ISSN values exist, clean them up before applying this migration.
CREATE UNIQUE INDEX IF NOT EXISTS uq_serial_publications_issn_not_empty
ON serial_publications (issn)
WHERE issn IS NOT NULL AND issn <> '';
