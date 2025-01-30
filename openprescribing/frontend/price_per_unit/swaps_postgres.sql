-- Rewritten from BigQuery-flavoured SQL using:
--   ./manage.py translate_bq_sql -i frontend/price_per_unit/swaps_bigquery.sql -o frontend/price_per_unit/swaps_postgres.sql

WITH
bq_dmd_vmp AS NOT MATERIALIZED (
  SELECT
    vpid AS id,
    vpiddt AS vpiddt,
    vpidprev AS vpidprev,
    vtmid AS vtm,
    invalid AS invalid,
    nm AS nm,
    abbrevnm AS abbrevnm,
    basiscd AS basis,
    nmdt AS nmdt,
    nmprev AS nmprev,
    basis_prevcd AS basis_prev,
    nmchangecd AS nmchange,
    combprodcd AS combprod,
    pres_statcd AS pres_stat,
    sug_f AS sug_f,
    glu_f AS glu_f,
    pres_f AS pres_f,
    cfc_f AS cfc_f,
    non_availcd AS non_avail,
    non_availdt AS non_availdt,
    df_indcd AS df_ind,
    udfs AS udfs,
    udfs_uomcd AS udfs_uom,
    unit_dose_uomcd AS unit_dose_uom,
    bnf_code AS bnf_code
  FROM dmd_vmp
),
bq_dmd_vpi AS NOT MATERIALIZED (
  SELECT
    id AS id,
    vpid AS vmp,
    isid AS ing,
    basis_strntcd AS basis_strnt,
    bs_subid AS bs_subid,
    strnt_nmrtr_val AS strnt_nmrtr_val,
    strnt_nmrtr_uomcd AS strnt_nmrtr_uom,
    strnt_dnmtr_val AS strnt_dnmtr_val,
    strnt_dnmtr_uomcd AS strnt_dnmtr_uom
  FROM dmd_vpi
),
bq_dmd_ont AS NOT MATERIALIZED (
  SELECT
    id AS id,
    vpid AS vmp,
    formcd AS form
  FROM dmd_ont
),
bq_dmd_dform AS NOT MATERIALIZED (
  SELECT
    id AS id,
    vpid AS vmp,
    formcd AS form
  FROM dmd_dform
),
bq_dmd_droute AS NOT MATERIALIZED (
  SELECT
    id AS id,
    vpid AS vmp,
    routecd AS route
  FROM dmd_droute
),
bq_dmd_amp AS NOT MATERIALIZED (
  SELECT
    apid AS id,
    invalid AS invalid,
    vpid AS vmp,
    nm AS nm,
    abbrevnm AS abbrevnm,
    descr AS descr,
    nmdt AS nmdt,
    nm_prev AS nm_prev,
    suppcd AS supp,
    lic_authcd AS lic_auth,
    lic_auth_prevcd AS lic_auth_prev,
    lic_authchangecd AS lic_authchange,
    lic_authchangedt AS lic_authchangedt,
    combprodcd AS combprod,
    flavourcd AS flavour,
    ema AS ema,
    parallel_import AS parallel_import,
    avail_restrictcd AS avail_restrict,
    bnf_code AS bnf_code
  FROM dmd_amp
),
bq_dmd_form AS NOT MATERIALIZED (
  SELECT
    cd AS cd,
    cddt AS cddt,
    cdprev AS cdprev,
    descr AS descr
  FROM dmd_form
),
bq_dmd_ontformroute AS NOT MATERIALIZED (
  SELECT
    cd AS cd,
    descr AS descr
  FROM dmd_ontformroute
),
bq_dmd_route AS NOT MATERIALIZED (
  SELECT
    cd AS cd,
    cddt AS cddt,
    cdprev AS cdprev,
    descr AS descr
  FROM dmd_route
)

