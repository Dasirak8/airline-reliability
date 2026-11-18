-- Compare early vs evening on the same carrier and directional route.
-- With thresholds in both windows, standardize rates with the same pooled
-- flight-volume weights. This is descriptive, not a causal adjustment.
WITH eligible AS (
    SELECT carrier, origin, destination,
           SUM(dep_hour BETWEEN 5 AND 10) AS early_n,
           SUM(dep_hour BETWEEN 16 AND 21) AS evening_n,
           SUM(dep_hour BETWEEN 5 AND 10 AND outcome <> 'on_time') AS early_bad,
           SUM(dep_hour BETWEEN 16 AND 21 AND outcome <> 'on_time') AS evening_bad
    FROM flights
    WHERE outcome <> 'unknown'
    GROUP BY carrier, origin, destination
    HAVING early_n >= 10 AND evening_n >= 10
), rates AS (
    SELECT *, early_n + evening_n AS weight,
           100.0 * early_bad / early_n AS early_pct,
           100.0 * evening_bad / evening_n AS evening_pct
    FROM eligible
)
SELECT COUNT(*) AS groups, SUM(early_n) AS early_n, SUM(evening_n) AS evening_n,
       SUM(weight * early_pct) / SUM(weight) AS early_matched_pct,
       SUM(weight * evening_pct) / SUM(weight) AS evening_matched_pct,
       SUM(weight * (evening_pct - early_pct)) / SUM(weight) AS matched_gap_pp
FROM rates;
