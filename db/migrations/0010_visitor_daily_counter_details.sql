-- Store source counter values used to calculate daily visitor counts.
ALTER TABLE IF EXISTS visitor_daily_counts
    ADD COLUMN IF NOT EXISTS previous_total INTEGER,
    ADD COLUMN IF NOT EXISTS count1 INTEGER,
    ADD COLUMN IF NOT EXISTS count2 INTEGER,
    ADD COLUMN IF NOT EXISTS current_total INTEGER;

-- New installations already include this constraint in schema.sql. For existing
-- databases, add it only when no duplicate school_year_id + visit_date rows exist.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'uq_visitor_daily_counts_school_year_visit_date'
    )
    AND NOT EXISTS (
        SELECT 1
        FROM visitor_daily_counts
        GROUP BY school_year_id, visit_date
        HAVING COUNT(*) > 1
    ) THEN
        ALTER TABLE visitor_daily_counts
            ADD CONSTRAINT uq_visitor_daily_counts_school_year_visit_date
            UNIQUE (school_year_id, visit_date);
    END IF;
END $$;
