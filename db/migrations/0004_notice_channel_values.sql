-- File: /db/migrations/0004_notice_channel_values.sql

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_type t
        JOIN pg_enum e ON t.oid = e.enumtypid
        WHERE t.typname = 'notice_channel'
          AND e.enumlabel = 'POPUP_BANNER'
    ) THEN
        ALTER TYPE notice_channel ADD VALUE IF NOT EXISTS 'POPUP_BANNER';
    END IF;
    IF NOT EXISTS (
        SELECT 1
        FROM pg_type t
        JOIN pg_enum e ON t.oid = e.enumtypid
        WHERE t.typname = 'notice_channel'
          AND e.enumlabel = 'NONE'
    ) THEN
        ALTER TYPE notice_channel ADD VALUE IF NOT EXISTS 'NONE';
    END IF;
    IF NOT EXISTS (
        SELECT 1
        FROM pg_type t
        JOIN pg_enum e ON t.oid = e.enumtypid
        WHERE t.typname = 'notice_channel'
          AND e.enumlabel = 'BOARD'
    ) THEN
        ALTER TYPE notice_channel ADD VALUE IF NOT EXISTS 'BOARD';
    END IF;
END$$;
