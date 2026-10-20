"""Fixed health projection reused by the independent audit query."""

BURDEN_COLUMNS = """b.geo_id, b.total_pop, b.latitude, b.longitude,
 b.pct_uninsured, b.pct_diabetes, b.pct_asthma, b.pct_copd, b.pct_high_bp,
 b.pct_checkup, b.pct_no_vehicle, b.pct_mobility_difficulty,
 SAFE_CAST(JSON_VALUE(TO_JSON_STRING(b), '$.pct_lack_transport') AS FLOAT64) AS pct_lack_transport,
 NULLIF(b.svi_socioeconomic, -999) AS svi_socioeconomic"""

AUDIT_HEALTH_SQL = """SELECT {columns}, a.km_to_any_care, a.km_to_safety_net,
 a.extra_km_if_uninsured FROM `{project}.{health}.burden_tracts` b
 LEFT JOIN `{project}.{health}.tract_access` a USING (geo_id)
 WHERE STARTS_WITH(b.geo_id, @county_fips)"""
AUDIT_COUNTS_SQL = """SELECT geo_id, total_pop, white_pop, black_pop, asian_pop,
 native_pop, pacific_pop, other_pop, multiracial_pop, hispanic_pop,
 limited_english_households, total_households
 FROM `{project}.{audit}.tract_demographics`
 WHERE STARTS_WITH(geo_id, @county_fips)"""
