-- File: /db/migrations/0013_serial_layout_and_shelf_type_extensions.sql
-- Consolidates legacy migrations from /migrations into the canonical /db/migrations sequence.

ALTER TABLE serial_publications
    ADD COLUMN IF NOT EXISTS shelf_row_end INTEGER,
    ADD COLUMN IF NOT EXISTS shelf_column_end INTEGER;

ALTER TABLE serial_layouts
    ADD COLUMN IF NOT EXISTS walls JSONB;

ALTER TABLE serial_shelf_types
    ADD COLUMN IF NOT EXISTS color VARCHAR(7);
