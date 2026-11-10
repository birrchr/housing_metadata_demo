-- =====================================================================
-- audit.usp_load_run: load one staged province-year into the warehouse
--
-- Called by the pipeline's "Load warehouse" activity after the notebook has
-- staged the run in the Lakehouse (lh_housing) and refreshed its SQL endpoint.
--
--   @mode = 'validate'  record QA only; dw is not touched
--   @mode = 'publish'   record QA; if no blocking issues, version the metadata,
--                       upsert dimensions and replace this province-year's facts
--
-- Blocking QA issues make the procedure THROW in either mode, so the pipeline
-- run shows as Failed and the caller sees why. Details: qa.check_detail.
--
-- The staging Lakehouse is referenced as lh_housing.dbo.*. If your Lakehouse
-- has another name, regenerate with: housing-fabric sql --lakehouse <name>
-- =====================================================================
CREATE OR ALTER PROCEDURE audit.usp_load_run
    @run_id           VARCHAR(64),
    @source_system    VARCHAR(16),
    @reference_year   INT,
    @metadata_release VARCHAR(64),
    @mode             VARCHAR(16) = 'validate'
AS
BEGIN
    SET NOCOUNT ON;

    DECLARE @now        DATETIME2(6) = SYSUTCDATETIME();
    DECLARE @prov       VARCHAR(16)  = UPPER(@source_system);
    DECLARE @dataset_id VARCHAR(64)  = CONCAT(UPPER(@source_system), '_', @reference_year);
    DECLARE @blocking   INT;
    DECLARE @warnings   INT;
    DECLARE @rows       BIGINT;
    DECLARE @msg        VARCHAR(4000);

    IF @mode NOT IN ('validate', 'publish')
        THROW 50000, 'mode must be ''validate'' or ''publish''.', 1;

    -- 0. The staged run must be visible through the Lakehouse SQL endpoint
    IF NOT EXISTS (SELECT 1 FROM lh_housing.dbo.stg_dataset
                   WHERE run_id = @run_id AND dataset_id = @dataset_id)
    BEGIN
        SET @msg = CONCAT('No staged dataset ', @dataset_id, ' for run ', @run_id,
                          '. Check the notebook output and the SQL endpoint refresh.');
        THROW 50001, @msg, 1;
    END;

    DELETE FROM audit.load_run WHERE run_id = @run_id;   -- a rerun of the same pipeline run
    INSERT INTO audit.load_run
        (run_id, source_system, reference_year, metadata_release, mode, status, started_at)
    VALUES (@run_id, @prov, @reference_year, @metadata_release, @mode, 'running', @now);

    -- 1. QA: keep every run's results -----------------------------------
    DELETE FROM qa.check_summary WHERE run_id = @run_id;
    INSERT INTO qa.check_summary
        (run_id, source_system, reference_year, check_name, n_issues, is_blocking, checked_at)
    SELECT run_id, @prov, @reference_year, check_name, n_issues, is_blocking, @now
    FROM lh_housing.dbo.stg_qa_summary
    WHERE run_id = @run_id;

    DELETE FROM qa.check_detail WHERE run_id = @run_id;
    INSERT INTO qa.check_detail (run_id, check_name, dataset_id, detail)
    SELECT run_id, check_name, dataset_id, LEFT(detail, 4000)
    FROM lh_housing.dbo.stg_qa_detail
    WHERE run_id = @run_id;

    SELECT @blocking = COALESCE(SUM(CASE WHEN is_blocking = 1 THEN n_issues END), 0),
           @warnings = COALESCE(SUM(CASE WHEN is_blocking = 0 THEN n_issues END), 0)
    FROM qa.check_summary
    WHERE run_id = @run_id;

    IF @blocking > 0
    BEGIN
        UPDATE audit.load_run
        SET status = 'failed_qa', finished_at = SYSUTCDATETIME(),
            blocking_issues = @blocking, warning_issues = @warnings,
            message = 'Blocking QA issues; see qa.check_detail'
        WHERE run_id = @run_id;
        SET @msg = CONCAT(@blocking, ' blocking QA issue(s) in ', @dataset_id,
                          '. See qa.check_detail WHERE run_id = ''', @run_id, '''.');
        THROW 50002, @msg, 1;
    END;

    IF @mode = 'validate'
    BEGIN
        UPDATE audit.load_run
        SET status = 'validated', finished_at = SYSUTCDATETIME(),
            blocking_issues = 0, warning_issues = @warnings
        WHERE run_id = @run_id;
        RETURN;
    END;

    DROP TABLE IF EXISTS #changed;
    DROP TABLE IF EXISTS #closed;

    BEGIN TRY
        -- 2. Metadata version history (SCD2) ------------------------------
        -- Rows in this release that are new or changed
        CREATE TABLE #changed (
            table_name VARCHAR(64), row_key VARCHAR(400), row_sig VARCHAR(4000),
            source_system VARCHAR(16), valid_from_year INT, valid_to_year INT);
        INSERT INTO #changed
        SELECT s.table_name, s.row_key, s.row_sig, s.source_system, s.valid_from_year, s.valid_to_year
        FROM lh_housing.dbo.stg_metadata AS s
        WHERE s.run_id = @run_id
          AND NOT EXISTS (SELECT 1 FROM meta.metadata_history AS h
                          WHERE h.is_current = 1 AND h.table_name = s.table_name
                            AND h.row_key = s.row_key AND h.row_sig = s.row_sig);

        -- Current rows that this release changed or removed
        CREATE TABLE #closed (
            table_name VARCHAR(64), row_key VARCHAR(400), row_sig VARCHAR(4000),
            source_system VARCHAR(16), valid_from_year INT, valid_to_year INT);
        INSERT INTO #closed
        SELECT h.table_name, h.row_key, h.row_sig, h.source_system, h.valid_from_year, h.valid_to_year
        FROM meta.metadata_history AS h
        WHERE h.is_current = 1
          AND NOT EXISTS (SELECT 1 FROM lh_housing.dbo.stg_metadata AS s
                          WHERE s.run_id = @run_id AND s.table_name = h.table_name
                            AND s.row_key = h.row_key AND s.row_sig = h.row_sig);

        -- Already-published province-years that a changed metadata row applies to.
        -- Their stored values were recoded with the old metadata: reload them.
        INSERT INTO qa.check_detail (run_id, check_name, dataset_id, detail)
        SELECT DISTINCT @run_id, 'reload_recommended', d.dataset_id,
               CONCAT('{"table":"', m.table_name, '","row_key":"', m.row_key,
                      '","published_release":"', d.metadata_release, '"}')
        FROM dw.dim_dataset AS d
        JOIN (SELECT table_name, row_key, source_system, valid_from_year, valid_to_year FROM #changed
              UNION ALL
              SELECT table_name, row_key, source_system, valid_from_year, valid_to_year FROM #closed) AS m
          ON (m.source_system IS NULL OR m.source_system = d.source_system)
         AND d.reference_year BETWEEN COALESCE(m.valid_from_year, 0) AND COALESCE(m.valid_to_year, 9999)
        WHERE d.dataset_id <> @dataset_id;

        INSERT INTO qa.check_summary
            (run_id, source_system, reference_year, check_name, n_issues, is_blocking, checked_at)
        SELECT @run_id, @prov, @reference_year, 'reload_recommended',
               COUNT(DISTINCT dataset_id), 0, @now
        FROM qa.check_detail
        WHERE run_id = @run_id AND check_name = 'reload_recommended';

        BEGIN TRANSACTION;

        UPDATE h
        SET is_current = 0, effective_to = @now
        FROM meta.metadata_history AS h
        JOIN #closed AS c
          ON c.table_name = h.table_name AND c.row_key = h.row_key AND c.row_sig = h.row_sig
        WHERE h.is_current = 1;

        INSERT INTO meta.metadata_history
            (table_name, row_key, row_sig, source_system, valid_from_year, valid_to_year,
             metadata_release, effective_from, effective_to, is_current, run_id)
        SELECT table_name, row_key, row_sig, source_system, valid_from_year, valid_to_year,
               @metadata_release, @now, NULL, 1, @run_id
        FROM #changed;

        -- 3. Dimensions (type 1 upserts) ----------------------------------
        UPDATE v
        SET variable_name = s.variable_name, label_en = s.label_en, role = s.role,
            data_type = s.data_type, codeset_id = s.codeset_id, unit = s.unit,
            valid_from_year = s.valid_from_year, valid_to_year = s.valid_to_year,
            metadata_release = @metadata_release, updated_at = @now
        FROM dw.dim_variable AS v
        JOIN lh_housing.dbo.stg_variable AS s ON s.variable_id = v.variable_id
        WHERE s.run_id = @run_id;

        INSERT INTO dw.dim_variable
            (variable_id, variable_name, label_en, role, data_type, codeset_id, unit,
             valid_from_year, valid_to_year, metadata_release, updated_at)
        SELECT s.variable_id, s.variable_name, s.label_en, s.role, s.data_type, s.codeset_id, s.unit,
               s.valid_from_year, s.valid_to_year, @metadata_release, @now
        FROM lh_housing.dbo.stg_variable AS s
        WHERE s.run_id = @run_id
          AND NOT EXISTS (SELECT 1 FROM dw.dim_variable AS v WHERE v.variable_id = s.variable_id);

        UPDATE c
        SET codeset_name = cs.codeset_name, label_en = s.label_en, sort_order = s.sort_order,
            valid_to_year = s.valid_to_year, metadata_release = @metadata_release, updated_at = @now
        FROM dw.dim_code AS c
        JOIN lh_housing.dbo.stg_code AS s
          ON s.codeset_id = c.codeset_id AND s.code = c.code AND s.valid_from_year = c.valid_from_year
        LEFT JOIN lh_housing.dbo.stg_codeset AS cs
          ON cs.codeset_id = s.codeset_id AND cs.run_id = @run_id
        WHERE s.run_id = @run_id;

        INSERT INTO dw.dim_code
            (codeset_id, codeset_name, code, label_en, sort_order, valid_from_year, valid_to_year,
             metadata_release, updated_at)
        SELECT s.codeset_id, cs.codeset_name, s.code, s.label_en, s.sort_order,
               s.valid_from_year, s.valid_to_year, @metadata_release, @now
        FROM lh_housing.dbo.stg_code AS s
        LEFT JOIN lh_housing.dbo.stg_codeset AS cs
          ON cs.codeset_id = s.codeset_id AND cs.run_id = @run_id
        WHERE s.run_id = @run_id
          AND NOT EXISTS (SELECT 1 FROM dw.dim_code AS c
                          WHERE c.codeset_id = s.codeset_id AND c.code = s.code
                            AND c.valid_from_year = s.valid_from_year);

        IF NOT EXISTS (SELECT 1 FROM dw.dim_source_system WHERE source_system = @prov)
            INSERT INTO dw.dim_source_system (source_system, first_year, last_year)
            VALUES (@prov, @reference_year, @reference_year);
        ELSE
            UPDATE dw.dim_source_system
            SET first_year = CASE WHEN @reference_year < first_year THEN @reference_year ELSE first_year END,
                last_year  = CASE WHEN @reference_year > last_year  THEN @reference_year ELSE last_year  END
            WHERE source_system = @prov;

        IF EXISTS (SELECT 1 FROM dw.dim_dataset WHERE dataset_id = @dataset_id)
            UPDATE d
            SET source_path = s.source_path, record_count = s.record_count, run_id = @run_id,
                metadata_release = @metadata_release, loaded_at = @now
            FROM dw.dim_dataset AS d
            JOIN lh_housing.dbo.stg_dataset AS s ON s.dataset_id = d.dataset_id
            WHERE s.run_id = @run_id AND d.dataset_id = @dataset_id;
        ELSE
            INSERT INTO dw.dim_dataset
                (dataset_id, source_system, reference_year, source_path, record_count,
                 run_id, metadata_release, loaded_at)
            SELECT dataset_id, source_system, reference_year, source_path, record_count,
                   @run_id, @metadata_release, @now
            FROM lh_housing.dbo.stg_dataset
            WHERE run_id = @run_id AND dataset_id = @dataset_id;

        -- 4. Facts: replace this province-year ----------------------------
        DECLARE @dataset_key BIGINT;
        SELECT @dataset_key = dataset_key FROM dw.dim_dataset WHERE dataset_id = @dataset_id;

        DELETE FROM dw.fact_record_value   WHERE dataset_key = @dataset_key;
        DELETE FROM dw.fact_dataset_column WHERE dataset_key = @dataset_key;

        INSERT INTO dw.fact_record_value
            (dataset_key, record_id, variable_key, code_key, source_value, value, value_num, mapped_via, run_id)
        SELECT @dataset_key, r.record_id, v.variable_key, c.code_key, r.source_value, r.value,
               CASE WHEN v.data_type IN ('INTEGER', 'DECIMAL') THEN TRY_CAST(r.value AS FLOAT) END,
               r.mapped_via, @run_id
        FROM lh_housing.dbo.stg_record_value AS r
        JOIN dw.dim_variable AS v ON v.variable_id = r.variable_id
        LEFT JOIN dw.dim_code AS c
          ON c.codeset_id = v.codeset_id AND c.code = r.value
         AND @reference_year BETWEEN c.valid_from_year AND COALESCE(c.valid_to_year, 9999)
        WHERE r.run_id = @run_id AND r.dataset_id = @dataset_id;
        SET @rows = @@ROWCOUNT;

        INSERT INTO dw.fact_dataset_column (dataset_key, source_column, source_type, variable_key, run_id)
        SELECT @dataset_key, s.source_column, s.source_type, v.variable_key, @run_id
        FROM lh_housing.dbo.stg_dataset_column AS s
        LEFT JOIN dw.dim_variable AS v ON v.variable_id = s.variable_id
        WHERE s.run_id = @run_id AND s.dataset_id = @dataset_id;

        COMMIT TRANSACTION;
    END TRY
    BEGIN CATCH
        IF @@TRANCOUNT > 0 ROLLBACK TRANSACTION;
        SET @msg = LEFT(ERROR_MESSAGE(), 4000);
        UPDATE audit.load_run
        SET status = 'failed', finished_at = SYSUTCDATETIME(), message = @msg
        WHERE run_id = @run_id;
        THROW;
    END CATCH;

    -- 5. Wide table, regenerated from dim_variable -----------------------
    -- One typed column per attribute variable; a new variable becomes a new column.
    DECLARE @cols VARCHAR(MAX);
    SELECT @cols = STRING_AGG(CAST(CONCAT(
               'TRY_CAST(MAX(CASE WHEN f.variable_key = ', variable_key, ' THEN f.value END) AS ',
               CASE data_type WHEN 'INTEGER' THEN 'INT'
                              WHEN 'DECIMAL' THEN 'DECIMAL(18,4)'
                              WHEN 'DATE'    THEN 'DATE'
                              ELSE 'VARCHAR(400)' END,
               ') AS ', QUOTENAME(variable_name)) AS VARCHAR(MAX)), ', ')
           WITHIN GROUP (ORDER BY variable_id)
    FROM dw.dim_variable
    WHERE role = 'attribute';

    DECLARE @sql VARCHAR(MAX) = CONCAT(
        'CREATE TABLE dw.fact_household AS ',
        'SELECT d.dataset_key, d.source_system, d.reference_year, f.record_id, ', @cols, ' ',
        'FROM dw.fact_record_value AS f JOIN dw.dim_dataset AS d ON d.dataset_key = f.dataset_key ',
        'GROUP BY d.dataset_key, d.source_system, d.reference_year, f.record_id;');

    DROP TABLE IF EXISTS dw.fact_household;
    EXEC (@sql);

    -- 6. Close the run ----------------------------------------------------
    SELECT @warnings = COALESCE(SUM(n_issues), 0)
    FROM qa.check_summary
    WHERE run_id = @run_id AND is_blocking = 0;

    UPDATE audit.load_run
    SET status = 'published', finished_at = SYSUTCDATETIME(),
        blocking_issues = 0, warning_issues = @warnings, rows_loaded = @rows
    WHERE run_id = @run_id;
END;
GO