, drugs AS (
    SELECT
        vmp.id,
        nm,
        bnf_code,
        pres_stat,
        droute.route,
        pres_f,
        sug_f,
        CASE WHEN formroute.descr LIKE 'solutioninfusion%' then 'solutioninfusion' -- simplify multiple infusion routes as 'infusion'
        WHEN formroute.descr LIKE 'solutioninjection%' then 'solutioninjection' -- simplify multiple injection routes as 'injection'
        ELSE formroute.descr END AS formroute,
        udfs,
        form.descr AS form,
        STRING_AGG(
            CONCAT(
                COALESCE(COALESCE(CAST (bs_subid AS TEXT), (CAST(ing AS TEXT))), 'NULL'), '|', -- ingredient code
                COALESCE(CAST(strnt_nmrtr_val AS TEXT), 'NULL'), '|', -- strength numerator value
                COALESCE(CAST(strnt_nmrtr_uom AS TEXT), 'NULL'), '|',  -- strength numerator unit
                COALESCE(CAST(strnt_dnmtr_val AS TEXT), 'NULL'), '|',  -- strength denominator value
                COALESCE(CAST(strnt_dnmtr_uom AS TEXT), 'NULL'), '|', -- strength denominator unit
                COALESCE(CAST(droute.route AS TEXT), 'NULL'), '|',-- route
                COALESCE(CAST(pres_stat AS TEXT), 'NULL'), '|',-- prescribing suitable for primary care value
                COALESCE(CASE WHEN formroute.descr NOT LIKE '%.oral%' THEN CAST(udfs AS TEXT) ELSE 'NULL' END, 'NULL'), '|', -- if not oral meds, then ensure unit doses are the same (e.g. injections)
                CASE WHEN LOWER(formroute.descr) LIKE '%modified-release%' THEN 'MR' ELSE 'NULL' END, '|', -- add 'modified release' flag on match string, so that non modified-release preps aren't matched with standard release
                CASE WHEN LOWER(route.descr) LIKE '%cutaneous%' THEN LEFT(formroute.descr, STRPOS(formroute.descr, '.') - 1)  ELSE 'NULL' END -- add type of formulation to cutaneous prepas
            ),
            ','
            ORDER BY ing, basis_strnt
        ) AS vpi_string
    FROM
        bq_dmd_vpi AS vpi
    INNER JOIN
        bq_dmd_vmp AS vmp ON vpi.vmp = vmp.id
    INNER JOIN
        bq_dmd_droute AS droute ON vmp.id = droute.vmp
    INNER JOIN
        bq_dmd_route AS route ON route.cd = droute.route
    INNER JOIN
        bq_dmd_ont AS ont ON vmp.id = ONT.vmp
    INNER JOIN
        bq_dmd_ontformroute AS formroute ON ont.form = formroute.cd
    INNER JOIN
        bq_dmd_dform AS dform ON vmp.id = dform.vmp
    INNER JOIN
        bq_dmd_form AS form ON dform.form = form.cd
    WHERE bnf_code IS NOT NULL --make sure all drugs have BNF code
    AND SUBSTR(bnf_code,10,2) = 'AA' --make sure all generic codes
    AND (non_avail != 1 OR non_avail is NULL) -- make sure all drugs are available
    AND strnt_nmrtr_val IS NOT NULL -- make sure all drugs have a strength
    AND pres_stat = 1 -- must be "suitable for prescribing in primary care", e.g. no cautions on switching preparations
    GROUP BY
        vmp.id, nm, bnf_code, pres_stat, pres_f, sug_f, route, formroute, udfs, form.descr
),
vmp_amp AS (SELECT
    vmp.nm AS generic_bnf_name,
    vmp.bnf_code AS generic_bnf_code,
    amp.nm AS brand_bnf_name,
    amp.bnf_code AS brand_bnf_code
FROM
    bq_dmd_vmp AS vmp
INNER JOIN
    bq_dmd_amp AS amp
ON
    vmp.id = amp.vmp
WHERE
    vmp.bnf_code <> amp.bnf_code -- doesn't pick up AMPs with generic BNF codes, as these aren't in the prescribing data
-- AND CONCAT(SUBSTR(vmp.bnf_code,0,9), SUBSTR(vmp.bnf_code,-2, 2)) != CONCAT(SUBSTR(amp.bnf_code,0,9), SUBSTR(amp.bnf_code,-2, 2))
AND amp.avail_restrict =1 -- make sure the brand is available for dispensing
AND vmp.pres_stat = 1
AND (vmp.non_avail != 1 OR vmp.non_avail is NULL) -- make sure all drugs are available
),
pairs AS (
  SELECT t1.brand_bnf_code AS col1,t1.brand_bnf_name as Name,   t2.brand_bnf_code AS col2, t2.brand_bnf_name as Alternative_name
  FROM vmp_amp t1
  CROSS JOIN vmp_amp t2
  WHERE t1.generic_bnf_code = t2.generic_bnf_code AND t1.brand_bnf_code != t2.brand_bnf_code
),
original AS (
  SELECT generic_bnf_code AS col1, generic_bnf_name as Name,  brand_bnf_code AS col2, vmp_amp.brand_bnf_name AS Alternative_name FROM vmp_amp
  UNION ALL
  SELECT brand_bnf_code AS col1, brand_bnf_name as Name, generic_bnf_code AS col2, vmp_amp.generic_bnf_name AS Alternative_name FROM vmp_amp
)
SELECT DISTINCT

        drugs_1.bnf_code AS Code,
        drugs_1.nm AS Name,
        drugs_2.bnf_code AS Alternative_code,
        CASE WHEN drugs_1.pres_f IS TRUE THEN concat(drugs_1.form, ' preservative free')
             WHEN drugs_1.sug_f IS TRUE THEN concat(drugs_1.form, ' sugar free')
             ELSE drugs_1.form END AS Formulation,
        drugs_2.nm AS Alternative_name,
        CASE WHEN drugs_2.pres_f IS TRUE THEN concat(drugs_2.form, ' preservative free')
             WHEN drugs_2.sug_f IS TRUE THEN concat(drugs_2.form, ' sugar free') ELSE drugs_2.form END AS Alternative_formulation
    FROM
        drugs AS drugs_1
    INNER JOIN
        drugs AS drugs_2
        ON drugs_1.vpi_string = drugs_2.vpi_string -- only where strings are the same, i.e. same ingredients, strength, and route
    WHERE
        drugs_1.id != drugs_2.id -- so don't get duplication
    AND
        drugs_1.bnf_code != drugs_2.bnf_code -- remove duplication where two different VMP types have same BNF code (e.g. solution/suspension)

UNION ALL

SELECT col1 AS Code, Name, col2 AS Alternative_code, NULL as Formulation, Alternative_name, NULL as Alternative_formulation
FROM pairs
UNION ALL
SELECT col1 AS Code, Name, col2 AS Alternative_code, NULL as Formulation, Alternative_name, NULL as Alternative_formulation
FROM original

ORDER BY Code
