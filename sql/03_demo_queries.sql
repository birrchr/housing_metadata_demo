-- Run with:  duckdb housing_meta.duckdb < sql/03_demo_queries.sql
-- (or open in the DuckDB UI / any SQL client)

-- 1. Data dictionary for 2025: one query, any province
SELECT variable_id, variable_label, data_type, unit, code, code_label
FROM meta.v_data_dictionary
WHERE reference_year = 2025
ORDER BY variable_id, sort_order;

-- 2. Lineage: what each province called each variable, by year
SELECT dc.variable_id, d.reference_year,
       string_agg(d.source_system || ':' || dc.source_column, '  ' ORDER BY d.source_system) AS source_columns
FROM meta.dataset_column dc JOIN meta.dataset d USING (dataset_id)
WHERE dc.variable_id IS NOT NULL
GROUP BY ALL ORDER BY 1, 2;

-- 3. Labelled cross-province tabulation with NO province-specific logic
SELECT reference_year, source_system, value_label AS tenure, count(*) AS households
FROM meta.v_record_labelled
WHERE variable_id = 'TENURE'
GROUP BY ALL ORDER BY 1, 2, 3;

-- 4. Same thing from the wide table, joined to code labels
SELECT h.reference_year, h.source_system, c.label_en AS dwelling_type,
       count(*) AS households, round(avg(h.monthly_shelter_cost), 0) AS avg_cost
FROM meta.household_harmonised h
JOIN meta.variable v ON v.variable_id = 'DWELL_TYPE'
LEFT JOIN meta.code c ON c.codeset_id = v.codeset_id AND c.code = h.dwelling_type
GROUP BY ALL ORDER BY 1, 2, 3;

-- 5. One record, fully described (source value -> canonical value -> label)
SELECT variable_label, source_value, value, value_label, unit
FROM meta.v_record_labelled
WHERE dataset_id = 'ON_2025' AND record_id = 'ON202500000';

-- 6. QA
SELECT * FROM meta.v_qa_summary;
SELECT * FROM meta.v_qa_unmapped_columns;
SELECT * FROM meta.v_qa_unmapped_values;
