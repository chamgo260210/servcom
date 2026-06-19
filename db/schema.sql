-- File: /db/schema.sql
-- PostgreSQL schema for Dasan Information Center shift management

CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'user_role') THEN
        CREATE TYPE user_role AS ENUM ('MASTER', 'OPERATOR', 'MEMBER');
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'request_type') THEN
        CREATE TYPE request_type AS ENUM ('ABSENCE', 'EXTRA');
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'request_status') THEN
        CREATE TYPE request_status AS ENUM ('PENDING', 'APPROVED', 'REJECTED', 'CANCELLED');
    ELSE
        IF NOT EXISTS (
            SELECT 1
            FROM pg_type t
            JOIN pg_enum e ON t.oid = e.enumtypid
            WHERE t.typname = 'request_status'
              AND e.enumlabel = 'CANCELLED'
        ) THEN
            ALTER TYPE request_status ADD VALUE IF NOT EXISTS 'CANCELLED';
        END IF;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'notice_type') THEN
        CREATE TYPE notice_type AS ENUM ('DB_MAINTENANCE', 'SYSTEM_MAINTENANCE', 'WORK_SPECIAL', 'GENERAL');
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'notice_channel') THEN
        CREATE TYPE notice_channel AS ENUM ('POPUP', 'BANNER', 'POPUP_BANNER', 'NONE', 'BOARD');
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'notice_scope') THEN
        CREATE TYPE notice_scope AS ENUM ('ALL', 'ROLE', 'USER');
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'visitor_period_type') THEN
        CREATE TYPE visitor_period_type AS ENUM ('SEMESTER_1', 'SEMESTER_2', 'SUMMER_BREAK', 'WINTER_BREAK');
    END IF;
END $$;

