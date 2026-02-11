-- File: /db/migrations/0010_serials_publications.sql
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'serial_acquisition_type') THEN
        CREATE TYPE serial_acquisition_type AS ENUM ('DONATION', 'SUBSCRIPTION');
    END IF;
END $$;

CREATE TABLE IF NOT EXISTS serial_publications (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    title TEXT NOT NULL,
    issn TEXT,
    acquisition_type serial_acquisition_type NOT NULL,
    shelf_section TEXT NOT NULL,
    shelf_row INTEGER,
    shelf_column INTEGER,
    shelf_note TEXT,
    remark TEXT,
    created_by UUID REFERENCES users(id),
    updated_by UUID REFERENCES users(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_serial_publications_title ON serial_publications(title);
CREATE INDEX IF NOT EXISTS idx_serial_publications_issn ON serial_publications(issn);
CREATE INDEX IF NOT EXISTS idx_serial_publications_shelf ON serial_publications(shelf_section);

CREATE TRIGGER trg_serial_publications_updated
BEFORE UPDATE ON serial_publications
FOR EACH ROW EXECUTE PROCEDURE update_timestamp();
