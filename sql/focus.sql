-- Investigate the highest-volume origin and the highest-rate eligible origin.
-- Choose those from the full month; these are exploratory breakdowns.
WITH eligible AS (
    SELECT origin, COUNT(*) AS n, SUM(outcome <> 'on_time') AS bad
    FROM flights WHERE outcome <> 'unknown'
    GROUP BY origin HAVING COUNT(*) >= 2000
), focus AS (
    SELECT origin FROM (
        SELECT origin FROM eligible ORDER BY bad DESC, origin LIMIT 1
    )
    UNION
    SELECT origin FROM (
        SELECT origin FROM eligible ORDER BY 1.0 * bad / n DESC, origin LIMIT 1
    )
)
SELECT f.origin, CASE WHEN dep_hour BETWEEN 16 AND 21 THEN 'evening' ELSE 'other' END AS period,
       COUNT(*) AS known, SUM(outcome <> 'on_time') AS disrupted,
       SUM(outcome = 'late') AS late, SUM(outcome = 'cancelled') AS cancelled,
       SUM(outcome = 'diverted') AS diverted,
       100.0 * SUM(outcome <> 'on_time') / COUNT(*) AS disruption_pct
FROM flights AS f JOIN focus AS s ON f.origin = s.origin
WHERE outcome <> 'unknown'
GROUP BY f.origin, period
ORDER BY f.origin, period;
