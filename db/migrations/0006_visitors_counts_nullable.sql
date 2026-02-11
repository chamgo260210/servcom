-- Make visitor count fields optional and add baseline/daily override fields if missing.
ALTER TABLE IF EXISTS visitor_daily_counts
    ADD COLUMN IF NOT EXISTS baseline_total INTEGER,
    ADD COLUMN IF NOT EXISTS daily_override INTEGER;

ALTER TABLE IF EXISTS visitor_daily_counts
    ALTER COLUMN count1 DROP NOT NULL,
    ALTER COLUMN count2 DROP NOT NULL;

ALTER TABLE IF EXISTS visitor_daily_counts
    ALTER COLUMN count1 DROP DEFAULT,
    ALTER COLUMN count2 DROP DEFAULT;
