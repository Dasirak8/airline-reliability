-- FlightDate is the source service date, not the actual arrival date.
SELECT flight_date, COUNT(*) AS known,
       SUM(outcome <> 'on_time') AS disrupted,
       100.0 * SUM(outcome <> 'on_time') / COUNT(*) AS disruption_pct
FROM flights
WHERE outcome <> 'unknown'
GROUP BY flight_date
ORDER BY flight_date;
