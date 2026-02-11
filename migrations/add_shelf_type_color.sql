-- Migration: Add color column to serial_shelf_types table
-- Run this script after the existing migrations

ALTER TABLE serial_shelf_types
    ADD COLUMN IF NOT EXISTS color VARCHAR(7);

-- Optional: Set default colors for existing shelf types
-- UPDATE serial_shelf_types SET color = '#3b82f6' WHERE color IS NULL;
