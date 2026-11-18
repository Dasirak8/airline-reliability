-- Scheduled local departure time is available even for cancelled flights.
WITH bucketed AS (
    SELECT CASE WHEN dep_hour BETWEEN 5 AND 10 THEN 1
                WHEN dep_hour BETWEEN 11 AND 15 THEN 2
                WHEN dep_hour BETWEEN 16 AND 21 THEN 3
                ELSE 4 END AS block, outcome
    FROM flights
    WHERE outcome <> 'unknown'
)
SELECT block,
       CASE block WHEN 1 THEN '05:00-10:59' WHEN 2 THEN '11:00-15:59'
                  WHEN 3 THEN '16:00-21:59' ELSE '22:00-04:59' END AS window,
       COUNT(*) AS known, SUM(outcome <> 'on_time') AS disrupted,
       100.0 * SUM(outcome <> 'on_time') / COUNT(*) AS disruption_pct
FROM bucketed
GROUP BY block
ORDER BY block;
