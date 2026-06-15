CREATE TABLE IF NOT EXISTS data_restore_jobs (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    backup_id UUID REFERENCES data_backups(id),
    domain TEXT NOT NULL,
    mode TEXT NOT NULL,
    status TEXT NOT NULL,
    requested_by UUID REFERENCES users(id),
    started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    finished_at TIMESTAMPTZ,
    error_message TEXT,
    summary JSONB
);

CREATE INDEX IF NOT EXISTS idx_data_restore_jobs_backup ON data_restore_jobs(backup_id);
CREATE INDEX IF NOT EXISTS idx_data_restore_jobs_requested ON data_restore_jobs(requested_by);
CREATE INDEX IF NOT EXISTS idx_data_restore_jobs_started ON data_restore_jobs(started_at);
