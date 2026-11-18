# Analyst walkthrough

## 1. Translate the business question into a decision

“Which airport is worst?” is ambiguous. A reliability team could want to reduce the most disruptions or find a place where the typical flight has high risk. Those are two different rankings. Define both and decide which one drives the recommendation: **count first** in this case because the team is trying to reach the most disrupted legs.

The observation window is December 2024. The grain is one scheduled nonstop domestic flight leg for a reporting operating carrier. JFK to LAX and LAX to JFK are different routes. A row is not a ticket, itinerary or airport.

## 2. Clean only what the analysis needs

Open `pipeline.py`. Python uses the CSV header to find the needed 11 source fields rather than keeping every column. The reader streams the ZIP member. Carrier and airport codes are stripped and uppercased; service dates are validated; scheduled HHMM becomes a clock hour. Status and delay values such as `1.00` and `-12.00` become integers without turning missing delay into zero. Negative arrival delays mean early arrivals and are kept.[1]

The exact-duplicate check fingerprints **all** parsed source fields before selecting columns. A second check measures collisions on the candidate key: service date, reporting carrier, flight number, origin, destination and scheduled HHMM. A candidate-key collision alone does not justify deleting a row. This snapshot had neither kind, so the row count stayed **590,581 → 590,581**.

| Source example | Clean outcome | Contribution |
|---|---|---|
| `ArrDelay=-12`, no cancellation/diversion | `on_time` | Denominator only |
| `ArrDelay=14`, no cancellation/diversion | `on_time` | Denominator only |
| `ArrDelay=15`, no cancellation/diversion | `late` | Numerator and denominator |
| Cancelled, `ArrDelay` missing | `cancelled` | Numerator and denominator |
| Diverted, `ArrDelay` missing | `diverted` | Numerator and denominator |
| Neither flag set, no arrival delay or late flag | `unknown` | Counted in scheduled total but excluded from the rate |

There were no unknown outcomes in this archive. For completed arrivals, the code cross-checks the computed 15-minute threshold against the source `ArrDel15` flag. An invalid date, status or contradictory arrival flag stops the build rather than quietly discarding flights.

## 3. Let SQL produce the numbers

In `sql/overview.sql`, `COUNT(*)` gives scheduled legs. In SQLite, a Boolean test produces 1 or 0, so summing the disruption condition counts flights with the matching statuses. Multiply by `100.0` to force non-integer division. `NULLIF(denominator, 0)` avoids division by zero.

```sql
SELECT COUNT(*) AS known_flights,
       SUM(outcome <> 'on_time') AS disrupted_flights,
       100.0 * SUM(outcome <> 'on_time') / NULLIF(COUNT(*), 0) AS disruption_pct
FROM flights
WHERE outcome <> 'unknown';
```

This returns 590,581 known flights, 129,470 disrupted flights and 21.92%. Do not take an unweighted average of daily percentages: each date can have a different number of flights. Keep the underlying numerator and denominator in every exported table.

`daypart.sql` assigns scheduled origin-local hours to four non-overlapping blocks with `CASE`, then groups by block. `airports.sql` groups by scheduled origin, filters small groups with `HAVING COUNT(*) >= 2000` and gives count and rate separate `RANK()` values. That 2,000 threshold is a review rule to avoid putting tiny origins at the top based on a small sample, not a universal standard. The focus query explicitly examines evening and other hours at the two leading origins to check that the DFW/evening recommendation holds at their intersection, not just in separate rankings.

`matched.sql` checks whether the early/evening comparison disappears if you hold carrier and directional route mix constant. It keeps groups with at least ten known flights in **both** 05:00–10:59 and 16:00–21:59. It calculates each window's rate within each group and weights both rates by the same pooled flight count (early + evening). Imagine two routes represent 75% and 25% of pooled volume: use those weights for both early and evening, even if their separate route mixes differ. The gap was **12.3 points** after this check versus **11.6 points** overall. It is not a day-, weather- or aircraft-adjusted causal estimate and does not represent routes flying only one window.

## 4. Use a chart for each decision

| Chart | Why this design? | What it answers |
|---|---|---|
| [Daily line](report/daily_trend.png) | Days have a sequence; the monthly benchmark makes spikes visible. | December 29 reached 42.3%. Review that date, but don't explain the spike without more data. |
| [Departure-window bars](report/departure_windows.png) | The time blocks are discrete categories. Start at zero, print rates and show denominators so the small overnight bucket is visible. | Evening had the highest rate (27.9%); early departures had 16.3%. |
| [Two aligned airport rankings](report/airport_priorities.png) | Compare count and rate for the same airports without a dual axis or a unit mismatch. Show the top five by count and add the rate leader. | DFW is the highest-volume starting point. SAN is the highest-rate origin among eligible airports. |

## 5. Make the recommendation and say what would change it

Start with DFW evening: **3,172 disruptions in 9,595 legs**. If the team is instead investigating where risk is most concentrated, use SAN as the comparison site. Within DFW evening, separate delayed flights from cancellations and diversions, then rank carrier-route-date segments. Compare several months and join weather and incoming-aircraft data to check possible explanations. Only then test an intervention. The project measures historical flight outcomes, not preventable disruptions or future improvements.

## Sources

[1] https://www.transtats.bts.gov/Fields.asp?gnoyr_VQ=FGJ — BTS TranStats reporting carrier data dictionary
