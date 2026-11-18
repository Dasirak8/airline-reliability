import csv
import io
import sqlite3
import unittest

import pipeline as p


def record(**changes):
    row = dict(FlightDate='2024-12-01', Reporting_Airline=' aa ',
               Flight_Number_Reporting_Airline='17', Origin='jfk', Dest='LAX',
               CRSDepTime='0605', ArrDelay='15.00', ArrDel15='1.00',
               Cancelled='0.00', Diverted='0.00', Flights='1.00')
    return row | changes


class FlightTests(unittest.TestCase):
    def test_arrival_threshold_and_normalization(self):
        self.assertEqual(p.normalize(record())[1:6], ('AA', '17', 'JFK', 'LAX', 6))
        self.assertEqual(p.normalize(record())[-1], 'late')
        self.assertEqual(p.normalize(record(ArrDelay='14', ArrDel15='0'))[-1], 'on_time')
        self.assertEqual(p.normalize(record(ArrDelay='-27', ArrDel15='0'))[-2:], (-27, 'on_time'))

    def test_cancelled_diverted_and_missing_arrival_are_distinct(self):
        self.assertEqual(p.normalize(record(Cancelled='1', Diverted='1', ArrDelay='', ArrDel15=''))[-2:], (None, 'cancelled'))
        self.assertEqual(p.normalize(record(Diverted='1', ArrDelay=''))[-1], 'diverted')
        self.assertEqual(p.normalize(record(ArrDelay='', ArrDel15=''))[-1], 'unknown')

    def test_invalid_schedule_or_inconsistent_flag_does_not_silently_pass(self):
        for changes in ({'CRSDepTime': '1260'}, {'ArrDelay': '14'}, {'Cancelled': ''}, {'Flights': '2'}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                p.normalize(record(**changes))

    def test_exact_duplicate_dropped_but_projected_key_collision_retained(self):
        buf = io.StringIO()
        rows = [record(Tail_Number='A'), record(Tail_Number='A'), record(Tail_Number='B')]
        w = csv.DictWriter(buf, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
        buf.seek(0)
        with sqlite3.connect(':memory:') as db:
            audit = p.ingest(db, buf)
            self.assertEqual((audit['raw_rows'], audit['exact_duplicates'], audit['clean_rows']), (3, 1, 2))
            self.assertEqual(audit['extra_rows_on_candidate_key'], 1)

    def test_sql_disruption_keeps_cancellations_diversions_but_not_unknown(self):
        with sqlite3.connect(':memory:') as db:
            p.insert_fixture(db, [record(), record(Cancelled='1'), record(Diverted='1'),
                                  record(ArrDelay='', ArrDel15=''), record(ArrDelay='0', ArrDel15='0')])
            row = p.query(db, 'overview')[0]
            self.assertEqual((row['scheduled'], row['known'], row['disrupted'], row['completed']), (5, 4, 3, 2))
            self.assertEqual(row['disruption_pct'], 75.0)
            self.assertEqual(row['completed_late_pct'], 50.0)

    def test_matched_comparison_excludes_unpaired_and_uses_common_weights(self):
        early = record(CRSDepTime='0655', ArrDelay='0', ArrDel15='0')
        evening = record(CRSDepTime='1700')
        rows = [early] * 11 + [evening] * 10
        rows += [early | {'Dest': 'DEN', 'ArrDelay': '30', 'ArrDel15': '1'}] * 10
        rows += [evening | {'Dest': 'DEN'}] * 10
        rows += [evening | {'Dest': 'SFO'}] * 100
        with sqlite3.connect(':memory:') as db:
            p.insert_fixture(db, rows)
            row = p.query(db, 'matched')[0]
            self.assertEqual((row['groups'], row['early_n'], row['evening_n']), (2, 21, 20))
            self.assertAlmostEqual(row['matched_gap_pp'], 100.0 * 21 / 41)
