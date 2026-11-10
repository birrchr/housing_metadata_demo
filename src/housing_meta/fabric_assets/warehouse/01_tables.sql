-- =====================================================================
-- Housing metadata model: Fabric Warehouse objects (run once, in wh_housing)
--
-- Fabric Warehouse rules this script follows:
--   * varchar only (UTF-8), no nvarchar
--   * IDENTITY must be BIGINT, no seed/increment
--   * PRIMARY / FOREIGN keys only as NOT ENFORCED (documentation + optimiser)
--   * datetime2 precision max 6
-- Integrity is enforced by audit.usp_load_run and the QA checks, not the engine.
-- =====================================================================

IF SCHEMA_ID('meta')  IS NULL EXEC('CREATE SCHEMA meta');
IF SCHEMA_ID('dw')    IS NULL EXEC('CREATE SCHEMA dw');
IF SCHEMA_ID('qa')    IS NULL EXEC('CREATE SCHEMA qa');
IF SCHEMA_ID('audit') IS NULL EXEC('CREATE SCHEMA audit');
GO

-- ---------- audit: one row per pipeline run ---------------------------
CREATE TABLE audit.load_run (
    run_id            VARCHAR(64)   NOT NULL,
    source_system     VARCHAR(16)   NOT NULL,
    reference_year    INT           NOT NULL,
    metadata_release  VARCHAR(64)   NOT NULL,
    mode              VARCHAR(16)   NOT NULL,   -- validate | publish
    status            VARCHAR(32)   NOT NULL,   -- running | validated | published | failed_qa | failed
    started_at        DATETIME2(6)  NOT NULL,
    finished_at       DATETIME2(6)  NULL,
    blocking_issues   INT           NULL,
    warning_issues    INT           NULL,
    rows_loaded       BIGINT        NULL,
    message           VARCHAR(4000) NULL
);

-- ---------- meta: version history of all five metadata tables ---------
-- One row per metadata row per version (SCD type 2). row_sig is the full row
-- as JSON, so any change to a label, mapping or validity opens a new version.
CREATE TABLE meta.metadata_history (
    table_name        VARCHAR(64)   NOT NULL,   -- codeset | code | variable | source_column_map | source_code_map
    row_key           VARCHAR(400)  NOT NULL,   -- natural key, '|'-joined
    row_sig           VARCHAR(4000) NOT NULL,
    source_system     VARCHAR(16)   NULL,       -- mapping tables only
    valid_from_year   INT           NULL,
    valid_to_year     INT           NULL,
    metadata_release  VARCHAR(64)   NOT NULL,
    effective_from    DATETIME2(6)  NOT NULL,
    effective_to      DATETIME2(6)  NULL,
    is_current        BIT           NOT NULL,
    run_id            VARCHAR(64)   NOT NULL
);

-- ---------- dw: dimensions (type 1; history lives in meta.metadata_history)
CREATE TABLE dw.dim_source_system (
    source_system_key BIGINT IDENTITY,
    source_system     VARCHAR(16)   NOT NULL,
    first_year        INT           NULL,
    last_year         INT           NULL
);

CREATE TABLE dw.dim_variable (
    variable_key      BIGINT IDENTITY,
    variable_id       VARCHAR(64)   NOT NULL,
    variable_name     VARCHAR(128)  NOT NULL,
    label_en          VARCHAR(400)  NOT NULL,
    role              VARCHAR(16)   NOT NULL,
    data_type         VARCHAR(32)   NOT NULL,
    codeset_id        VARCHAR(64)   NULL,
    unit              VARCHAR(64)   NULL,
    valid_from_year   INT           NOT NULL,
    valid_to_year     INT           NULL,
    metadata_release  VARCHAR(64)   NOT NULL,
    updated_at        DATETIME2(6)  NOT NULL
);

CREATE TABLE dw.dim_code (
    code_key          BIGINT IDENTITY,
    codeset_id        VARCHAR(64)   NOT NULL,
    codeset_name      VARCHAR(400)  NULL,
    code              VARCHAR(64)   NOT NULL,
    label_en          VARCHAR(400)  NOT NULL,
    sort_order        INT           NULL,
    valid_from_year   INT           NOT NULL,
    valid_to_year     INT           NULL,
    metadata_release  VARCHAR(64)   NOT NULL,
    updated_at        DATETIME2(6)  NOT NULL
);

CREATE TABLE dw.dim_dataset (
    dataset_key       BIGINT IDENTITY,
    dataset_id        VARCHAR(64)   NOT NULL,   -- e.g. ON_2025
    source_system     VARCHAR(16)   NOT NULL,
    reference_year    INT           NOT NULL,
    source_path       VARCHAR(1000) NULL,
    record_count      BIGINT        NULL,
    run_id            VARCHAR(64)   NOT NULL,   -- run that last published it
    metadata_release  VARCHAR(64)   NOT NULL,   -- release it was published with
    loaded_at         DATETIME2(6)  NOT NULL
);

