-- =====================================================================
-- Analyst and operations views (run after 01_tables.sql)
-- These mirror the DuckDB views, so the walkthrough notebook's queries
-- port with little change.
-- =====================================================================

-- Every value with its variable label, unit and code label
CREATE OR ALTER VIEW dw.v_record_labelled AS
SELECT
    d.reference_year,
    d.source_system,
    d.dataset_id,
    f.record_id,
    v.variable_id,
    v.variable_name,
    v.label_en      AS variable_label,
    v.data_type,
    v.unit,
    f.source_value,
    f.value,
    f.value_num,
    c.label_en      AS value_label,
    f.mapped_via
FROM dw.fact_record_value AS f
JOIN dw.dim_dataset  AS d ON d.dataset_key  = f.dataset_key
JOIN dw.dim_variable AS v ON v.variable_key = f.variable_key
LEFT JOIN dw.dim_code AS c ON c.code_key    = f.code_key;
GO

-- Variables and codes valid in each published reference year
CREATE OR ALTER VIEW dw.v_data_dictionary AS
SELECT
    y.reference_year,
    v.variable_id,
    v.variable_name,
    v.label_en      AS variable_label,
    v.role,
    v.data_type,
    v.unit,
    v.codeset_id,
    c.code,
    c.label_en      AS code_label,
    c.sort_order
FROM (SELECT DISTINCT reference_year FROM dw.dim_dataset) AS y
JOIN dw.dim_variable AS v
  ON y.reference_year BETWEEN v.valid_from_year AND COALESCE(v.valid_to_year, 9999)
LEFT JOIN dw.dim_code AS c
  ON c.codeset_id = v.codeset_id
 AND y.reference_year BETWEEN c.valid_from_year AND COALESCE(c.valid_to_year, 9999);
GO

-- Lineage: which source column fed each variable, by province and year
CREATE OR ALTER VIEW dw.v_lineage AS
SELECT
    d.source_system,
    d.reference_year,
    d.dataset_id,
    l.source_column,
    l.source_type,
    COALESCE(v.variable_id, '(unmapped)') AS variable_id
FROM dw.fact_dataset_column AS l
JOIN dw.dim_dataset AS d ON d.dataset_key = l.dataset_key
LEFT JOIN dw.dim_variable AS v ON v.variable_key = l.variable_key;
GO

-- Latest run per province-year, with its QA outcome
CREATE OR ALTER VIEW audit.v_latest_runs AS
SELECT run_id, source_system, reference_year, metadata_release, mode, status,
       started_at, finished_at, blocking_issues, warning_issues, rows_loaded, message
FROM (
    SELECT r.*,
           ROW_NUMBER() OVER (PARTITION BY source_system, reference_year
                              ORDER BY started_at DESC) AS rn
    FROM audit.load_run AS r
) AS x
WHERE rn = 1;
GO

-- Metadata as it was at any point in time:
--   SELECT * FROM meta.metadata_history
--   WHERE '2026-06-30' >= effective_from AND ('2026-06-30' < effective_to OR effective_to IS NULL)
CREATE OR ALTER VIEW meta.v_metadata_current AS
SELECT table_name, row_key, row_sig, source_system, valid_from_year, valid_to_year,
       metadata_release, effective_from, run_id
FROM meta.metadata_history
WHERE is_current = 1;
GO
