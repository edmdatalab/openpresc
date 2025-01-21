-- this sql calculates the ADQ values for benzodiazepines
SELECT
    rx.month AS month, 
    rx.practice AS practice, 
    rx.bnf_name AS bnf_name,
    rx.bnf_code AS bnf_code, 
    SUM(quantity) AS quantity,
    SUM(items) AS items, 
    strnt_nmrtr_val, -- strength numerator value from dmd
    strnt_nmrtr_uom, -- strength numerator unit of measurement from dmd
    strnt_dnmtr_val, -- strength denominator value from dmd
    strnt_dnmtr_uom, -- strength numerator unit of measurement from dmd
    adq.adq AS adq_value,
    SUM(quantity) * IEEE_DIVIDE( -- multiplies quantity by normalised adq value to get total usage value
        CASE 
            WHEN strnt_nmrtr_uom = 258685003 THEN strnt_nmrtr_val / 1000 -- divides where uom is in micrograms by 1000 to get milligram value for consistency 
            ELSE strnt_nmrtr_val 
        END, 
        COALESCE(strnt_dnmtr_val, 1) -- divides by unit size value, or 1 if this does not exist
    ) / adq.adq AS total_adq_usage -- gets adq usage value normalised to mg
FROM 
    hscic.normalised_prescribing AS rx
INNER JOIN 
    dmd.vmp AS vmp
    ON CONCAT(SUBSTR(rx.bnf_code, 0, 9), SUBSTR(rx.bnf_code, -2)) = CONCAT(SUBSTR(vmp.bnf_code, 0, 9), SUBSTR(vmp.bnf_code, -2)) --joins "generic" BNF code to dm+d
INNER JOIN 
    dmd.vpi AS vpi
    ON vpi.vmp = vmp.id -- joins from vmp to vpi (where strengths are stored)
INNER JOIN 
    measures.adq_bdz AS adq --joins to bdz ADQ table
    ON SUBSTR(rx.bnf_code, 0, 9) = adq.chemical_code
INNER JOIN
    dmd.droute AS droute -- joins to droute to get route codes
    ON vmp.id = droute.vmp
INNER JOIN
    dmd.route AS route -- joins to route to get route names
    ON route.cd = droute.route
WHERE 
    route.descr = 'Oral' AND -- Oral preps only
    rx.bnf_code LIKE '0401%' -- Hypnotics and Anxiolytics
GROUP BY 
    month, 
    rx.bnf_name, 
    rx.bnf_code, 
    rx.practice,
    strnt_nmrtr_val, 
    strnt_nmrtr_uom, 
    strnt_dnmtr_val, 
    strnt_dnmtr_uom,
    adq.adq
