CREATE TABLE IF NOT EXISTS serial_layouts (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    name TEXT NOT NULL,
    width INTEGER NOT NULL DEFAULT 800,
    height INTEGER NOT NULL DEFAULT 500,
    note TEXT,
    created_by UUID REFERENCES users(id),
    updated_by UUID REFERENCES users(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS serial_shelf_types (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    name TEXT NOT NULL,
    width INTEGER NOT NULL DEFAULT 80,
    height INTEGER NOT NULL DEFAULT 40,
    rows INTEGER NOT NULL DEFAULT 5,
    columns INTEGER NOT NULL DEFAULT 5,
    note TEXT,
    created_by UUID REFERENCES users(id),
    updated_by UUID REFERENCES users(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS serial_shelves (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    layout_id UUID NOT NULL REFERENCES serial_layouts(id) ON DELETE CASCADE,
    shelf_type_id UUID NOT NULL REFERENCES serial_shelf_types(id),
    code TEXT NOT NULL,
    x INTEGER NOT NULL DEFAULT 0,
    y INTEGER NOT NULL DEFAULT 0,
    rotation INTEGER NOT NULL DEFAULT 0,
    note TEXT,
    created_by UUID REFERENCES users(id),
    updated_by UUID REFERENCES users(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_serial_shelves_layout ON serial_shelves(layout_id);
CREATE INDEX IF NOT EXISTS idx_serial_shelves_code ON serial_shelves(code);

ALTER TABLE serial_publications
    ADD COLUMN IF NOT EXISTS shelf_id UUID REFERENCES serial_shelves(id);

DO $$ BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_trigger WHERE tgname = 'trg_serial_layouts_updated'
    ) THEN
        CREATE TRIGGER trg_serial_layouts_updated
        BEFORE UPDATE ON serial_layouts
        FOR EACH ROW EXECUTE PROCEDURE update_timestamp();
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_trigger WHERE tgname = 'trg_serial_shelf_types_updated'
    ) THEN
        CREATE TRIGGER trg_serial_shelf_types_updated
        BEFORE UPDATE ON serial_shelf_types
        FOR EACH ROW EXECUTE PROCEDURE update_timestamp();
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_trigger WHERE tgname = 'trg_serial_shelves_updated'
    ) THEN
        CREATE TRIGGER trg_serial_shelves_updated
        BEFORE UPDATE ON serial_shelves
        FOR EACH ROW EXECUTE PROCEDURE update_timestamp();
    END IF;
END $$;
