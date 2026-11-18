"""Run SQL, reconcile counts, export result tables and render the portfolio report."""
import csv
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
from textwrap import fill

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.backends.backend_pdf import PdfPages
from matplotlib.ticker import PercentFormatter, StrMethodFormatter

from pipeline import DATABASE, ROOT, query

OUT = ROOT / 'outputs'
REPORT = ROOT / 'report'
NAMES = ('overview', 'daily', 'daypart', 'airports', 'matched', 'focus')
# US DOT February 2025 Air Travel Consumer Report, Table 7A (December total).
BENCHMARK = dict(scheduled=590581, on_time=461111, cancelled=4151, diverted=1598)
NAVY, MUTED, TEAL, ORANGE, PALE = '#102a43', '#486581', '#087e83', '#a64215', '#e9f2f6'
plt.rcParams.update({'font.family': 'DejaVu Sans', 'text.color': NAVY,
                     'axes.labelcolor': MUTED, 'xtick.color': MUTED,
                     'ytick.color': MUTED, 'axes.spines.top': False,
                     'axes.spines.right': False, 'axes.edgecolor': '#afc3d4',
                     'axes.axisbelow': True, 'grid.color': '#dce8ef', 'pdf.fonttype': 42})


def validate(tables, audit):
    a = tables['overview'][0]
    if not (a['scheduled'] == audit['clean_rows'] == a['known'] + a['unknown']
            and a['known'] == a['on_time'] + a['disrupted']
            and a['disrupted'] == a['late'] + a['cancelled'] + a['diverted']):
        raise ValueError('Outcome totals do not reconcile')
    for group in ('daily', 'daypart'):
        for measure in ('known', 'disrupted'):
            if sum(r[measure] for r in tables[group]) != a[measure]:
                raise ValueError(f'{group} does not reconcile on {measure}')
    if len(tables['daily']) != 31 or len(tables['daypart']) != 4:
        raise ValueError('Missing flight date or time block')
    for origin in {r['origin'] for r in tables['focus']}:
        subset = [r for r in tables['focus'] if r['origin'] == origin]
        airport = next(r for r in tables['airports'] if r['origin'] == origin)
        for measure in ('known', 'disrupted'):
            if sum(r[measure] for r in subset) != airport[measure]:
                raise ValueError(f'{origin} drill-down does not reconcile')
    if any(a[k] != n for k, n in BENCHMARK.items()):
        raise ValueError('Aggregate counts do not match DOT Table 7A')


def page(n, section, title, subtitle):
    fig = plt.figure(figsize=(8.5, 11), facecolor='white')
    fig.add_artist(plt.Rectangle((0, .982), 1, .018, color=TEAL, transform=fig.transFigure))
    fig.text(.09, .934, f'FLIGHT RELIABILITY  /  {section.upper()}', fontsize=10, weight='bold', color=TEAL)
    fig.text(.09, .885, title, fontsize=23, weight='bold')
    fig.text(.09, .855, subtitle, fontsize=10, color=MUTED)
    fig.add_artist(plt.Line2D([.09, .91], [.057, .057], transform=fig.transFigure, color=PALE))
    fig.text(.09, .035, 'BTS REPORTING CARRIERS  /  DECEMBER 2024  /  PORTFOLIO CASE STUDY', fontsize=8, color=MUTED)
    fig.text(.91, .035, f'{n:02d} / 04', fontsize=8, ha='right', color=MUTED)
    return fig


def block(fig, y, title, body, width=88):
    fig.text(.09, y, title, fontsize=12, weight='bold', va='top')
    fig.text(.09, y - .031, fill(body, width=width), fontsize=10.2, linespacing=1.52, va='top', color=MUTED)


def axes(fig, panel=None, **kwargs):
    ax = fig.add_axes(panel, **kwargs) if fig else plt.subplots(figsize=(9, 4.1), layout='constrained')[1]
    return ax


