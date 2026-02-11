-- File: /db/migrations/0002_notices.sql

DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'notice_type') THEN
        CREATE TYPE notice_type AS ENUM ('DB_MAINTENANCE', 'SYSTEM_MAINTENANCE', 'WORK_SPECIAL', 'GENERAL');
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'notice_channel') THEN
        CREATE TYPE notice_channel AS ENUM ('POPUP', 'BANNER', 'BOARD');
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'notice_scope') THEN
        CREATE TYPE notice_scope AS ENUM ('ALL', 'ROLE', 'USER');
    END IF;
END $$;

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

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_trigger
        WHERE tgname = 'trg_notices_updated'
    ) THEN
        CREATE TRIGGER trg_notices_updated
        BEFORE UPDATE ON notices
        FOR EACH ROW EXECUTE PROCEDURE update_timestamp();
    END IF;
END$$;
