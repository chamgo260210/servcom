DO $$ BEGIN
    IF EXISTS (SELECT 1 FROM pg_type WHERE typname = 'serial_acquisition_type') THEN
        IF NOT EXISTS (
            SELECT 1
            FROM pg_enum e
            JOIN pg_type t ON t.oid = e.enumtypid
            WHERE t.typname = 'serial_acquisition_type'
              AND e.enumlabel = 'UNCLASSIFIED'
        ) THEN
            ALTER TYPE serial_acquisition_type ADD VALUE 'UNCLASSIFIED';
        END IF;
    END IF;
END $$;