def trend(rows, overall, fig=None):
    ax = axes(fig, [.145, .295, .75, .275])
    x = [int(r['flight_date'][-2:]) for r in rows]
    y = [r['disruption_pct'] for r in rows]
    peak = max(rows, key=lambda r: r['disruption_pct'])
    day = int(peak['flight_date'][-2:])
    ax.fill_between(x, y, color='#d7edf0')
    ax.plot(x, y, color=TEAL, lw=2.2)
    ax.axhline(overall, ls='--', color=MUTED, lw=1.15, label=f'Month overall: {overall:.1f}%')
    ax.scatter(day, max(y), s=45, color=ORANGE, zorder=3)
    ax.annotate(f'Dec {day}: {max(y):.1f}%', (day, max(y)), xytext=(-17, 8), textcoords='offset points',
                ha='right', color=ORANGE, weight='bold')
    ax.set(xlim=(1, 31), ylim=(0, 49), xlabel='Scheduled service date (December 2024)', ylabel='Disruption rate')
    ax.set_xticks([1, 5, 10, 15, 20, 25, 31])
    ax.yaxis.set_major_formatter(PercentFormatter(100))
    ax.grid(axis='y')
    ax.legend(loc='upper left', frameon=False, fontsize=9)
    if not fig:
        ax.set_title('Daily disruption rate for reporting carriers', loc='left', weight='bold', pad=12)
    return ax.figure


def daypart(rows, fig=None):
    ax = axes(fig, [.15, .42, .74, .33])
    rates = [r['disruption_pct'] for r in rows]
    bars = ax.bar(range(len(rows)), rates, color=[ORANGE if r['block'] == 3 else TEAL for r in rows], width=.62)
    ax.bar_label(bars, labels=[f'{v:.1f}%' for v in rates], padding=4, fontsize=10, weight='bold')
    ax.set_xticks(range(len(rows)), [f"{r['window']}\n(n={r['known']:,})" for r in rows], fontsize=9)
    ax.set(ylim=(0, 35), ylabel='Disruption rate', xlabel='Scheduled departure window (origin local time)')
    ax.yaxis.set_major_formatter(PercentFormatter(100))
    ax.grid(axis='y')
    if not fig:
        ax.set_title('Evening had the highest disruption rate', loc='left', weight='bold', pad=12)
    return ax.figure


def airport_bars(rows, fig=None):
    lead = next(r for r in rows if r['rate_rank'] == 1)
    chosen = rows[:5] + ([lead] if lead not in rows[:5] else [])
    x = list(range(len(chosen)))
    if fig:
        a = axes(fig, [.165, .47, .325, .28])
        b = axes(fig, [.56, .47, .32, .28], sharey=a)
    else:
        fig, (a, b) = plt.subplots(1, 2, figsize=(9, 4.1), sharey=True, layout='constrained')
    colors = [ORANGE if r['origin'] == lead['origin'] else TEAL if i == 0 else '#82a8bd' for i, r in enumerate(chosen)]
    counts = a.barh(x, [r['disrupted'] for r in chosen], color=colors, height=.6)
    rates = b.barh(x, [r['disruption_pct'] for r in chosen], color=colors, height=.6)
    a.bar_label(counts, labels=[f"{r['disrupted']:,}" for r in chosen], padding=3, fontsize=9)
    b.bar_label(rates, labels=[f"{r['disruption_pct']:.1f}%" for r in chosen], padding=3, fontsize=9)
    a.set_yticks(x, [r['origin'] for r in chosen], weight='bold')
    a.set(xlim=(0, 8800), xlabel='Disrupted flights')
    b.set(xlim=(0, 41), xlabel='Disruption rate')
    a.set_xticks(range(0, 8801, 2000))
    a.xaxis.set_major_formatter(StrMethodFormatter('{x:,.0f}'))
    b.xaxis.set_major_formatter(PercentFormatter(100))
    b.tick_params(axis='y', left=False, labelleft=False)
    a.invert_yaxis()
    for ax, title in [(a, 'Disruption count'), (b, 'Rate at the same airports')]:
        ax.grid(axis='x')
        ax.set_title(title, loc='left', weight='bold', fontsize=11)
    return fig


