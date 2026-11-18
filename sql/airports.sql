-- All networks/airlines combined, assigned to scheduled origin.
-- Restrict the priority list to origins with >=2,000 known flight outcomes.
WITH counts AS (
    SELECT origin, COUNT(*) AS known, SUM(outcome <> 'on_time') AS disrupted
    FROM flights
    WHERE outcome <> 'unknown'
    GROUP BY origin
    HAVING COUNT(*) >= 2000
)
SELECT origin, known, disrupted, 100.0 * disrupted / known AS disruption_pct,
       RANK() OVER (ORDER BY disrupted DESC) AS volume_rank,
       RANK() OVER (ORDER BY 1.0 * disrupted / known DESC) AS rate_rank
FROM counts
ORDER BY volume_rank, origin;
