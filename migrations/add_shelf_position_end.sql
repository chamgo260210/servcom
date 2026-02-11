-- Migration: Add shelf position end columns for multi-position serial placement
-- File: migrations/add_shelf_position_end.sql

-- Add shelf_row_end and shelf_column_end columns to serial_publications
ALTER TABLE serial_publications
    ADD COLUMN IF NOT EXISTS shelf_row_end INTEGER,
    ADD COLUMN IF NOT EXISTS shelf_column_end INTEGER;

-- Note: These columns allow storing position ranges like:
-- row:1, col:1, row_end:2, col_end:3 means the serial spans rows 1-2 and columns 1-3
