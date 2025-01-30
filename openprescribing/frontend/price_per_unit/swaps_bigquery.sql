WITH drugs AS (
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
                COALESCE(COALESCE(CAST (bs_subid AS STRING), (CAST(ing AS STRING))), 'NULL'), '|', -- ingredient code
                COALESCE(CAST(strnt_nmrtr_val AS STRING), 'NULL'), '|', -- strength numerator value
                COALESCE(CAST(strnt_nmrtr_uom AS STRING), 'NULL'), '|',  -- strength numerator unit
                COALESCE(CAST(strnt_dnmtr_val AS STRING), 'NULL'), '|',  -- strength denominator value
                COALESCE(CAST(strnt_dnmtr_uom AS STRING), 'NULL'), '|', -- strength denominator unit
                COALESCE(CAST(droute.route AS STRING), 'NULL'), '|',-- route
                COALESCE(CAST(pres_stat AS STRING), 'NULL'), '|',-- prescribing suitable for primary care value
                COALESCE(CASE WHEN formroute.descr NOT LIKE '%.oral%' THEN CAST(udfs AS STRING) ELSE 'NULL' END, 'NULL'), '|', -- if not oral meds, then ensure unit doses are the same (e.g. injections)
                CASE WHEN LOWER(formroute.descr) LIKE '%modified-release%' THEN 'MR' ELSE 'NULL' END, '|', -- add 'modified release' flag on match string, so that non modified-release preps aren't matched with standard release
                CASE WHEN LOWER(route.descr) LIKE '%cutaneous%' THEN LEFT(formroute.descr, STRPOS(formroute.descr, '.') - 1)  ELSE 'NULL' END -- add type of formulation to cutaneous prepas
            ),
            ','
            ORDER BY ing, basis_strnt
        ) AS vpi_string
    FROM
        dmd.vpi AS vpi
    INNER JOIN
        dmd.vmp AS vmp ON vpi.vmp = vmp.id
    INNER JOIN
        dmd.droute AS droute ON vmp.id = droute.vmp
    INNER JOIN
        dmd.route AS route ON route.cd = droute.route
    INNER JOIN
        dmd.ont AS ont ON vmp.id = ONT.vmp
    INNER JOIN
        dmd.ontformroute AS formroute ON ont.form = formroute.cd
    INNER JOIN
        dmd.dform AS dform ON vmp.id = dform.vmp
    INNER JOIN
        dmd.form AS form ON dform.form = form.cd
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
    dmd.vmp AS vmp
INNER JOIN
    dmd.amp AS amp
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
