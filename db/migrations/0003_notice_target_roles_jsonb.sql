-- File: /db/migrations/0003_notice_target_roles_jsonb.sql

ALTER TABLE notices
    ALTER COLUMN target_roles TYPE JSONB
    USING CASE
        WHEN target_roles IS NULL THEN NULL
        ELSE target_roles::jsonb
    END;
