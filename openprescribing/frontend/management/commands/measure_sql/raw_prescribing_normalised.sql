SELECT
  ccgs.regional_team_id AS regional_team,
  ccgs.stp_id AS stp,
  practices.ccg_id AS pct,
  practices.pcn_id AS pcn,
  raw_prescribing.PRACTICE_CODE AS practice,
  COALESCE(bnf_map.current_bnf_code, raw_prescribing.BNF_CODE)
    AS bnf_code,
  raw_prescribing.BNF_DESCRIPTION AS bnf_name,
  raw_prescribing.ITEMS AS items,
  raw_prescribing.NIC AS net_cost,
  raw_prescribing.ACTUAL_COST AS actual_cost,
  raw_prescribing.QUANTITY AS quantity_per_item,
  raw_prescribing.TOTAL_QUANTITY AS total_quantity,
  PARSE_TIMESTAMP("%F", REGEXP_REPLACE(raw_prescribing._FILE_NAME, "^.+/(20\\d\\d)_(\\d\\d)/[^/]+$", "\\1-\\2-01")) AS month
FROM
  {project}.{hscic}.raw_prescribing_v2 AS raw_prescribing
LEFT JOIN
  {project}.{hscic}.bnf_map AS bnf_map
ON
  bnf_map.former_bnf_code = raw_prescribing.BNF_Code
INNER JOIN
  {project}.{hscic}.practices  AS practices
ON practices.code = raw_prescribing.Practice_Code
INNER JOIN
  {project}.{hscic}.ccgs AS ccgs
ON practices.ccg_id = ccgs.code
