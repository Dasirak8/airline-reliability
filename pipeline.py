"""Download the pinned BTS archive and build a clean, auditable SQLite table."""
import argparse
import csv
from datetime import date
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import io
import json
from pathlib import Path
import re
import sqlite3
from urllib.request import urlopen
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parent
URL = ('https://www.transtats.bts.gov/PREZIP/'
       'On_Time_Reporting_Carrier_On_Time_Performance_1987_present_2024_12.zip')
SHA256 = 'a324577aa76d1f0aa36239f8010b6b5270fea0ff5ac846177753b76a42c5d01c'
MONTH = '2024-12'
ARCHIVE = ROOT / 'data/raw/2024-12.zip'
DATABASE = ROOT / 'data/flights.sqlite'
COLUMNS = ('FlightDate', 'Reporting_Airline', 'Flight_Number_Reporting_Airline',
           'Origin', 'Dest', 'CRSDepTime', 'ArrDelay', 'ArrDel15', 'Cancelled',
           'Diverted', 'Flights')
SCHEMA = '''CREATE TABLE flights (
    flight_date TEXT NOT NULL, carrier TEXT NOT NULL, flight_number TEXT NOT NULL,
    origin TEXT NOT NULL, destination TEXT NOT NULL,
    dep_hour INTEGER NOT NULL CHECK(dep_hour BETWEEN 0 AND 23),
    scheduled_hhmm TEXT NOT NULL,
    arrival_delay_min INTEGER,
    outcome TEXT NOT NULL CHECK(outcome IN ('on_time','late','cancelled','diverted','unknown'))
)'''
INSERT = 'INSERT INTO flights VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)'


def integer(value, *, nullable=False):
    value = value.strip()
    if nullable and not value:
        return None
    try:
        num = Decimal(value)
    except InvalidOperation as err:
        raise ValueError(f'Not a number: {value!r}') from err
    if not num.is_finite() or num != num.to_integral_value():
        raise ValueError(f'Expected an integer: {value!r}')
    return int(num)


def normalize(row):
    flight_date = date.fromisoformat(row['FlightDate'].strip()).isoformat()
    if not flight_date.startswith(MONTH + '-'):
        raise ValueError(f'Date outside {MONTH}: {flight_date}')
    carrier, number, origin, dest = (row[c].strip().upper() for c in COLUMNS[1:5])
    if not (re.fullmatch(r'[A-Z0-9]{2,3}', carrier) and number.isdigit()
            and all(re.fullmatch(r'[A-Z]{3}', a) for a in (origin, dest))):
        raise ValueError('Invalid carrier, flight number or airport')
    hhmm = row['CRSDepTime'].strip().zfill(4)
    if not re.fullmatch(r'(?:[01]\d|2[0-3])[0-5]\d|2400', hhmm):
        raise ValueError(f'Invalid scheduled departure: {hhmm!r}')
    hour = int(hhmm[:2]) % 24  # Clock grouping only. Do not shift BTS FlightDate.
    cancelled, diverted, count = (integer(row[c]) for c in ('Cancelled','Diverted','Flights'))
    if cancelled not in (0, 1) or diverted not in (0, 1) or count != 1:
        raise ValueError('Invalid status flag or more than one flight per source row')
    delay = integer(row['ArrDelay'], nullable=True)
    if cancelled or diverted:
        outcome = 'cancelled' if cancelled else 'diverted'
        delay = None  # Ordinary arrival-delay KPI excludes these flights.
    elif delay is None:
        if row['ArrDel15'].strip():
            raise ValueError('Arrival flag present without arrival delay')
        outcome = 'unknown'
    else:
        late = delay >= 15
        if integer(row['ArrDel15']) != int(late):
            raise ValueError('ArrDel15 disagrees with ArrDelay >= 15')
        outcome = 'late' if late else 'on_time'
    return flight_date, carrier, number, origin, dest, hour, hhmm, delay, outcome