def make_report(tables, audit):
    a, m = tables['overview'][0], tables['matched'][0]
    windows, airports, daily = (tables[name] for name in ('daypart', 'airports', 'daily'))
    early, _, eve, _ = windows
    d = max(daily, key=lambda r: r['disruption_pct'])
    v = airports[0]
    r = next(row for row in airports if row['rate_rank'] == 1)
    focus = {(row['origin'], row['period']): row for row in tables['focus']}
    ve, vo = focus[v['origin'], 'evening'], focus[v['origin'], 'other']
    re = focus[r['origin'], 'evening']
    REPORT.mkdir(exist_ok=True)
    for name, chart in [('daily_trend', trend(daily, a['disruption_pct'])),
                        ('departure_windows', daypart(windows)), ('airport_priorities', airport_bars(airports))]:
        chart.savefig(REPORT / f'{name}.png', dpi=155, bbox_inches='tight', pad_inches=.18)
        plt.close(chart)
    with PdfPages(REPORT / 'airline-reliability.pdf', metadata={
            'Title': 'Where should flight operations investigate first?',
            'Subject': 'December 2024 U.S. flight disruption | SQL and Python case study',
            'CreationDate': datetime(2026, 11, 18, tzinfo=timezone.utc)}) as pdf:
        fig = page(1, 'Decision brief', 'Where to investigate flight disruption', 'U.S. nonstop domestic flights reported to the Bureau of Transportation Statistics [1, 3]')
        for x, label, value in [(.09, 'Scheduled flights', f"{a['scheduled']:,}"),
                                (.37, 'Disrupted flights', f"{a['disrupted']:,}"),
                                (.65, 'Disruption rate', f"{a['disruption_pct']:.2f}%")]:
            fig.add_artist(plt.Rectangle((x, .724), .26, .085, color=PALE, transform=fig.transFigure))
            fig.text(x + .015, .774, value, fontsize=21, weight='bold')
            fig.text(x + .015, .744, label, fontsize=10, color=MUTED)
        block(fig, .686, f"Recommendation: start with {v['origin']}'s evening departures",
              f"The 16:00-21:59 local window had {ve['disrupted']:,} disruptions out of "
              f"{ve['known']:,} scheduled flights at {v['origin']} ({ve['disruption_pct']:.1f}%). "
              f"Use {r['origin']} as a second review site if the goal is to find high-rate problems.")
        trend(daily, a['disruption_pct'], fig)
        block(fig, .215, 'Why a line chart?', f"Dates have a natural order, so a line reveals spikes hidden by the monthly rate. "
              f"The peak was December {int(d['flight_date'][-2:])}: {d['disrupted']:,} of {d['known']:,} flights "
              f"({d['disruption_pct']:.1f}%). Use it as a drill-down date, not proof of a specific cause.")
        pdf.savefig(fig); plt.close(fig)

        fig = page(2, 'When', 'Evening had the highest disruption rate', 'Disrupted flights divided by known outcomes in each scheduled departure window')
        daypart(windows, fig)
        block(fig, .333, 'Why a bar chart?', f"The time buckets are categories. A zero-based bar chart makes rate differences easy to compare. "
              f"Evening reached {eve['disruption_pct']:.1f}% versus {early['disruption_pct']:.1f}% early, "
              f"a gap of {eve['disruption_pct'] - early['disruption_pct']:.1f} percentage points.")
        block(fig, .213, 'Does the carrier and route mix explain the gap?',
              f"Using the same mix of {m['groups']:,} carrier-route groups with at least 10 flights in both windows, "
              f"the gap was {m['matched_gap_pp']:.1f} points ({m['early_matched_pct']:.1f}% vs. "
              f"{m['evening_matched_pct']:.1f}%). The difference persisted in that subset. Day, weather and aircraft rotation remain uncontrolled.")
        pdf.savefig(fig); plt.close(fig)

        fig = page(3, 'Where', 'Count and rate answer different questions', f"Five busiest by disruptions plus the rate leader, from {len(airports)} origins with at least 2,000 outcomes")
        airport_bars(airports, fig)
        block(fig, .385, 'Why use two aligned bar charts?', f"The left panel shows where the most flights were affected; the right shows how often it happened. "
              f"{v['origin']} led by count ({v['disrupted']:,}, {v['disruption_pct']:.1f}%). "
              f"{r['origin']} led by rate ({r['disruption_pct']:.1f}%, {r['disrupted']:,}); it ranked #{r['volume_rank']} by count.")
        block(fig, .263, 'Where to start the review', f"{v['origin']}'s evening rate was {ve['disruption_pct']:.1f}% vs. "
              f"{vo['disruption_pct']:.1f}% in its other hours. {re['disrupted']:,} {r['origin']} evening flights were disrupted "
              f"({re['disruption_pct']:.1f}%). Start at {v['origin']} for volume and use {r['origin']} to investigate high risk.")
        fig.text(.09, .127, 'An origin label tells us where a flight was scheduled to leave, not where the disruption began.',
                 color=MUTED, fontsize=9, style='italic')
        pdf.savefig(fig); plt.close(fig)

        fig = page(4, 'Methods & next steps', 'How to reproduce the decision', 'From one row per scheduled flight leg to a short list for operational review')
        block(fig, .803, '1. Clean and audit the raw file [1, 3]',
              f"Python validates dates, airport codes, scheduled time and status flags. Whole-row duplicate checking found "
              f"{audit['exact_duplicates']} duplicates; all {audit['clean_rows']:,} rows stayed. Keep negative arrival delays (early arrivals). "
              f"Keep cancelled/diverted flights but leave ordinary arrival delay blank.")
        block(fig, .657, '2. Cross-check the counts against the DOT report [2]',
              f"Disrupted means arriving 15+ minutes late, being cancelled or being diverted. The denominator is known outcomes "
              f"({a['unknown']:,} unknown here). DOT Table 7A matches scheduled, on-time, cancelled and diverted counts exactly. "
              f"Among completed non-diverted flights only, {a['completed_late_pct']:.2f}% arrived 15+ minutes late.")
        block(fig, .511, '3. Diagnose before changing the schedule',
              f"At {v['origin']}, split evening records by date, carrier and directional route; then review weather, aircraft rotations "
              f"and turn times. Confirm recurrence in other months before testing any intervention. "
              f"The case measures flight legs, not affected passengers, costs or preventable disruptions.")
        block(fig, .365, '4. Run the same analysis yourself',
              'Run python pipeline.py to rebuild SQLite, then python analyze.py to regenerate the SQL exports, PNGs and PDF. '
              'Read sql/overview.sql first, then sql/daypart.sql and sql/matched.sql. No database server is needed.')
        fig.text(.09, .195, 'Sources (click to open)', weight='bold', fontsize=11)
        refs = [('[1] BTS TranStats reporting carrier data dictionary', 'https://www.transtats.bts.gov/Fields.asp?gnoyr_VQ=FGJ'),
                ('[2] U.S. DOT February 2025 Air Travel Consumer Report, Table 7A', 'https://www.transportation.gov/sites/dot.gov/files/2025-03/February%202025%20ATCR.pdf'),
                ('[3] BTS December 2024 source archive', audit['source_url'])]
        for i, (text, url) in enumerate(refs):
            fig.text(.09, .17 - .024 * i, text, fontsize=9, color=TEAL, url=url)
        pdf.savefig(fig); plt.close(fig)


def main():
    audit = json.loads((OUT / 'audit.json').read_text(encoding='utf-8'))
    with sqlite3.connect(DATABASE.as_uri() + '?mode=ro', uri=True) as db:
        tables = {name: query(db, name) for name in NAMES}
    validate(tables, audit)
    for name, rows in tables.items():
        with (OUT / f'{name}.csv').open('w', newline='', encoding='utf-8') as stream:
            writer = csv.DictWriter(stream, fieldnames=list(rows[0]), lineterminator='\n')
            writer.writeheader()
            writer.writerows(rows)
    make_report(tables, audit)
    print('Verified daily/time/airport rollups and four DOT benchmark counts.')
    print(f'Report: {Path(REPORT / "airline-reliability.pdf").relative_to(ROOT)}')


if __name__ == '__main__':
    main()
