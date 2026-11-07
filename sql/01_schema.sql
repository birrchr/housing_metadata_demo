-- =====================================================================
-- Housing metadata model
-- Eight tables, three groups:
--   Reference metadata (source-controlled CSVs, reloaded every build)
--     codeset, code, variable
--   Source mappings (the ONLY place province differences live)
--     source_column_map, source_code_map
--   Annual production (rebuilt per reference year)
--     dataset, dataset_column, record_value
-- =====================================================================

CREATE SCHEMA IF NOT EXISTS meta;

-- Reference + mapping tables are rebuilt every run (drop children first)
DROP TABLE IF EXISTS meta.source_code_map;
DROP TABLE IF EXISTS meta.source_column_map;
DROP TABLE IF EXISTS meta.variable;
DROP TABLE IF EXISTS meta.code;
DROP TABLE IF EXISTS meta.codeset;

-- ---------- Reference metadata --------------------------------------

CREATE TABLE meta.codeset (
    codeset_id      VARCHAR PRIMARY KEY,
    codeset_name    VARCHAR NOT NULL,
    description     VARCHAR
);

CREATE TABLE meta.code (
    codeset_id      VARCHAR NOT NULL REFERENCES meta.codeset (codeset_id),
    code            VARCHAR NOT NULL,
    label_en        VARCHAR NOT NULL,
    sort_order      INTEGER,
    valid_from_year INTEGER NOT NULL,
    valid_to_year   INTEGER,                -- NULL = still valid
    PRIMARY KEY (codeset_id, code, valid_from_year)
);

CREATE TABLE meta.variable (
    variable_id     VARCHAR PRIMARY KEY,    -- canonical, province-neutral
    variable_name   VARCHAR NOT NULL,       -- snake_case analysis name
    label_en        VARCHAR NOT NULL,
    role            VARCHAR NOT NULL CHECK (role IN ('key', 'attribute')),
    data_type       VARCHAR NOT NULL,       -- VARCHAR | INTEGER | DECIMAL | DATE
    codeset_id      VARCHAR REFERENCES meta.codeset (codeset_id),
    unit            VARCHAR,
    valid_from_year INTEGER NOT NULL,
    valid_to_year   INTEGER
);

-- ---------- Source mappings -----------------------------------------

-- Province column name -> canonical variable, by year range
CREATE TABLE meta.source_column_map (
    source_system   VARCHAR NOT NULL,       -- e.g. province code
    source_column   VARCHAR NOT NULL,
    variable_id     VARCHAR NOT NULL REFERENCES meta.variable (variable_id),
    valid_from_year INTEGER NOT NULL,
    valid_to_year   INTEGER,
    notes           VARCHAR,
    PRIMARY KEY (source_system, source_column, valid_from_year)
);

-- Province code value -> canonical code, by year range.
-- If a (source_system, variable_id) pair has no rows, values pass through as-is.
CREATE TABLE meta.source_code_map (
    source_system   VARCHAR NOT NULL,
    variable_id     VARCHAR NOT NULL REFERENCES meta.variable (variable_id),
    source_value    VARCHAR NOT NULL,
    code            VARCHAR NOT NULL,
    valid_from_year INTEGER NOT NULL,
    valid_to_year   INTEGER,
    PRIMARY KEY (source_system, variable_id, source_value, valid_from_year)
);

-- ---------- Annual production ---------------------------------------
-- These are created only if missing so that loading one year
-- does not wipe the others.

-- One row per input Parquet file
CREATE TABLE IF NOT EXISTS meta.dataset (
    dataset_id      VARCHAR PRIMARY KEY,    -- e.g. ON_2025
    source_system   VARCHAR NOT NULL,
    reference_year  INTEGER NOT NULL,
    source_path     VARCHAR NOT NULL,
    record_count    BIGINT,
    loaded_at       TIMESTAMP DEFAULT current_timestamp
);

-- Every column actually found in each file, and what it mapped to (lineage)
CREATE TABLE IF NOT EXISTS meta.dataset_column (
    dataset_id      VARCHAR NOT NULL,
    source_column   VARCHAR NOT NULL,
    source_type     VARCHAR,
    variable_id     VARCHAR,                -- NULL = unmapped column
    PRIMARY KEY (dataset_id, source_column)
);

-- Harmonised values in long form: one row per record x variable
CREATE TABLE IF NOT EXISTS meta.record_value (
    dataset_id      VARCHAR NOT NULL,
    record_id       VARCHAR NOT NULL,       -- value of the 'key' variable
    variable_id     VARCHAR NOT NULL,
    source_value    VARCHAR,                -- exactly as delivered
    value           VARCHAR,                -- canonical (recoded) value
    mapped_via      VARCHAR,                -- 'code_map' | 'passthrough'
    PRIMARY KEY (dataset_id, record_id, variable_id)
);
