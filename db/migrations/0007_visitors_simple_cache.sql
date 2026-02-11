ALTER TABLE visitor_daily_counts
    DROP COLUMN IF EXISTS count1,
    DROP COLUMN IF EXISTS count2,
    DROP COLUMN IF EXISTS baseline_total,
    DROP COLUMN IF EXISTS daily_override,
    DROP COLUMN IF EXISTS total_count,
    DROP COLUMN IF EXISTS previous_total;

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
