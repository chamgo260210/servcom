ALTER TABLE data_backups
    ADD COLUMN IF NOT EXISTS kind TEXT NOT NULL DEFAULT 'MANUAL';

UPDATE data_backups
SET kind = 'MANUAL'
WHERE kind IS NULL;

UPDATE data_backups
SET kind = 'RESTORE_POINT'
WHERE file_name LIKE 'pre_restore_%'
   OR file_name LIKE 'restore_point_%';

CREATE INDEX IF NOT EXISTS idx_data_backups_kind ON data_backups(kind);
