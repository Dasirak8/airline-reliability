-- Primary KPI covers known outcomes, not just completed arrivals.
SELECT COUNT(*) AS scheduled,
       SUM(outcome <> 'unknown') AS known,
       SUM(outcome = 'unknown') AS unknown,
       SUM(outcome = 'on_time') AS on_time,
       SUM(outcome = 'late') AS late,
       SUM(outcome = 'cancelled') AS cancelled,
       SUM(outcome = 'diverted') AS diverted,
       SUM(outcome IN ('on_time', 'late')) AS completed,
       SUM(outcome IN ('late', 'cancelled', 'diverted')) AS disrupted,
       100.0 * SUM(outcome IN ('late', 'cancelled', 'diverted'))
           / NULLIF(SUM(outcome <> 'unknown'), 0) AS disruption_pct,
       100.0 * SUM(outcome = 'late')
           / NULLIF(SUM(outcome IN ('on_time', 'late')), 0) AS completed_late_pct
FROM flights;