def ingest(db, stream):
    """Retain distinct source records, even if their candidate keys collide."""
    reader = csv.reader(stream)
    header = next(reader)
    missing = set(COLUMNS) - set(header)
    if missing:
        raise ValueError(f'Missing columns: {sorted(missing)}')
    indices = {col: header.index(col) for col in COLUMNS}
    seen = set()
    raw_rows = duplicates = 0

    def rows():
        nonlocal raw_rows, duplicates
        for row in reader:
            raw_rows += 1
            if len(row) != len(header):
                raise ValueError(f'Wrong column count on CSV record {raw_rows}')
            # Hash every source field (including fields discarded in projection).
            key = sha256(json.dumps(row, ensure_ascii=False, separators=(',', ':')).encode()).digest()
            if key in seen:
                duplicates += 1
                continue
            seen.add(key)
            try:
                yield normalize({col: row[i] for col, i in indices.items()})
            except ValueError as err:
                raise ValueError(f'CSV record {raw_rows}: {err}') from err

    db.execute(SCHEMA)
    db.executemany(INSERT, rows())
    extra, = db.execute('''SELECT COALESCE(SUM(n - 1), 0) FROM (
        SELECT COUNT(*) AS n FROM flights
        GROUP BY flight_date, carrier, flight_number, origin, destination, scheduled_hhmm
        HAVING COUNT(*) > 1)''').fetchone()
    clean, earliest, latest = db.execute('SELECT COUNT(*), MIN(flight_date), MAX(flight_date) FROM flights').fetchone()
    if not clean or clean + duplicates != raw_rows:
        raise ValueError('Empty dataset or row counts did not reconcile')
    return dict(raw_rows=raw_rows, exact_duplicates=duplicates, clean_rows=clean,
                extra_rows_on_candidate_key=extra, first_date=earliest, last_date=latest)


def insert_fixture(db, rows):
    db.execute(SCHEMA)
    db.executemany(INSERT, map(normalize, rows))


def query(db, name):
    path = ROOT / 'sql' / f'{name}.sql'
    cur = db.execute(path.read_text(encoding='utf-8'))
    return [dict(zip([col[0] for col in cur.description], row)) for row in cur]


def get_source():
    ARCHIVE.parent.mkdir(parents=True, exist_ok=True)
    if not ARCHIVE.exists():
        temp = ARCHIVE.with_suffix('.zip.part')
        with urlopen(URL, timeout=90) as response, temp.open('wb') as out:
            while chunk := response.read(1024 * 1024):
                out.write(chunk)
        with temp.open('rb') as stream:
            if __import__('hashlib').file_digest(stream, 'sha256').hexdigest() != SHA256:
                raise ValueError('Download checksum changed; investigate before rebuilding')
        temp.replace(ARCHIVE)
    with ARCHIVE.open('rb') as stream:
        if __import__('hashlib').file_digest(stream, 'sha256').hexdigest() != SHA256:
            raise ValueError('Cached archive checksum does not match pinned source')


def build():
    get_source()
    target = DATABASE.with_suffix('.build.sqlite')
    target.unlink(missing_ok=True)
    try:
        with ZipFile(ARCHIVE) as archive:
            member, = [name for name in archive.namelist() if name.lower().endswith('.csv')]
            with archive.open(member) as raw, io.TextIOWrapper(raw, encoding='utf-8-sig', newline='') as stream:
                db = sqlite3.connect(target)
                try:
                    audit = ingest(db, stream)
                    db.commit()
                    if db.execute('PRAGMA quick_check').fetchone() != ('ok',):
                        raise ValueError('Database integrity check failed')
                finally:
                    db.close()
        target.replace(DATABASE)
    except Exception:
        target.unlink(missing_ok=True)
        raise
    output = ROOT / 'outputs'
    output.mkdir(exist_ok=True)
    audit.update(source_url=URL, sha256=SHA256, csv_member=member)
    (output / 'audit.json').write_text(json.dumps(audit, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(audit, indent=2))


if __name__ == '__main__':
    argparse.ArgumentParser(description=__doc__).parse_args()
    build()
