-- Add walls column to serial_layouts table
ALTER TABLE serial_layouts ADD COLUMN IF NOT EXISTS walls JSONB;
