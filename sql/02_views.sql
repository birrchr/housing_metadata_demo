-- =====================================================================
-- Views: the generic joins everyone should use instead of
-- writing province-specific logic.
-- =====================================================================

-- Data dictionary for any year: variables + their codes, valid that year
CREATE OR REPLACE VIEW meta.v_data_dictionary AS
SELECT
    y.reference_year,
    v.variable_id,
    v.variable_name,
    v.label_en          AS variable_label,
    v.role,
    v.data_type,
    v.unit,
    v.codeset_id,
    c.code,
    c.label_en          AS code_label,
    c.sort_order
FROM (SELECT DISTINCT reference_year FROM meta.dataset) y
JOIN meta.variable v
  ON y.reference_year BETWEEN v.valid_from_year AND coalesce(v.valid_to_year, 9999)
LEFT JOIN meta.code c
  ON c.codeset_id = v.codeset_id
 AND y.reference_year BETWEEN c.valid_from_year AND coalesce(c.valid_to_year, 9999);

-- Every value with its variable description and code label: the "seamless join"
CREATE OR REPLACE VIEW meta.v_record_labelled AS
SELECT
    d.reference_year,
    d.source_system,
    r.dataset_id,
    r.record_id,
    v.variable_id,
    v.variable_name,
    v.label_en          AS variable_label,
    v.data_type,
    v.unit,
    r.source_value,
    r.value,
    CASE WHEN v.data_type IN ('INTEGER', 'DECIMAL')
         THEN try_cast(r.value AS DOUBLE) END   AS value_num,
    c.label_en          AS value_label,
    r.mapped_via
FROM meta.record_value r
JOIN meta.dataset  d USING (dataset_id)
JOIN meta.variable v USING (variable_id)
LEFT JOIN meta.code c
  ON c.codeset_id = v.codeset_id
 AND c.code = r.value
 AND d.reference_year BETWEEN c.valid_from_year AND coalesce(c.valid_to_year, 9999);

-- ---------- QA views (each should return zero rows in a clean build) ----

-- Columns delivered in a file that nobody has mapped
CREATE OR REPLACE VIEW meta.v_qa_unmapped_columns AS
SELECT d.source_system, d.reference_year, dc.dataset_id, dc.source_column, dc.source_type
FROM meta.dataset_column dc
JOIN meta.dataset d USING (dataset_id)
WHERE dc.variable_id IS NULL;

-- Variables expected that year but missing from a province file
CREATE OR REPLACE VIEW meta.v_qa_missing_variables AS
SELECT d.dataset_id, d.source_system, d.reference_year, v.variable_id
FROM meta.dataset d
JOIN meta.variable v
  ON d.reference_year BETWEEN v.valid_from_year AND coalesce(v.valid_to_year, 9999)
WHERE NOT EXISTS (
    SELECT 1 FROM meta.dataset_column dc
    WHERE dc.dataset_id = d.dataset_id AND dc.variable_id = v.variable_id
);

-- Source values with no entry in source_code_map (province sent a new code)
CREATE OR REPLACE VIEW meta.v_qa_unmapped_values AS
SELECT d.reference_year, d.source_system, r.dataset_id, r.variable_id,
       r.source_value, count(*) AS n_records
FROM meta.record_value r
JOIN meta.dataset d USING (dataset_id)
WHERE r.mapped_via = 'code_map'
  AND r.source_value IS NOT NULL
  AND r.value IS NULL
GROUP BY ALL;

-- Coded values that do not exist in the codeset for that year
CREATE OR REPLACE VIEW meta.v_qa_invalid_codes AS
SELECT
    reference_year, source_system, dataset_id, variable_id,
    source_value, value, mapped_via, count(*) AS n_records
FROM meta.v_record_labelled l
WHERE value IS NOT NULL
  AND value_label IS NULL
  AND variable_id IN (SELECT variable_id FROM meta.variable WHERE codeset_id IS NOT NULL)
GROUP BY ALL;

-- Numeric variables whose values do not cast
CREATE OR REPLACE VIEW meta.v_qa_bad_numerics AS
SELECT reference_year, dataset_id, variable_id, source_value, count(*) AS n_records
FROM meta.v_record_labelled
WHERE data_type IN ('INTEGER', 'DECIMAL')
  AND value IS NOT NULL AND value_num IS NULL
GROUP BY ALL;

-- One-stop summary
CREATE OR REPLACE VIEW meta.v_qa_summary AS
SELECT 'unmapped_columns'  AS check_name, count(*) AS n_issues FROM meta.v_qa_unmapped_columns
UNION ALL SELECT 'missing_variables', count(*) FROM meta.v_qa_missing_variables
UNION ALL SELECT 'unmapped_values',   count(*) FROM meta.v_qa_unmapped_values
UNION ALL SELECT 'invalid_codes',     count(*) FROM meta.v_qa_invalid_codes
UNION ALL SELECT 'bad_numerics',      count(*) FROM meta.v_qa_bad_numerics;
