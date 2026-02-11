WITH year_bounds AS (
    SELECT
        id AS year_id,
        academic_year,
        start_date AS year_start,
        end_date AS year_end
    FROM visitor_school_years
),
calc AS (
    SELECT
        year_id,
        academic_year,
        year_start,
        year_end,
        (
            make_date(academic_year, 6, 1)
            + (
                ((1 - EXTRACT(DOW FROM make_date(academic_year, 6, 1))::int + 7) % 7)
                + 21
            ) * INTERVAL '1 day'
        )::date AS summer_start,
        (
            (make_date(academic_year, 9, 1) - INTERVAL '1 day')
            - (
                ((
                    EXTRACT(DOW FROM (make_date(academic_year, 9, 1) - INTERVAL '1 day'))::int
                    - 5 + 7
                ) % 7) * INTERVAL '1 day'
            )
        )::date AS summer_end,
        (
            make_date(academic_year, 12, 1)
            + (
                ((1 - EXTRACT(DOW FROM make_date(academic_year, 12, 1))::int + 7) % 7)
                + 21
            ) * INTERVAL '1 day'
        )::date AS winter_start,
        (
            (make_date(academic_year + 1, 3, 1) - INTERVAL '1 day')
            - (
                ((
                    EXTRACT(DOW FROM (make_date(academic_year + 1, 3, 1) - INTERVAL '1 day'))::int
                    - 5 + 7
                ) % 7) * INTERVAL '1 day'
            )
        )::date AS winter_end
    FROM year_bounds
),
ranges AS (
    SELECT
        year_id,
        year_start,
        year_end,
        summer_start,
        summer_end,
        winter_start,
        winter_end,
        year_start AS semester1_start,
        (summer_start - INTERVAL '1 day')::date AS semester1_end,
        (summer_end + INTERVAL '1 day')::date AS semester2_start,
        (winter_start - INTERVAL '1 day')::date AS semester2_end
    FROM calc
),
clamped AS (
    SELECT
        year_id,
        GREATEST(semester1_start, year_start) AS semester1_start,
        LEAST(semester1_end, year_end) AS semester1_end,
        GREATEST(summer_start, year_start) AS summer_start,
        LEAST(summer_end, year_end) AS summer_end,
        GREATEST(semester2_start, year_start) AS semester2_start,
        LEAST(semester2_end, year_end) AS semester2_end,
        GREATEST(winter_start, year_start) AS winter_start,
        LEAST(winter_end, year_end) AS winter_end
    FROM ranges
),
fixed AS (
    SELECT
        year_id,
        semester1_start,
        CASE WHEN semester1_end < semester1_start THEN semester1_start ELSE semester1_end END AS semester1_end,
        summer_start,
        CASE WHEN summer_end < summer_start THEN summer_start ELSE summer_end END AS summer_end,
        semester2_start,
        CASE WHEN semester2_end < semester2_start THEN semester2_start ELSE semester2_end END AS semester2_end,
        winter_start,
        CASE WHEN winter_end < winter_start THEN winter_start ELSE winter_end END AS winter_end
    FROM clamped
)
UPDATE visitor_periods AS p
SET
    start_date = COALESCE(
        p.start_date,
        CASE p.period_type
            WHEN 'SEMESTER_1' THEN f.semester1_start
            WHEN 'SUMMER_BREAK' THEN f.summer_start
            WHEN 'SEMESTER_2' THEN f.semester2_start
            WHEN 'WINTER_BREAK' THEN f.winter_start
        END
    ),
    end_date = COALESCE(
        p.end_date,
        CASE p.period_type
            WHEN 'SEMESTER_1' THEN f.semester1_end
            WHEN 'SUMMER_BREAK' THEN f.summer_end
            WHEN 'SEMESTER_2' THEN f.semester2_end
            WHEN 'WINTER_BREAK' THEN f.winter_end
        END
    )
FROM fixed AS f
WHERE p.school_year_id = f.year_id
  AND (p.start_date IS NULL OR p.end_date IS NULL);

DELETE FROM visitor_period_stats;

INSERT INTO visitor_period_stats (
    school_year_id,
    period_id,
    total_visitors,
    open_days,
    created_at,
    updated_at
)
SELECT
    p.school_year_id,
    p.id,
    COALESCE(SUM(d.daily_visitors), 0) AS total_visitors,
    COUNT(d.id) AS open_days,
    NOW(),
    NOW()
FROM visitor_periods p
LEFT JOIN visitor_daily_counts d
    ON d.school_year_id = p.school_year_id
    AND d.visit_date >= p.start_date
    AND d.visit_date <= p.end_date
WHERE p.start_date IS NOT NULL
  AND p.end_date IS NOT NULL
GROUP BY p.school_year_id, p.id;
