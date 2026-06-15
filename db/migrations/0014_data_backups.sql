CREATE TABLE IF NOT EXISTS data_backups (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    domain TEXT NOT NULL,
    backup_type TEXT NOT NULL DEFAULT 'JSON',
    file_name TEXT NOT NULL,
    file_path TEXT NOT NULL,
    file_size BIGINT,
    checksum TEXT,
    schema_version TEXT NOT NULL DEFAULT '1.0.0',
    status TEXT NOT NULL DEFAULT 'READY',
    description TEXT,
    created_by UUID REFERENCES users(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    deleted_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_data_backups_domain ON data_backups(domain);
CREATE INDEX IF NOT EXISTS idx_data_backups_created ON data_backups(created_at);
CREATE INDEX IF NOT EXISTS idx_data_backups_deleted ON data_backups(deleted_at);