CREATE TABLE IF NOT EXISTS users (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    name TEXT NOT NULL,
    identifier TEXT UNIQUE,
    role user_role NOT NULL DEFAULT 'MEMBER',
    active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS auth_accounts (
    user_id UUID PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    login_id TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    last_login_at TIMESTAMPTZ,
    session_version INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS shifts (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    name TEXT NOT NULL,
    weekday SMALLINT NOT NULL CHECK (weekday BETWEEN 0 AND 6),
    start_time TIME NOT NULL,
    end_time TIME NOT NULL,
    location TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CHECK (start_time < end_time)
);

CREATE TABLE IF NOT EXISTS user_shifts (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    shift_id UUID NOT NULL REFERENCES shifts(id) ON DELETE CASCADE,
    valid_from DATE NOT NULL,
    valid_to DATE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_user_shifts_user ON user_shifts(user_id);
CREATE INDEX IF NOT EXISTS idx_user_shifts_shift ON user_shifts(shift_id);

CREATE TABLE IF NOT EXISTS shift_requests (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    type request_type NOT NULL,
    target_date DATE NOT NULL,
    target_shift_id UUID NOT NULL REFERENCES shifts(id) ON DELETE CASCADE,
    target_start_time TIME,
    target_end_time TIME,
    reason TEXT,
    status request_status NOT NULL DEFAULT 'PENDING',
    operator_id UUID REFERENCES users(id),
    decided_at TIMESTAMPTZ,
    cancelled_after_approval BOOLEAN NOT NULL DEFAULT FALSE,
    cancel_reason TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_shift_requests_user ON shift_requests(user_id);
CREATE INDEX IF NOT EXISTS idx_shift_requests_status ON shift_requests(status);
CREATE INDEX IF NOT EXISTS idx_shift_requests_type ON shift_requests(type);

CREATE TABLE IF NOT EXISTS audit_logs (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    actor_user_id UUID REFERENCES users(id),
    action_type TEXT NOT NULL,
    target_user_id UUID REFERENCES users(id),
    request_id UUID REFERENCES shift_requests(id),
    details JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_audit_logs_actor ON audit_logs(actor_user_id);
CREATE INDEX IF NOT EXISTS idx_audit_logs_created ON audit_logs(created_at);

CREATE TABLE IF NOT EXISTS data_backups (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    domain TEXT NOT NULL,
    backup_type TEXT NOT NULL DEFAULT 'JSON',
    kind TEXT NOT NULL DEFAULT 'MANUAL',
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
CREATE INDEX IF NOT EXISTS idx_data_backups_kind ON data_backups(kind);
CREATE INDEX IF NOT EXISTS idx_data_backups_created ON data_backups(created_at);
CREATE INDEX IF NOT EXISTS idx_data_backups_deleted ON data_backups(deleted_at);

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

CREATE TABLE IF NOT EXISTS notices (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    title TEXT NOT NULL,
    body TEXT NOT NULL,
    type notice_type NOT NULL,
    channel notice_channel NOT NULL,
    scope notice_scope NOT NULL DEFAULT 'ALL',
    target_roles JSONB,
    priority INTEGER NOT NULL DEFAULT 0,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    start_at TIMESTAMPTZ,
    end_at TIMESTAMPTZ,
    created_by UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_notices_active ON notices(is_active);
CREATE INDEX IF NOT EXISTS idx_notices_channel ON notices(channel);
CREATE INDEX IF NOT EXISTS idx_notices_window ON notices(start_at, end_at);
CREATE INDEX IF NOT EXISTS idx_notices_priority ON notices(priority);

CREATE TABLE IF NOT EXISTS notice_targets (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    notice_id UUID NOT NULL REFERENCES notices(id) ON DELETE CASCADE,
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_notice_targets_unique ON notice_targets(notice_id, user_id);

CREATE TABLE IF NOT EXISTS notice_reads (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    notice_id UUID NOT NULL REFERENCES notices(id) ON DELETE CASCADE,
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    channel notice_channel NOT NULL,
    read_at TIMESTAMPTZ,
    dismissed_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_notice_reads_unique ON notice_reads(notice_id, user_id, channel);
CREATE INDEX IF NOT EXISTS idx_notice_reads_user ON notice_reads(user_id);

CREATE TABLE IF NOT EXISTS visitor_school_years (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    academic_year INTEGER NOT NULL UNIQUE,
    label TEXT NOT NULL,
    start_date DATE NOT NULL,
    end_date DATE NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS visitor_periods (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    school_year_id UUID NOT NULL REFERENCES visitor_school_years(id) ON DELETE CASCADE,
    period_type visitor_period_type NOT NULL,
    name TEXT NOT NULL,
    start_date DATE,
    end_date DATE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (school_year_id, period_type)
);
CREATE INDEX IF NOT EXISTS idx_visitor_periods_year ON visitor_periods(school_year_id);

CREATE TABLE IF NOT EXISTS visitor_daily_counts (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    school_year_id UUID NOT NULL REFERENCES visitor_school_years(id) ON DELETE CASCADE,
    visit_date DATE NOT NULL,
    daily_visitors INTEGER NOT NULL DEFAULT 0,
    created_by UUID REFERENCES users(id),
    updated_by UUID REFERENCES users(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (school_year_id, visit_date)
);
CREATE INDEX IF NOT EXISTS idx_visitor_daily_year ON visitor_daily_counts(school_year_id);
CREATE INDEX IF NOT EXISTS idx_visitor_daily_date ON visitor_daily_counts(visit_date);

CREATE TABLE IF NOT EXISTS visitor_running_totals (
    school_year_id UUID PRIMARY KEY REFERENCES visitor_school_years(id) ON DELETE CASCADE,
    previous_total INTEGER,
    current_total INTEGER,
    running_date DATE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS visitor_monthly_stats (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    school_year_id UUID NOT NULL REFERENCES visitor_school_years(id) ON DELETE CASCADE,
    year INTEGER NOT NULL,
    month INTEGER NOT NULL,
    total_visitors INTEGER NOT NULL DEFAULT 0,
    open_days INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (school_year_id, year, month)
);
CREATE INDEX IF NOT EXISTS idx_visitor_monthly_year ON visitor_monthly_stats(school_year_id);

CREATE TABLE IF NOT EXISTS visitor_period_stats (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    school_year_id UUID NOT NULL REFERENCES visitor_school_years(id) ON DELETE CASCADE,
    period_id UUID NOT NULL REFERENCES visitor_periods(id) ON DELETE CASCADE,
    total_visitors INTEGER NOT NULL DEFAULT 0,
    open_days INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (school_year_id, period_id)
);
CREATE INDEX IF NOT EXISTS idx_visitor_period_stats_year ON visitor_period_stats(school_year_id);

CREATE TABLE IF NOT EXISTS visitor_year_stats (
    school_year_id UUID PRIMARY KEY REFERENCES visitor_school_years(id) ON DELETE CASCADE,
    total_visitors INTEGER NOT NULL DEFAULT 0,
    open_days INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE OR REPLACE FUNCTION update_timestamp()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_users_updated
BEFORE UPDATE ON users
FOR EACH ROW EXECUTE PROCEDURE update_timestamp();

CREATE TRIGGER trg_shifts_updated
BEFORE UPDATE ON shifts
FOR EACH ROW EXECUTE PROCEDURE update_timestamp();

CREATE TRIGGER trg_user_shifts_updated
BEFORE UPDATE ON user_shifts
FOR EACH ROW EXECUTE PROCEDURE update_timestamp();

CREATE TRIGGER trg_shift_requests_updated
BEFORE UPDATE ON shift_requests
FOR EACH ROW EXECUTE PROCEDURE update_timestamp();

CREATE TRIGGER trg_notices_updated
BEFORE UPDATE ON notices
FOR EACH ROW EXECUTE PROCEDURE update_timestamp();

CREATE TRIGGER trg_visitor_school_years_updated
BEFORE UPDATE ON visitor_school_years
FOR EACH ROW EXECUTE PROCEDURE update_timestamp();

CREATE TRIGGER trg_visitor_periods_updated
BEFORE UPDATE ON visitor_periods
FOR EACH ROW EXECUTE PROCEDURE update_timestamp();

CREATE TRIGGER trg_visitor_daily_counts_updated
BEFORE UPDATE ON visitor_daily_counts
FOR EACH ROW EXECUTE PROCEDURE update_timestamp();

CREATE TRIGGER trg_visitor_running_totals_updated
BEFORE UPDATE ON visitor_running_totals
FOR EACH ROW EXECUTE PROCEDURE update_timestamp();

CREATE TRIGGER trg_visitor_monthly_stats_updated
BEFORE UPDATE ON visitor_monthly_stats
FOR EACH ROW EXECUTE PROCEDURE update_timestamp();

CREATE TRIGGER trg_visitor_period_stats_updated
BEFORE UPDATE ON visitor_period_stats
FOR EACH ROW EXECUTE PROCEDURE update_timestamp();

CREATE TRIGGER trg_visitor_year_stats_updated
BEFORE UPDATE ON visitor_year_stats
FOR EACH ROW EXECUTE PROCEDURE update_timestamp();

DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'serial_acquisition_type') THEN
        CREATE TYPE serial_acquisition_type AS ENUM ('UNCLASSIFIED', 'DONATION', 'SUBSCRIPTION');
    END IF;
END $$;

CREATE TABLE IF NOT EXISTS serial_publications (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    title TEXT NOT NULL,
    issn TEXT,
    acquisition_type serial_acquisition_type NOT NULL,
    shelf_section TEXT NOT NULL,
    shelf_row INTEGER,
    shelf_column INTEGER,
    shelf_row_end INTEGER,
    shelf_column_end INTEGER,
    shelf_note TEXT,
    remark TEXT,
    created_by UUID REFERENCES users(id),
    updated_by UUID REFERENCES users(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_serial_publications_title ON serial_publications(title);
CREATE INDEX IF NOT EXISTS idx_serial_publications_issn ON serial_publications(issn);
CREATE UNIQUE INDEX IF NOT EXISTS uq_serial_publications_issn_not_empty
ON serial_publications(issn)
WHERE issn IS NOT NULL AND issn <> '';
CREATE INDEX IF NOT EXISTS idx_serial_publications_shelf ON serial_publications(shelf_section);

CREATE TRIGGER trg_serial_publications_updated
BEFORE UPDATE ON serial_publications
FOR EACH ROW EXECUTE PROCEDURE update_timestamp();

CREATE TABLE IF NOT EXISTS serial_layouts (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    name TEXT NOT NULL,
    width INTEGER NOT NULL DEFAULT 800,
    height INTEGER NOT NULL DEFAULT 500,
    note TEXT,
    walls JSONB,
    created_by UUID REFERENCES users(id),
    updated_by UUID REFERENCES users(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS serial_shelf_types (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    name TEXT NOT NULL,
    width INTEGER NOT NULL DEFAULT 80,
    height INTEGER NOT NULL DEFAULT 40,
    rows INTEGER NOT NULL DEFAULT 5,
    columns INTEGER NOT NULL DEFAULT 5,
    color VARCHAR(7),
    note TEXT,
    created_by UUID REFERENCES users(id),
    updated_by UUID REFERENCES users(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS serial_shelves (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    layout_id UUID NOT NULL REFERENCES serial_layouts(id) ON DELETE CASCADE,
    shelf_type_id UUID NOT NULL REFERENCES serial_shelf_types(id),
    code TEXT NOT NULL,
    x INTEGER NOT NULL DEFAULT 0,
    y INTEGER NOT NULL DEFAULT 0,
    rotation INTEGER NOT NULL DEFAULT 0,
    note TEXT,
    created_by UUID REFERENCES users(id),
    updated_by UUID REFERENCES users(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

ALTER TABLE serial_publications
    ADD COLUMN IF NOT EXISTS shelf_id UUID REFERENCES serial_shelves(id);

CREATE INDEX IF NOT EXISTS idx_serial_shelves_layout ON serial_shelves(layout_id);
CREATE INDEX IF NOT EXISTS idx_serial_shelves_code ON serial_shelves(code);

CREATE TRIGGER trg_serial_layouts_updated
BEFORE UPDATE ON serial_layouts
FOR EACH ROW EXECUTE PROCEDURE update_timestamp();

CREATE TRIGGER trg_serial_shelf_types_updated
BEFORE UPDATE ON serial_shelf_types
FOR EACH ROW EXECUTE PROCEDURE update_timestamp();

CREATE TRIGGER trg_serial_shelves_updated
BEFORE UPDATE ON serial_shelves
FOR EACH ROW EXECUTE PROCEDURE update_timestamp();
