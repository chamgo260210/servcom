-- File: /db/migrations/0005_visitors.sql
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'visitor_period_type') THEN
        CREATE TYPE visitor_period_type AS ENUM ('SEMESTER_1', 'SEMESTER_2', 'SUMMER_BREAK', 'WINTER_BREAK');
    END IF;
END $$;

CREATE TABLE IF NOT EXISTS visitor_school_years (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    academic_year INTEGER NOT NULL UNIQUE,
    label TEXT NOT NULL,
    start_date DATE NOT NULL,
    end_date DATE NOT NULL,
    initial_total INTEGER NOT NULL DEFAULT 0,
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
    count1 INTEGER NOT NULL DEFAULT 0,
    count2 INTEGER NOT NULL DEFAULT 0,
    total_count INTEGER NOT NULL DEFAULT 0,
    previous_total INTEGER NOT NULL DEFAULT 0,
    daily_visitors INTEGER NOT NULL DEFAULT 0,
    created_by UUID REFERENCES users(id),
    updated_by UUID REFERENCES users(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (school_year_id, visit_date)
);
CREATE INDEX IF NOT EXISTS idx_visitor_daily_year ON visitor_daily_counts(school_year_id);
CREATE INDEX IF NOT EXISTS idx_visitor_daily_date ON visitor_daily_counts(visit_date);

CREATE TRIGGER trg_visitor_school_years_updated
BEFORE UPDATE ON visitor_school_years
FOR EACH ROW EXECUTE PROCEDURE update_timestamp();

CREATE TRIGGER trg_visitor_periods_updated
BEFORE UPDATE ON visitor_periods
FOR EACH ROW EXECUTE PROCEDURE update_timestamp();

CREATE TRIGGER trg_visitor_daily_counts_updated
BEFORE UPDATE ON visitor_daily_counts
FOR EACH ROW EXECUTE PROCEDURE update_timestamp();
