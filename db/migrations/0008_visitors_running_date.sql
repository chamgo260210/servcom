DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE table_name = 'visitor_school_years' AND column_name = 'initial_total'
    ) THEN
        ALTER TABLE visitor_school_years DROP COLUMN initial_total;
    END IF;

    IF EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE table_name = 'visitor_running_totals' AND column_name = 'current_date'
    ) THEN
        ALTER TABLE visitor_running_totals RENAME COLUMN current_date TO running_date;
    END IF;
END$$;