-- ---------- dw: facts -------------------------------------------------
-- Long form: one row per record x variable. The source for every variable.
CREATE TABLE dw.fact_record_value (
    dataset_key       BIGINT        NOT NULL,
    record_id         VARCHAR(128)  NOT NULL,
    variable_key      BIGINT        NOT NULL,
    code_key          BIGINT        NULL,       -- coded variables only
    source_value      VARCHAR(4000) NULL,       -- as delivered
    value             VARCHAR(4000) NULL,       -- canonical
    value_num         FLOAT         NULL,       -- numeric variables only
    mapped_via        VARCHAR(16)   NULL,       -- code_map | passthrough
    run_id            VARCHAR(64)   NOT NULL
);

-- Lineage: every column found in each delivery and what it mapped to
CREATE TABLE dw.fact_dataset_column (
    dataset_key       BIGINT        NOT NULL,
    source_column     VARCHAR(256)  NOT NULL,
    source_type       VARCHAR(64)   NULL,
    variable_key      BIGINT        NULL,       -- NULL = unmapped
    run_id            VARCHAR(64)   NOT NULL
);

-- dw.fact_household (wide, typed) is generated by audit.usp_load_run from
-- dim_variable, so a new variable becomes a new column without a code change.

-- ---------- qa: results of every run ----------------------------------
CREATE TABLE qa.check_summary (
    run_id            VARCHAR(64)   NOT NULL,
    source_system     VARCHAR(16)   NOT NULL,
    reference_year    INT           NOT NULL,
    check_name        VARCHAR(64)   NOT NULL,
    n_issues          INT           NOT NULL,
    is_blocking       BIT           NOT NULL,
    checked_at        DATETIME2(6)  NOT NULL
);

CREATE TABLE qa.check_detail (
    run_id            VARCHAR(64)   NOT NULL,
    check_name        VARCHAR(64)   NOT NULL,
    dataset_id        VARCHAR(64)   NULL,
    detail            VARCHAR(4000) NULL        -- the QA row as JSON
);
GO

-- ---------- keys (NOT ENFORCED: documentation, optimiser, Power BI) ----
ALTER TABLE dw.dim_source_system ADD CONSTRAINT pk_dim_source_system PRIMARY KEY NONCLUSTERED (source_system_key) NOT ENFORCED;
ALTER TABLE dw.dim_variable      ADD CONSTRAINT pk_dim_variable      PRIMARY KEY NONCLUSTERED (variable_key)      NOT ENFORCED;
ALTER TABLE dw.dim_code          ADD CONSTRAINT pk_dim_code          PRIMARY KEY NONCLUSTERED (code_key)          NOT ENFORCED;
ALTER TABLE dw.dim_dataset       ADD CONSTRAINT pk_dim_dataset       PRIMARY KEY NONCLUSTERED (dataset_key)       NOT ENFORCED;
ALTER TABLE audit.load_run       ADD CONSTRAINT pk_load_run          PRIMARY KEY NONCLUSTERED (run_id)            NOT ENFORCED;

ALTER TABLE dw.dim_variable ADD CONSTRAINT uq_dim_variable UNIQUE NONCLUSTERED (variable_id) NOT ENFORCED;
ALTER TABLE dw.dim_dataset  ADD CONSTRAINT uq_dim_dataset  UNIQUE NONCLUSTERED (dataset_id)  NOT ENFORCED;

ALTER TABLE dw.fact_record_value   ADD CONSTRAINT fk_frv_dataset  FOREIGN KEY (dataset_key)  REFERENCES dw.dim_dataset (dataset_key)   NOT ENFORCED;
ALTER TABLE dw.fact_record_value   ADD CONSTRAINT fk_frv_variable FOREIGN KEY (variable_key) REFERENCES dw.dim_variable (variable_key) NOT ENFORCED;
ALTER TABLE dw.fact_record_value   ADD CONSTRAINT fk_frv_code     FOREIGN KEY (code_key)     REFERENCES dw.dim_code (code_key)         NOT ENFORCED;
ALTER TABLE dw.fact_dataset_column ADD CONSTRAINT fk_fdc_dataset  FOREIGN KEY (dataset_key)  REFERENCES dw.dim_dataset (dataset_key)   NOT ENFORCED;
ALTER TABLE dw.fact_dataset_column ADD CONSTRAINT fk_fdc_variable FOREIGN KEY (variable_key) REFERENCES dw.dim_variable (variable_key) NOT ENFORCED;
GO
