"""Builds voltrelay_analysis.ipynb from the cell list below.

Run:  python3 notebooks/build_notebook.py
"""
import json, os

CELLS = []
def md(s): CELLS.append(("markdown", s.strip("\n")))
def code(s): CELLS.append(("code", s.strip("\n")))

# ---------------------------------------------------------------- intro
md(r"""
# VoltRelay Energy: why did service, retention and margin get worse while volume grew?

**Period:** Jan 2024 to Jun 2025, across 6 cities (Bengaluru, Delhi NCR, Hyderabad, Pune, Mumbai, Jaipur).

**Business problem.** Swaps and revenue grew, but in the same period (1) failed or abandoned swaps and long queues rose,
(2) more new riders stopped using the service, and (3) margin per swap fell. Leadership made several changes during
this time: two station expansion waves, price changes, a peak/off-peak pricing test, a new battery supplier and a
renegotiated fleet contract. They need to know which of these actually caused the decline before they set the next budget.

**Approach.**
1. Audit and clean all 8 tables, and log every fix (section 1).
2. Build one clean, enriched swap table (section 2).
3. Answer the 6 questions: trends, failures, stations, batteries, pricing and fleets, churn (sections 3 to 8).
4. Rank the root causes by business impact and turn them into budget recommendations (section 9).

Each question section separates *what changed* from *what only happened at the same time*, by comparing
segments that were affected by a change against segments that were not.

**How to run (Google Colab).** Put the 8 CSV files in Google Drive under `MyDrive/VoltRelay/`, then use
*Runtime → Run all*. All tables and charts are written to `outputs/`, and the last cell zips them as `outputs.zip`.
A full run takes about 5 to 10 minutes.

**AI disclosure.** AI tools (Kiro) were used to help write code, check data quality and draft documentation.
The team reviewed every result, and all numbers come from running this notebook on the organizer dataset.
""")

# ---------------------------------------------------------------- setup
code(r"""
# ---------- Setup ----------
import os, sys, json, shutil, subprocess, traceback, contextlib, warnings
warnings.filterwarnings('ignore')
try:
    import duckdb
except ImportError:
    subprocess.run([sys.executable, '-m', 'pip', 'install', '-q', 'duckdb'], check=True)
    import duckdb
import numpy as np, pandas as pd
import matplotlib.pyplot as plt, matplotlib.ticker as mtick

IN_COLAB = 'google.colab' in sys.modules
if IN_COLAB:
    from google.colab import drive
    drive.mount('/content/drive')
    DATA_DIR = '/content/drive/MyDrive/VoltRelay'      # <-- folder holding the 8 CSVs
    WORK = '/content'
else:
    DATA_DIR = os.path.abspath('../data')
    WORK = os.path.abspath('..')

OUT = os.path.join(WORK, 'outputs')
for d in ('tables', 'figures'):
    os.makedirs(os.path.join(OUT, d), exist_ok=True)

FILES = ['swap_events', 'station_hourly_status', 'stations', 'batteries', 'riders',
         'support_tickets', 'city_daily_context', 'fleet_partners']
missing = [f for f in FILES if not os.path.exists(f'{DATA_DIR}/{f}.csv')]
assert not missing, f'Missing files in {DATA_DIR}: {missing}'

START, END = '2024-01-01', '2025-07-01'     # analysis window [START, END)
KM_MAX = 200                                # km per charge above this is a sensor/odometer error
LONG_WAIT_SEC = 600                         # "long queue" = waited more than 10 minutes

con = duckdb.connect(os.path.join(WORK, 'voltrelay.duckdb'))
con.execute("SET memory_limit = '8GB'")
pd.set_option('display.max_columns', 60); pd.set_option('display.width', 200)

def q(sql):
    return con.execute(sql).df()

def save(df, name):
    df.to_csv(f'{OUT}/tables/{name}.csv', index=False)
    return df

KEY, ERRORS = {}, []          # headline numbers for the report / errors from any section

@contextlib.contextmanager
def section(name):
    # Runs one analysis block; if it fails, the error is logged and the notebook keeps going.
    print(f'\n========== {name} ==========')
    try:
        yield
    except Exception:
        tb = traceback.format_exc()
        ERRORS.append({'section': name, 'error': tb})
        print(f'!!! {name} FAILED\n{tb}')

PALETTE = ['#1f6feb', '#f0883e', '#2da44e', '#cf222e', '#8250df', '#6e7781', '#bf8700', '#0a7c86']
plt.rcParams.update({'figure.dpi': 110, 'axes.spines.top': False, 'axes.spines.right': False,
                     'axes.grid': True, 'grid.alpha': .3, 'axes.titleweight': 'bold', 'axes.titlesize': 11,
                     'axes.prop_cycle': plt.cycler(color=PALETTE), 'legend.frameon': False})
PCT = mtick.PercentFormatter(1.0)

def savefig(fig, name):
    fig.tight_layout()
    fig.savefig(f'{OUT}/figures/{name}.png', dpi=150, bbox_inches='tight')
    plt.show(); plt.close(fig)

# Known business changes (from the brief + table metadata). Price / peak-pricing dates are added after discovery.
EVENTS = {
    'Kyron supplier': '2024-07-01',
    'Wave 1 stations': '2024-08-07',
    'ZipDrop 28% deal': '2024-11-01',
    'Competitor (BLR)': '2025-01-15',
    'Wave 2 stations': '2025-02-01',
    'Firmware update': '2025-03-10',
}

def mark_events(ax, keys=None):
    lo, hi = ax.get_ylim()
    for k, d in EVENTS.items():
        if keys and k not in keys:
            continue
        x = pd.Timestamp(d)
        ax.axvline(x, color='#6e7781', ls='--', lw=.8, alpha=.8)
        ax.text(x, hi, ' ' + k, rotation=90, va='top', ha='left', fontsize=7, color='#57606a')
print('Setup OK. DuckDB', duckdb.__version__)
""")

code(r"""
# ---------- Load raw CSVs (everything as text first, so bad values can be audited instead of silently dropped) ----------
SRC = DATA_DIR
if IN_COLAB:                                   # copying from Drive to local disk makes reads much faster
    SRC = '/content/data'; os.makedirs(SRC, exist_ok=True)
    for f in FILES:
        if not os.path.exists(f'{SRC}/{f}.csv') or os.path.getsize(f'{SRC}/{f}.csv') != os.path.getsize(f'{DATA_DIR}/{f}.csv'):
            shutil.copy(f'{DATA_DIR}/{f}.csv', f'{SRC}/{f}.csv')

# DuckDB's dialect sniffer rejects swap_events.csv (a few malformed rows), so we skip sniffing:
# read the header with Python, declare every column as text, and skip/log rows that do not parse.
LOAD_LOG = []
for f in FILES:
    path = f'{SRC}/{f}.csv'
    with open(path, 'r', encoding='utf-8-sig', errors='replace') as fh:
        header = [c.strip().strip('"') for c in fh.readline().strip().split(',')]
    with open(path, 'rb') as fh:
        lines = sum(1 for _ in fh) - 1
    cols = ', '.join(f"'{c}': 'VARCHAR'" for c in header)
    con.execute(f'''CREATE OR REPLACE TABLE raw_{f} AS SELECT * FROM read_csv('{path}', auto_detect=false, header=true,
                    delim=',', quote='"', escape='"', columns={{{cols}}}, strict_mode=false, ignore_errors=true,
                    null_padding=true, max_line_size=10000000)''')
    n = con.execute(f'SELECT COUNT(*) FROM raw_{f}').fetchone()[0]
    LOAD_LOG.append(dict(file=f, file_lines=lines, rows_loaded=n, rows_dropped=lines - n))
    print(f'{f:24s} {n:>11,d} rows loaded | {lines:>11,d} lines in file | {lines - n:,} dropped')
save(pd.DataFrame(LOAD_LOG), 'load_log')
""")

# ---------------------------------------------------------------- 1. data quality
md(r"""
## 1. Data quality audit and cleaning

Every check below is logged in `outputs/tables/dq_log.csv` with the number of rows affected and the action taken.
""")

code(r"""
DQ = []
def dq(table, issue, rows, action):
    DQ.append(dict(table=table, issue=issue, rows=int(rows or 0), action=action))
for x in LOAD_LOG:
    if x['rows_dropped'] > 0:
        dq(x['file'], 'Malformed CSV lines (wrong column count / broken quotes)', x['rows_dropped'], 'Skipped at load')

# ---- swap_events ----
a = q(f'''
WITH s AS (
  SELECT *, TRY_CAST(event_ts AS TIMESTAMP) ts, TRY_CAST(km_since_last_swap AS DOUBLE) km,
         TRY_CAST(soh_in_pct AS DOUBLE) soh_i, TRY_CAST(soh_out_pct AS DOUBLE) soh_o,
         TRY_CAST(soc_in_pct AS DOUBLE) soc_i, TRY_CAST(soc_out_pct AS DOUBLE) soc_o,
         TRY_CAST(queue_wait_sec AS DOUBLE) qw, TRY_CAST(list_price_inr AS DOUBLE) lp,
         TRY_CAST(discount_inr AS DOUBLE) dc, TRY_CAST(amount_charged_inr AS DOUBLE) amt
  FROM raw_swap_events)
SELECT COUNT(*) total,
  COUNT(*) - COUNT(DISTINCT event_id)                                   dup_event_id,
  COUNT(*) FILTER (WHERE ts IS NULL)                                    bad_ts,
  COUNT(*) FILTER (WHERE ts < TIMESTAMP '{START}' OR ts >= TIMESTAMP '{END}') outside_window,
  COUNT(*) FILTER (WHERE station_id LIKE 'STN-TST-%')                   test_station,
  COUNT(*) FILTER (WHERE station_id NOT IN (SELECT station_id FROM raw_stations)) unknown_station,
  COUNT(*) FILTER (WHERE rider_id NOT IN (SELECT rider_id FROM raw_riders))       unknown_rider,
  COUNT(*) FILTER (WHERE km < 0)                                        neg_km,
  COUNT(*) FILTER (WHERE km > {KM_MAX})                                 km_too_high,
  COUNT(*) FILTER (WHERE soh_i > 100 OR soh_o > 100)                    soh_over_100,
  COUNT(*) FILTER (WHERE soc_i > 100 OR soc_o > 100)                    soc_over_100,
  COUNT(*) FILTER (WHERE qw < 0)                                        neg_wait,
  COUNT(*) FILTER (WHERE qw IS NULL)                                    null_wait,
  COUNT(*) FILTER (WHERE event_type = 'swap_completed' AND battery_out_id IS NULL) completed_no_battery_out,
  COUNT(*) FILTER (WHERE event_type = 'swap_completed' AND ABS(lp - dc - amt) > 0.01) price_mismatch,
  COUNT(*) FILTER (WHERE event_type <> 'swap_completed' AND amt > 0)    charged_on_failed
FROM s''').iloc[0]
display(a.to_frame('rows'))

dups = q('''
SELECT sync_mode,
       COUNT(*) FILTER (WHERE rn_ts > 1)  AS extra_same_timestamp,
       COUNT(*) FILTER (WHERE rn_bat > 1) AS extra_same_battery_pair
FROM (SELECT sync_mode,
        ROW_NUMBER() OVER (PARTITION BY rider_id, station_id, event_ts, event_type ORDER BY event_id) rn_ts,
        ROW_NUMBER() OVER (PARTITION BY rider_id, COALESCE(battery_in_id, event_id),
                           COALESCE(battery_out_id, event_id), event_type ORDER BY event_ts, event_id) rn_bat
      FROM raw_swap_events) GROUP BY 1''')
display(dups)
print('event types:'); display(q('SELECT event_type, COUNT(*) n FROM raw_swap_events GROUP BY 1 ORDER BY 2 DESC'))

dq('swap_events', 'Duplicate event_id', a.dup_event_id, 'Keep first')
dq('swap_events', 'Same rider+station+timestamp+type logged twice (re-sync)', dups.extra_same_timestamp.sum(), 'Drop extra copies')
dq('swap_events', 'Same rider + same battery pair completed twice', dups.extra_same_battery_pair.sum(), 'Drop extra copies')
dq('swap_events', 'Unparseable timestamp', a.bad_ts, 'Drop')
dq('swap_events', 'Outside Jan 2024 - Jun 2025', a.outside_window, 'Drop')
dq('swap_events', 'Test stations STN-TST-*', a.test_station, 'Drop (not real customers)')
dq('swap_events', 'Unknown station / rider id', a.unknown_station + a.unknown_rider, 'Drop')
dq('swap_events', 'Negative km since last swap', a.neg_km, 'Set km to NULL')
dq('swap_events', f'km since last swap > {KM_MAX} (impossible on one charge)', a.km_too_high, 'Set km to NULL')
dq('swap_events', 'Battery SoH > 100%', a.soh_over_100, 'Cap at 100')
dq('swap_events', 'Battery SoC > 100%', a.soc_over_100, 'Cap at 100')
dq('swap_events', 'Negative / missing queue wait', a.neg_wait + a.null_wait, 'Set to NULL')
dq('swap_events', 'Completed swap without battery_out', a.completed_no_battery_out, 'Flag only')
dq('swap_events', 'amount != list price - discount', a.price_mismatch, 'Keep amount_charged as truth')
dq('swap_events', 'Money charged on a failed swap', a.charged_on_failed, 'Flag only')
""")

code(r"""
# ---- other tables ----
r = q('''SELECT
  COUNT(*) FILTER (WHERE home_city NOT IN ('Bengaluru','Delhi NCR','Hyderabad','Pune','Mumbai','Jaipur')) bad_city,
  COUNT(*) FILTER (WHERE NULLIF(declared_shift,'') IS NULL) no_shift,
  COUNT(*) FILTER (WHERE NULLIF(age_band,'') IS NULL) no_age,
  COUNT(*) FILTER (WHERE NULLIF(partner_id,'') IS NULL) no_partner,
  COUNT(*) FILTER (WHERE signup_date LIKE '2023-12%') dec23_signups
FROM raw_riders''').iloc[0]
print('riders home_city variants:'); display(q('SELECT home_city, COUNT(*) n FROM raw_riders GROUP BY 1 ORDER BY 2 DESC'))
nv = q("SELECT COUNT(DISTINCT home_city) FROM raw_riders WHERE home_city NOT IN ('Bengaluru','Delhi NCR','Hyderabad','Pune','Mumbai','Jaipur')").iloc[0, 0]
dq('riders', f'home_city written {nv} non-standard ways (BLR, Bombay, Gurgaon, "bengaluru ", ...)', r.bad_city, 'Map to 6 standard cities')
dq('riders', 'Missing declared_shift', r.no_shift, "Fill 'unknown'")
dq('riders', 'Missing age_band', r.no_age, "Fill 'unknown'")
dq('riders', 'No partner_id (retail riders, not an error)', r.no_partner, "Label 'RETAIL'")
dq('riders', 'Only a handful of signups in Dec 2023 (data gap)', r.dec23_signups, 'Flag; outside window')

s = q('''SELECT COUNT(*) FILTER (WHERE station_id LIKE 'STN-TST-%') test,
                COUNT(*) FILTER (WHERE NULLIF(decommissioned_date,'') IS NOT NULL) decom FROM raw_stations''').iloc[0]
dq('stations', 'Test stations STN-TST-01/02', s.test, 'Exclude everywhere')
dq('stations', 'Decommissioned station', s.decom, 'Count costs only while active')

b = q('''SELECT COUNT(*) FILTER (WHERE NULLIF(retired_date,'') IS NOT NULL) retired,
                COUNT(DISTINCT retired_date) FILTER (WHERE NULLIF(retired_date,'') IS NOT NULL) retire_dates,
                COUNT(*) FILTER (WHERE TRY_CAST(commission_date AS DATE) < TRY_CAST(manufacture_date AS DATE)) bad_dates
         FROM raw_batteries''').iloc[0]
dq('batteries', "Retired packs all share one date and say 'end_of_life' although < 1 year old", b.retired, 'Treat as a recall; analysed in section 6')
dq('batteries', 'Commissioned before manufactured', b.bad_dates, 'Flag')

t = q(f'''SELECT COUNT(*) FILTER (WHERE resolution_status = 'duplicate') dup,
     COUNT(*) FILTER (WHERE TRY_CAST(created_ts AS TIMESTAMP) < TIMESTAMP '{START}' OR TRY_CAST(created_ts AS TIMESTAMP) >= TIMESTAMP '{END}') outside,
     COUNT(*) FILTER (WHERE station_id LIKE 'STN-TST-%') test,
     COUNT(*) FILTER (WHERE regexp_matches(rider_comment, 'staion|battry|rnage')) typos,
     COUNT(*) FILTER (WHERE category = 'other' AND regexp_matches(lower(rider_comment), 'vehicle performance|bike problem|scooter weak')) other_vehicle
   FROM raw_support_tickets''').iloc[0]
dq('support_tickets', "resolution_status = 'duplicate'", t.dup, 'Drop')
dq('support_tickets', 'Created outside the analysis window', t.outside, 'Drop')
dq('support_tickets', 'Raised at test stations', t.test, 'Drop')
dq('support_tickets', 'Typos in comments (staion, battry, rnage)', t.typos, 'Fix spelling')
dq('support_tickets', "'other' tickets that describe weak vehicle / range", t.other_vehicle, "Re-tag 'vehicle_performance' (tested in section 6)")

h = q('''SELECT COUNT(*) - COUNT(DISTINCT station_id || '|' || hour_start) dup,
                COUNT(*) FILTER (WHERE station_id LIKE 'STN-TST-%') test,
                COUNT(*) FILTER (WHERE telemetry_status <> 'ok') AS partial_rows,
                COUNT(*) FILTER (WHERE TRY_CAST(charged_2w_avg AS DOUBLE) < 0 OR TRY_CAST(avg_charge_minutes AS DOUBLE) < 0) neg
         FROM raw_station_hourly_status''').iloc[0]
print('telemetry_status values:'); display(q('SELECT telemetry_status, COUNT(*) n FROM raw_station_hourly_status GROUP BY 1'))
dq('station_hourly_status', 'Duplicate station-hour rows', h.dup, 'Keep one')
dq('station_hourly_status', 'Test stations', h.test, 'Drop')
dq('station_hourly_status', 'Telemetry not ok', h.partial_rows, 'Keep, flag')
dq('station_hourly_status', 'Negative readings', h.neg, 'Set to NULL')

dq_log = save(pd.DataFrame(DQ), 'dq_log')
display(dq_log)
""")

code(r"""
# ---------- Clean dimension tables ----------
con.execute('''
CREATE OR REPLACE TABLE stations AS
SELECT station_id, city, zone, TRY_CAST(latitude AS DOUBLE) lat, TRY_CAST(longitude AS DOUBLE) lon,
       location_type, host_type, TRY_CAST(commissioned_date AS DATE) commissioned_date,
       TRY_CAST(decommissioned_date AS DATE) decommissioned_date, expansion_wave, charger_generation,
       TRY_CAST(slots_2w AS INT) slots_2w, TRY_CAST(slots_3w AS INT) slots_3w,
       TRY_CAST(inventory_target_2w AS INT) inv_target_2w, TRY_CAST(inventory_target_3w AS INT) inv_target_3w,
       TRY_CAST(monthly_rent_inr AS DOUBLE) rent, TRY_CAST(monthly_maintenance_inr AS DOUBLE) maint,
       TRY_CAST(grid_tariff_inr_kwh AS DOUBLE) grid_tariff, connectivity_tier, firmware_version,
       TRY_CAST(firmware_updated_date AS DATE) fw_date, TRY_CAST(competitor_within_1_5km_since AS DATE) competitor_since
FROM raw_stations WHERE station_id NOT LIKE 'STN-TST-%' ''')

con.execute('''
CREATE OR REPLACE TABLE riders AS
SELECT rider_id, NULLIF(partner_id,'') partner_id, COALESCE(NULLIF(partner_id,''),'RETAIL') partner_group,
       vehicle_class, vehicle_model, home_zone, TRY_CAST(signup_date AS DATE) signup_date, signup_channel, plan_type,
       COALESCE(NULLIF(declared_shift,''),'unknown') declared_shift, COALESCE(NULLIF(age_band,''),'unknown') age_band,
       TRY_CAST(kyc_verified AS BOOLEAN) kyc_verified,
       CASE WHEN lower(trim(home_city)) IN ('bengaluru','bangalore','blr')               THEN 'Bengaluru'
            WHEN lower(trim(home_city)) IN ('delhi ncr','delhi','new delhi','gurgaon','del') THEN 'Delhi NCR'
            WHEN lower(trim(home_city)) IN ('hyderabad','hyd')                          THEN 'Hyderabad'
            WHEN lower(trim(home_city)) IN ('pune','pun')                               THEN 'Pune'
            WHEN lower(trim(home_city)) IN ('mumbai','bombay','mum')                    THEN 'Mumbai'
            WHEN lower(trim(home_city)) IN ('jaipur','jai')                             THEN 'Jaipur' END home_city,
       home_city AS home_city_raw
FROM raw_riders''')
print('unmapped cities:', con.execute('SELECT COUNT(*) FROM riders WHERE home_city IS NULL').fetchone()[0])

con.execute('''
CREATE OR REPLACE TABLE batteries AS
WITH b AS (
  SELECT battery_id, pack_type, supplier, manufacturing_lot lot, TRY_CAST(manufacture_date AS DATE) mfg_date,
         TRY_CAST(commission_date AS DATE) commission_date, TRY_CAST(rated_capacity_kwh AS DOUBLE) capacity_kwh,
         TRY_CAST(purchase_cost_inr AS DOUBLE) cost_inr, TRY_CAST(initial_soh_pct AS DOUBLE) initial_soh, bms_firmware,
         TRY_CAST(retired_date AS DATE) retired_date, NULLIF(retirement_reason,'') retirement_reason,
         TRY_CAST(current_soh_pct AS DOUBLE) current_soh
  FROM raw_batteries),
l AS (SELECT lot, AVG(current_soh) lot_soh FROM b GROUP BY 1)
SELECT b.*, l.lot_soh, (l.lot_soh < 70)::INT AS bad_lot FROM b JOIN l USING (lot)''')

con.execute(f'''
CREATE OR REPLACE TABLE tickets AS
SELECT ticket_id, rider_id, NULLIF(station_id,'') station_id, NULLIF(battery_id,'') battery_id,
       TRY_CAST(created_ts AS TIMESTAMP) ts, channel, category,
       CASE WHEN category = 'other' AND regexp_matches(lower(rider_comment), 'vehicle performance|bike problem|scooter weak')
            THEN 'vehicle_performance' ELSE category END AS category_clean,
       regexp_replace(regexp_replace(regexp_replace(rider_comment, 'staion', 'station', 'g'), 'battry', 'battery', 'g'), 'rnage', 'range', 'g') comment_clean,
       priority, resolution_status, TRY_CAST(resolution_hours AS DOUBLE) resolution_hours, TRY_CAST(csat_score AS INT) csat
FROM raw_support_tickets
WHERE resolution_status <> 'duplicate'
  AND COALESCE(station_id,'') NOT LIKE 'STN-TST-%'
  AND TRY_CAST(created_ts AS TIMESTAMP) >= TIMESTAMP '{START}' AND TRY_CAST(created_ts AS TIMESTAMP) < TIMESTAMP '{END}' ''')

con.execute('''
CREATE OR REPLACE TABLE hourly AS
SELECT station_id, TRY_CAST(hour_start AS TIMESTAMP) hour_start,
  CASE WHEN TRY_CAST(charged_2w_avg AS DOUBLE) >= 0 THEN TRY_CAST(charged_2w_avg AS DOUBLE) END charged_2w_avg,
  TRY_CAST(charged_2w_min AS DOUBLE) charged_2w_min, TRY_CAST(charged_3w_min AS DOUBLE) charged_3w_min,
  TRY_CAST(packs_charging AS DOUBLE) packs_charging, TRY_CAST(packs_quarantined AS DOUBLE) packs_quarantined,
  TRY_CAST(chargers_online AS DOUBLE) chargers_online, TRY_CAST(ambient_temp_c AS DOUBLE) ambient_temp_c,
  TRY_CAST(cabinet_temp_c AS DOUBLE) cabinet_temp_c,
  CASE WHEN TRY_CAST(avg_charge_minutes AS DOUBLE) >= 0 THEN TRY_CAST(avg_charge_minutes AS DOUBLE) END avg_charge_minutes,
  TRY_CAST(outage_minutes AS DOUBLE) outage_minutes, TRY_CAST(grid_kwh AS DOUBLE) grid_kwh, telemetry_status
FROM raw_station_hourly_status
WHERE station_id NOT LIKE 'STN-TST-%'
QUALIFY ROW_NUMBER() OVER (PARTITION BY station_id, hour_start ORDER BY telemetry_status) = 1''')

con.execute('''
CREATE OR REPLACE TABLE city_days AS
SELECT city, TRY_CAST(date AS DATE) AS day, TRY_CAST(max_temp_c AS DOUBLE) max_temp_c, TRY_CAST(min_temp_c AS DOUBLE) min_temp_c,
       TRY_CAST(rainfall_mm AS DOUBLE) rainfall_mm, TRY_CAST(heat_alert AS BOOLEAN) heat_alert,
       TRY_CAST(flood_disruption AS BOOLEAN) flood_disruption, NULLIF(festival_or_event,'') festival,
       TRY_CAST(is_public_holiday AS BOOLEAN) holiday, TRY_CAST(grid_outage_hours AS DOUBLE) grid_outage_hours,
       TRY_CAST(competitor_promo_active AS BOOLEAN) competitor_promo, TRY_CAST(ecommerce_sale_event AS BOOLEAN) sale_event
FROM raw_city_daily_context''')

con.execute('''
CREATE OR REPLACE TABLE partners AS
SELECT partner_id, partner_name, partner_segment, vehicle_class, contract_type, TRY_CAST(contract_start_date AS DATE) contract_start,
       TRY_CAST(discount_pct AS DOUBLE) discount_pct, TRY_CAST(amendment_date AS DATE) amendment_date,
       TRY_CAST(discount_pct_after_amendment AS DOUBLE) discount_after, peak_surcharge_billable = 'Y' AS peak_surcharge_billable,
       TRY_CAST(payment_terms_days AS INT) payment_terms_days, cities_active
FROM raw_fleet_partners''')
for t in ['stations', 'riders', 'batteries', 'tickets', 'hourly', 'city_days', 'partners']:
    print(f"{t:10s} {con.execute(f'SELECT COUNT(*) FROM {t}').fetchone()[0]:>10,d}")
""")

# ---------------------------------------------------------------- 2. enriched swaps
md(r"""
## 2. One clean, enriched swap table

Each swap attempt is joined to its station, rider and the two batteries involved (the one returned and the one given out).

**Definitions used throughout**
- **Service failure** = any attempt that did not end in `swap_completed` (no charged battery, system error, abandoned queue, cancelled).
- **Long wait** = queue wait over 10 minutes.
- **Energy cost** = energy needed to recharge the returned pack × that station's grid tariff.
- **Margin per swap** = (revenue − energy cost − station rent − station maintenance) ÷ completed swaps.
  Rent and maintenance are counted for every month a station is open.
""")

code(r"""
con.execute(f'''
CREATE OR REPLACE TABLE swaps AS
WITH s AS (
  SELECT event_id, rider_id, station_id, TRY_CAST(event_ts AS TIMESTAMP) ts, event_type,
         TRY_CAST(attempt_seq AS INT) attempt_seq,
         CASE WHEN TRY_CAST(queue_wait_sec AS DOUBLE) >= 0 THEN TRY_CAST(queue_wait_sec AS DOUBLE) END queue_wait_sec,
         NULLIF(battery_in_id,'') battery_in_id, NULLIF(battery_out_id,'') battery_out_id,
         LEAST(TRY_CAST(soc_in_pct AS DOUBLE), 100) soc_in, LEAST(TRY_CAST(soh_in_pct AS DOUBLE), 100) soh_in,
         LEAST(TRY_CAST(soc_out_pct AS DOUBLE), 100) soc_out, LEAST(TRY_CAST(soh_out_pct AS DOUBLE), 100) soh_out,
         CASE WHEN TRY_CAST(km_since_last_swap AS DOUBLE) BETWEEN 0 AND {KM_MAX} THEN TRY_CAST(km_since_last_swap AS DOUBLE) END km,
         tariff_code, TRY_CAST(list_price_inr AS DOUBLE) list_price, TRY_CAST(discount_inr AS DOUBLE) discount,
         TRY_CAST(amount_charged_inr AS DOUBLE) amount, payment_mode,
         TRY_CAST(energy_to_recharge_kwh AS DOUBLE) energy_kwh, station_firmware, sync_mode
  FROM raw_swap_events),
d AS (
  SELECT * FROM s
  QUALIFY ROW_NUMBER() OVER (PARTITION BY event_id ORDER BY ts) = 1
      AND ROW_NUMBER() OVER (PARTITION BY rider_id, station_id, ts, event_type ORDER BY event_id) = 1
      AND ROW_NUMBER() OVER (PARTITION BY rider_id, COALESCE(battery_in_id, event_id),
                             COALESCE(battery_out_id, event_id), event_type ORDER BY ts, event_id) = 1)
SELECT d.*,
       date_trunc('month', d.ts)::DATE AS month, d.ts::DATE AS day, hour(d.ts) AS hr, dayofweek(d.ts) AS dow,
       st.city, st.zone, st.expansion_wave wave, st.charger_generation gen, st.location_type, st.connectivity_tier,
       st.grid_tariff, st.commissioned_date, st.competitor_since,
       r.vehicle_class, r.partner_group, r.plan_type, r.signup_date,
       bo.supplier out_supplier, bo.lot out_lot, bo.bad_lot out_bad_lot,
       bi.supplier in_supplier, bi.lot in_lot, bi.bad_lot in_bad_lot, bi.commission_date in_commission_date,
       (d.event_type = 'swap_completed')::INT completed,
       (d.event_type <> 'swap_completed')::INT service_fail,
       (d.event_type = 'failed_no_charged_battery')::INT fail_no_battery,
       (d.event_type = 'failed_system_error')::INT fail_system,
       (d.event_type IN ('abandoned_queue', 'cancelled_by_rider'))::INT fail_abandon,
       (d.queue_wait_sec > {LONG_WAIT_SEC})::INT long_wait,
       CASE WHEN d.event_type = 'swap_completed' THEN COALESCE(d.energy_kwh, 0) * st.grid_tariff ELSE 0 END energy_cost
FROM d
JOIN stations st USING (station_id)
JOIN riders r USING (rider_id)
LEFT JOIN batteries bo ON d.battery_out_id = bo.battery_id
LEFT JOIN batteries bi ON d.battery_in_id = bi.battery_id
WHERE d.ts >= TIMESTAMP '{START}' AND d.ts < TIMESTAMP '{END}' ''')

n_raw = con.execute('SELECT COUNT(*) FROM raw_swap_events').fetchone()[0]
n = con.execute('SELECT COUNT(*) FROM swaps').fetchone()[0]
print(f'swap events: {n_raw:,} raw -> {n:,} clean ({n_raw - n:,} removed)')
KEY['swap_rows_raw'], KEY['swap_rows_clean'] = n_raw, n

x = q('''SELECT COUNT(*) FILTER (WHERE ts::DATE < signup_date) before_signup,
                COUNT(*) FILTER (WHERE ts::DATE < commissioned_date) before_station_open FROM swaps''').iloc[0]
print('swaps before rider signup:', x.before_signup, '| swaps before station opened:', x.before_station_open)
dq('swap_events', 'Swap before rider signup date', x.before_signup, 'Flag only')
dq('swap_events', 'Swap before station commission date', x.before_station_open, 'Flag only')
save(pd.DataFrame(DQ), 'dq_log')

# free memory: raw swap table is no longer needed
con.execute('DROP TABLE raw_swap_events'); con.execute('DROP TABLE raw_station_hourly_status')
display(q('SELECT * FROM swaps LIMIT 3'))
""")

code(r"""
# ---------- Discover price changes and peak/off-peak tariff codes ----------
with section('Tariff discovery'):
    tariffs = save(q('''
      SELECT tariff_code, vehicle_class, MIN(ts)::DATE first_seen, MAX(ts)::DATE last_seen, COUNT(*) n,
             ROUND(AVG(list_price),1) avg_list, ROUND(AVG(discount),1) avg_discount, MIN(hr) min_hr, MAX(hr) max_hr,
             string_agg(DISTINCT city, ', ') cities
      FROM swaps WHERE completed = 1 GROUP BY 1, 2 ORDER BY 1, 2'''), 'tariff_codes')
    display(tariffs)
    price_m = save(q('''
      SELECT month, vehicle_class, tariff_code, COUNT(*) swaps, AVG(list_price) avg_list, AVG(discount) avg_discount,
             AVG(amount) avg_amount, mode(list_price) modal_list
      FROM swaps WHERE completed = 1 GROUP BY ALL ORDER BY ALL'''), 'price_by_month')

    # price change: first month the modal STD list price differs from Jan 2024
    std = price_m[price_m.tariff_code == 'STD'].copy()
    for vc, g in std.groupby('vehicle_class'):
        g = g.sort_values('month'); base = g.modal_list.iloc[0]
        ch = g[g.modal_list != base]
        if len(ch):
            EVENTS[f'Price change {vc}'] = str(pd.Timestamp(ch.month.iloc[0]).date())
            print(f'{vc} STD list price {base} -> {ch.modal_list.iloc[0]} from {pd.Timestamp(ch.month.iloc[0]).date()}')
    PEAK_CODES = [c for c in tariffs.tariff_code.unique() if 'PEAK' in c.upper() and 'OFF' not in c.upper()]
    OFF_CODES = [c for c in tariffs.tariff_code.unique() if 'OFF' in c.upper()]
    print('peak codes:', PEAK_CODES, '| off-peak codes:', OFF_CODES)
    if PEAK_CODES or OFF_CODES:
        EVENTS['Peak pricing pilot'] = str(tariffs[tariffs.tariff_code.isin(PEAK_CODES + OFF_CODES)].first_seen.min())

    hours = q('SELECT hr, COUNT(*) n FROM swaps GROUP BY 1 ORDER BY 1')
    if PEAK_CODES:
        ph = q(f"SELECT DISTINCT hr FROM swaps WHERE tariff_code IN ({','.join(repr(c) for c in PEAK_CODES)})")
        PEAK_HOURS = sorted(ph.hr.tolist())
    else:                                       # fall back: busiest hours (> 1.3x the average hour)
        PEAK_HOURS = sorted(hours[hours.n > hours.n.mean() * 1.3].hr.tolist())
    print('peak hours used:', PEAK_HOURS)
    KEY['peak_hours'] = PEAK_HOURS
    con.execute(f"CREATE OR REPLACE TABLE peak_hours AS SELECT UNNEST({PEAK_HOURS}) hr")
    print('events used on charts:', EVENTS)
""")

# ---------------------------------------------------------------- 3. trends
md(r"""
## 3. Q1 Trends: how did swaps, revenue, failures and margin move?
""")

code(r"""
with section('Q1 monthly trends'):
    monthly = save(q('''
    WITH m AS (
      SELECT month, COUNT(*) attempts, SUM(completed) swaps, SUM(amount) revenue, SUM(energy_cost) energy_cost,
             SUM(CASE WHEN completed = 1 THEN list_price END) list_revenue, SUM(CASE WHEN completed = 1 THEN discount END) discounts,
             AVG(service_fail) fail_rate, AVG(fail_no_battery) no_battery_rate, AVG(fail_system) system_fail_rate,
             AVG(fail_abandon) abandon_rate, AVG(long_wait) long_wait_rate, AVG(queue_wait_sec) / 60 avg_wait_min,
             quantile_cont(queue_wait_sec, 0.9) / 60 p90_wait_min, COUNT(DISTINCT rider_id) active_riders
      FROM swaps GROUP BY 1),
    cal AS (SELECT CAST(m AS DATE) AS month FROM generate_series(TIMESTAMP '2024-01-01', TIMESTAMP '2025-06-01', INTERVAL 1 MONTH) t(m)),
    opex AS (
      SELECT cal.month, COUNT(*) active_stations, SUM(rent) rent, SUM(maint) maint
      FROM cal JOIN stations st ON st.commissioned_date <= last_day(cal.month)
                                   AND (st.decommissioned_date IS NULL OR st.decommissioned_date >= cal.month)
      GROUP BY 1)
    SELECT m.*, o.active_stations, o.rent, o.maint,
           revenue / swaps rev_per_swap, list_revenue / swaps list_per_swap, discounts / swaps discount_per_swap,
           energy_cost / swaps energy_per_swap, (rent + maint) / swaps opex_per_swap,
           (revenue - energy_cost - rent - maint) / swaps margin_per_swap,
           (revenue - energy_cost - rent - maint) / revenue margin_pct,
           swaps / active_stations / day(last_day(month)) swaps_per_station_day
    FROM m JOIN opex o USING (month) ORDER BY month'''), 'q1_monthly')
    monthly['month'] = pd.to_datetime(monthly.month)
    display(monthly.round(3))

    f, l = monthly.iloc[:3], monthly.iloc[-3:]          # Q1 2024 vs Q2 2025
    for c in ['swaps', 'revenue', 'fail_rate', 'no_battery_rate', 'long_wait_rate', 'p90_wait_min', 'rev_per_swap',
              'energy_per_swap', 'opex_per_swap', 'margin_per_swap', 'margin_pct', 'swaps_per_station_day', 'active_stations']:
        KEY[f'q1_{c}_Q1-2024'] = float(f[c].mean()); KEY[f'q1_{c}_Q2-2025'] = float(l[c].mean())
    print(json.dumps({k: round(v, 3) for k, v in KEY.items() if k.startswith('q1_')}, indent=1))

    fig, ax = plt.subplots(2, 3, figsize=(17, 8.5)); ax = ax.ravel()
    ax[0].plot(monthly.month, monthly.swaps / 1e3, marker='o'); ax[0].set_title('Completed swaps per month (000s)')
    ax[1].plot(monthly.month, monthly.revenue / 1e6, marker='o', color=PALETTE[2]); ax[1].set_title('Revenue per month (₹ million)')
    ax[2].stackplot(monthly.month, monthly.no_battery_rate, monthly.system_fail_rate, monthly.abandon_rate,
                    labels=['No charged battery', 'System error', 'Abandoned / cancelled'], colors=[PALETTE[3], PALETTE[4], PALETTE[1]], alpha=.85)
    ax[2].yaxis.set_major_formatter(PCT); ax[2].set_title('Service-failure rate by type'); ax[2].legend(loc='upper left', fontsize=8)
    ax[3].plot(monthly.month, monthly.p90_wait_min, marker='o', color=PALETTE[1], label='90th percentile wait (min)')
    ax3b = ax[3].twinx(); ax3b.plot(monthly.month, monthly.long_wait_rate, color=PALETTE[3], ls=':', label='> 10 min share')
    ax3b.yaxis.set_major_formatter(PCT); ax[3].set_title('Queue wait'); ax[3].legend(loc='upper left', fontsize=8); ax3b.legend(loc='lower right', fontsize=8)
    ax[4].plot(monthly.month, monthly.rev_per_swap, marker='o', label='Revenue / swap')
    ax[4].plot(monthly.month, monthly.margin_per_swap, marker='o', label='Margin / swap', color=PALETTE[3])
    ax[4].set_title('Unit economics (₹ per swap)'); ax[4].legend(fontsize=8)
    ax[5].bar(monthly.month, monthly.active_stations, width=20, color='#d0d7de', label='Active stations')
    ax5b = ax[5].twinx(); ax5b.plot(monthly.month, monthly.swaps_per_station_day, marker='o', color=PALETTE[0], label='Swaps / station / day')
    ax[5].set_title('Network size vs utilisation'); ax[5].legend(loc='upper left', fontsize=8); ax5b.legend(loc='lower right', fontsize=8)
    for a_ in ax: mark_events(a_)
    fig.suptitle('VoltRelay, Jan 2024 – Jun 2025: volume grew while service and margin got worse', fontsize=14, fontweight='bold')
    savefig(fig, 'q1_trends_dashboard')
""")

# ---------------------------------------------------------------- 4. failures
md(r"""
## 4. Q2 Failures: where and when do failures and long waits happen?
""")

code(r"""
with section('Q2 failures by city / hour / vehicle'):
    city_m = save(q('SELECT city, month, COUNT(*) attempts, AVG(service_fail) fail_rate, AVG(fail_no_battery) no_battery_rate, AVG(long_wait) long_wait_rate FROM swaps GROUP BY ALL ORDER BY ALL'), 'q2_city_month')
    hm = city_m.pivot(index='city', columns='month', values='fail_rate')
    hm.columns = [pd.Timestamp(c).strftime('%b-%y') for c in hm.columns]
    fig, ax = plt.subplots(figsize=(15, 3.8))
    im = ax.imshow(hm.values, aspect='auto', cmap='Reds'); ax.set_yticks(range(len(hm.index)), hm.index)
    ax.set_xticks(range(len(hm.columns)), hm.columns, rotation=45); ax.grid(False)
    for i in range(hm.shape[0]):
        for j in range(hm.shape[1]):
            ax.text(j, i, f'{hm.values[i, j]:.0%}', ha='center', va='center', fontsize=7)
    ax.set_title('Service-failure rate by city and month'); plt.colorbar(im, format=PCT)
    savefig(fig, 'q2_city_month_heatmap')

    hr = save(q('''SELECT vehicle_class, hr, COUNT(*) attempts, AVG(service_fail) fail_rate, AVG(fail_no_battery) no_battery_rate,
                          AVG(long_wait) long_wait_rate, AVG(queue_wait_sec)/60 avg_wait_min
                   FROM swaps GROUP BY ALL ORDER BY ALL'''), 'q2_hour_vehicle')
    fig, ax = plt.subplots(1, 2, figsize=(15, 4.2))
    for vc, g in hr.groupby('vehicle_class'):
        ax[0].plot(g.hr, g.fail_rate, marker='o', label=f'{vc} failure rate')
        ax[0].plot(g.hr, g.long_wait_rate, ls=':', label=f'{vc} waits > 10 min')
    ax[0].yaxis.set_major_formatter(PCT); ax[0].set_xlabel('Hour of day'); ax[0].legend(fontsize=8); ax[0].set_title('Failures and long waits by hour')
    tot = hr.groupby('hr').attempts.sum()
    ax[1].bar(tot.index, tot.values / 1e3, color='#d0d7de'); ax[1].set_title('Swap attempts by hour (000s)'); ax[1].set_xlabel('Hour of day')
    for h_ in KEY['peak_hours']: ax[1].bar(h_, tot.get(h_, 0) / 1e3, color=PALETTE[1])
    savefig(fig, 'q2_hour_profile')

    vm = save(q('SELECT month, vehicle_class, AVG(service_fail) fail_rate, AVG(long_wait) long_wait_rate FROM swaps GROUP BY ALL ORDER BY ALL'), 'q2_vehicle_month')
    city_tot = save(q('''SELECT city, COUNT(*) attempts, AVG(service_fail) fail_rate, AVG(fail_no_battery) no_battery_rate,
                          AVG(long_wait) long_wait_rate, AVG(CASE WHEN month >= DATE '2025-01-01' THEN service_fail END) fail_rate_2025,
                          AVG(CASE WHEN month < DATE '2024-07-01' THEN service_fail END) fail_rate_h1_2024
                         FROM swaps GROUP BY 1 ORDER BY fail_rate DESC'''), 'q2_city_summary')
    display(city_tot)
    peak = q('''SELECT (hr IN (SELECT hr FROM peak_hours)) AS is_peak, AVG(service_fail) fail_rate, AVG(long_wait) long_wait_rate, COUNT(*) n FROM swaps GROUP BY 1''')
    display(peak)
    KEY['q2_fail_rate_peak'] = float(peak[peak.is_peak].fail_rate.iloc[0]); KEY['q2_fail_rate_offpeak'] = float(peak[~peak.is_peak].fail_rate.iloc[0])
""")

code(r"""
with section('Q2 failures vs charged inventory, weather and events'):
    inv = save(q('''
      SELECT CASE WHEN h.charged_2w_min = 0 THEN '0' WHEN h.charged_2w_min <= 2 THEN '1-2'
                  WHEN h.charged_2w_min <= 5 THEN '3-5' ELSE '6+' END charged_2w_min_bucket,
             COUNT(*) attempts, AVG(service_fail) fail_rate, AVG(fail_no_battery) no_battery_rate, AVG(long_wait) long_wait_rate
      FROM swaps s JOIN hourly h ON s.station_id = h.station_id AND date_trunc('hour', s.ts) = h.hour_start
      WHERE s.vehicle_class = '2W' GROUP BY 1 ORDER BY 1'''), 'q2_failure_vs_inventory')
    display(inv)

    wx = save(q('''
      WITH d AS (SELECT city, day, COUNT(*) attempts, AVG(service_fail) fail_rate, AVG(long_wait) long_wait_rate FROM swaps GROUP BY 1, 2)
      SELECT d.*, c.max_temp_c, c.rainfall_mm, c.heat_alert, c.flood_disruption, c.festival, c.holiday, c.grid_outage_hours,
             c.competitor_promo, c.sale_event FROM d JOIN city_days c USING (city, day)'''), 'q2_city_day_weather')
    rows = []
    for col, lab in [('heat_alert', 'Heat alert'), ('flood_disruption', 'Flood'), ('sale_event', 'E-commerce sale'),
                     ('competitor_promo', 'Competitor promo'), ('holiday', 'Public holiday')]:
        for v, g in wx.groupby(col):
            rows.append(dict(factor=lab, value=bool(v), days=len(g), fail_rate=g.fail_rate.mean(), long_wait_rate=g.long_wait_rate.mean(), attempts_per_day=g.attempts.mean()))
    wx['rain_bucket'] = pd.cut(wx.rainfall_mm, [-1, 0, 10, 50, 1e4], labels=['none', '0-10 mm', '10-50 mm', '50+ mm'])
    wx['temp_bucket'] = pd.cut(wx.max_temp_c, [-1, 30, 35, 40, 60], labels=['<30C', '30-35C', '35-40C', '40C+'])
    for col in ['rain_bucket', 'temp_bucket']:
        for v, g in wx.groupby(col):
            rows.append(dict(factor=col, value=str(v), days=len(g), fail_rate=g.fail_rate.mean(), long_wait_rate=g.long_wait_rate.mean(), attempts_per_day=g.attempts.mean()))
    wx_sum = save(pd.DataFrame(rows), 'q2_external_factors'); display(wx_sum.round(3))

    fig, ax = plt.subplots(1, 2, figsize=(14, 4))
    ax[0].bar(inv.charged_2w_min_bucket, inv.fail_rate, color=PALETTE[3]); ax[0].yaxis.set_major_formatter(PCT)
    ax[0].set_title('2W failure rate vs lowest charged-pack count that hour'); ax[0].set_xlabel('Charged 2W packs in cabinet (hourly minimum)')
    tb = wx_sum[wx_sum.factor == 'temp_bucket']
    ax[1].bar(tb.value, tb.fail_rate, color=PALETTE[1]); ax[1].yaxis.set_major_formatter(PCT); ax[1].set_title('Failure rate by daily max temperature')
    savefig(fig, 'q2_inventory_and_heat')
""")

# ---------------------------------------------------------------- 5. stations
md(r"""
## 5. Q3 Stations: how do location, charger generation and expansion wave affect performance?

Gen1 chargers exist only at Launch stations, so Gen1 is compared with Gen2 *inside the Launch wave* and Gen2 with Gen3
*inside Wave 1*. Otherwise the wave effect and the charger effect would be mixed together.
""")

code(r"""
with section('Q3 station performance'):
    con.execute(f'''
    CREATE OR REPLACE TABLE station_perf AS
    WITH s AS (
      SELECT station_id, COUNT(*) attempts, SUM(completed) swaps, COUNT(DISTINCT day) AS days, SUM(amount) revenue,
             SUM(energy_cost) energy_cost, AVG(service_fail) fail_rate, AVG(fail_no_battery) no_battery_rate,
             AVG(fail_system) system_fail_rate, AVG(long_wait) long_wait_rate, AVG(queue_wait_sec)/60 avg_wait_min,
             AVG(CASE WHEN sync_mode = 'offline_batch' THEN 1.0 ELSE 0 END) offline_share
      FROM swaps WHERE month >= DATE '2025-04-01' GROUP BY 1),
    h AS (
      SELECT station_id, AVG((charged_2w_min = 0)::INT) stockout_hour_share, AVG(avg_charge_minutes) avg_charge_min,
             AVG(cabinet_temp_c) cabinet_temp, AVG(packs_quarantined) quarantined, SUM(outage_minutes)/60 outage_hours,
             AVG(chargers_online) chargers_online
      FROM hourly WHERE hour_start >= TIMESTAMP '2025-04-01' GROUP BY 1)
    SELECT st.station_id, st.city, st.zone, st.expansion_wave wave, st.charger_generation gen, st.location_type, st.host_type,
           st.connectivity_tier, st.slots_2w, st.slots_3w, st.inv_target_2w, st.rent, st.maint, st.grid_tariff,
           st.competitor_since IS NOT NULL AS competitor_nearby, s.* EXCLUDE (station_id), h.* EXCLUDE (station_id),
           s.swaps / s.days swaps_per_day, s.swaps / s.days / NULLIF(st.slots_2w + st.slots_3w, 0) swaps_per_slot_day,
           (s.revenue - s.energy_cost) / s.days * 30.4 - st.rent - st.maint monthly_profit
    FROM stations st JOIN s USING (station_id) LEFT JOIN h USING (station_id)''')
    sp = save(q('SELECT * FROM station_perf'), 'q3_station_perf_Q2_2025')

    agg = dict(stations=('station_id', 'count'), swaps_per_day=('swaps_per_day', 'mean'), fail_rate=('fail_rate', 'mean'),
               no_battery_rate=('no_battery_rate', 'mean'), long_wait_rate=('long_wait_rate', 'mean'),
               stockout_hour_share=('stockout_hour_share', 'mean'), avg_charge_min=('avg_charge_min', 'mean'),
               monthly_profit=('monthly_profit', 'mean'), loss_making=('monthly_profit', lambda x: (x < 0).sum()))
    by_wg = save(sp.groupby(['wave', 'gen']).agg(**agg).reset_index(), 'q3_wave_x_gen'); display(by_wg.round(3))
    by_loc = save(sp.groupby('location_type').agg(**agg).reset_index().sort_values('fail_rate'), 'q3_location_type'); display(by_loc.round(3))
    by_conn = save(sp.groupby('connectivity_tier').agg(**agg, system_fail_rate=('system_fail_rate', 'mean'), offline_share=('offline_share', 'mean')).reset_index(), 'q3_connectivity'); display(by_conn.round(3))
    by_city = save(sp.groupby('city').agg(**agg).reset_index(), 'q3_city'); display(by_city.round(3))
    KEY['q3_loss_making_stations_Q2_2025'] = int((sp.monthly_profit < 0).sum()); KEY['q3_stations_Q2_2025'] = int(len(sp))
    KEY['q3_loss_by_wave'] = sp[sp.monthly_profit < 0].wave.value_counts().to_dict()

    fig, ax = plt.subplots(1, 3, figsize=(17, 4.3))
    lab = by_wg.wave.str.replace('_', ' ') + '\n' + by_wg.gen
    ax[0].bar(lab, by_wg.fail_rate, color=PALETTE[3]); ax[0].yaxis.set_major_formatter(PCT); ax[0].set_title('Failure rate (Apr–Jun 2025)')
    ax[1].bar(lab, by_wg.stockout_hour_share, color=PALETTE[1]); ax[1].yaxis.set_major_formatter(PCT); ax[1].set_title('Hours with zero charged 2W packs')
    ax[2].bar(lab, by_wg.monthly_profit / 1e3, color=[PALETTE[2] if v >= 0 else PALETTE[3] for v in by_wg.monthly_profit]); ax[2].set_title('Avg monthly station profit (₹000)')
    for a_ in ax: a_.tick_params(axis='x', labelsize=8)
    savefig(fig, 'q3_wave_gen')

    fig, ax = plt.subplots(figsize=(8, 5))
    for i, (w, g) in enumerate(sp.groupby('wave')):
        ax.scatter(g.swaps_per_day, g.monthly_profit / 1e3, label=w, alpha=.75, color=PALETTE[i])
    ax.axhline(0, color='k', lw=.8); ax.set_xlabel('Completed swaps per day'); ax.set_ylabel('Monthly profit (₹000)')
    ax.set_title('Station profit vs utilisation (Apr–Jun 2025)'); ax.legend()
    savefig(fig, 'q3_station_profit_scatter')
""")

code(r"""
with section('Q3 charger generation, heat and charging speed'):
    gh = save(q('''
      SELECT st.charger_generation gen, date_trunc('month', h.hour_start)::DATE AS month,
             AVG(h.avg_charge_minutes) avg_charge_min, AVG(h.cabinet_temp_c) cabinet_temp,
             AVG((h.charged_2w_min = 0)::INT) stockout_share, AVG(h.chargers_online) chargers_online,
             AVG(h.packs_quarantined) quarantined, AVG(h.outage_minutes) outage_min
      FROM hourly h JOIN stations st USING (station_id) GROUP BY ALL ORDER BY ALL'''), 'q3_gen_month_hourly')
    ct = save(q('''
      SELECT st.charger_generation gen, FLOOR(h.cabinet_temp_c / 5) * 5 AS cabinet_temp_bin, COUNT(*) AS hours,
             AVG(h.avg_charge_minutes) avg_charge_min, AVG((h.charged_2w_min = 0)::INT) stockout_share
      FROM hourly h JOIN stations st USING (station_id) WHERE h.cabinet_temp_c IS NOT NULL GROUP BY ALL HAVING COUNT(*) > 200 ORDER BY ALL'''), 'q3_charge_time_vs_temp')
    gh['month'] = pd.to_datetime(gh.month)
    fig, ax = plt.subplots(1, 3, figsize=(17, 4.2))
    for g_, d_ in gh.groupby('gen'):
        ax[0].plot(d_.month, d_.avg_charge_min, marker='o', label=g_)
        ax[1].plot(d_.month, d_.stockout_share, marker='o', label=g_)
    for g_, d_ in ct.groupby('gen'):
        ax[2].plot(d_.cabinet_temp_bin, d_.avg_charge_min, marker='o', label=g_)
    ax[0].set_title('Average charge time (min) by charger gen'); ax[1].set_title('Share of hours with zero charged 2W packs'); ax[1].yaxis.set_major_formatter(PCT)
    ax[2].set_title('Charge time vs cabinet temperature'); ax[2].set_xlabel('Cabinet temp (°C)')
    for a_ in ax: a_.legend(fontsize=8)
    mark_events(ax[0]); mark_events(ax[1])
    savefig(fig, 'q3_charger_gen_heat')
""")

code(r"""
with section('Q3 cannibalisation, competitor and firmware'):
    # Did new stations steal volume from Launch stations in the same zone?
    cz = save(q('''
      WITH z AS (SELECT DISTINCT zone FROM stations WHERE expansion_wave <> 'Launch'),
      l AS (
        SELECT s.station_id, (z.zone IS NOT NULL) AS zone_got_new_station,
               SUM(CASE WHEN s.month BETWEEN DATE '2024-04-01' AND DATE '2024-07-01' THEN completed END) / 122.0 per_day_before,
               SUM(CASE WHEN s.month BETWEEN DATE '2025-03-01' AND DATE '2025-06-01' THEN completed END) / 122.0 per_day_after
        FROM swaps s JOIN stations st USING (station_id) LEFT JOIN z ON st.zone = z.zone
        WHERE st.expansion_wave = 'Launch' GROUP BY 1, 2)
      SELECT zone_got_new_station, COUNT(*) launch_stations, AVG(per_day_before) per_day_before, AVG(per_day_after) per_day_after,
             AVG(per_day_after) / AVG(per_day_before) - 1 growth FROM l GROUP BY 1'''), 'q3_cannibalisation')
    display(cz)

    comp = save(q('''
      SELECT month, competitor_since IS NOT NULL AS competitor_nearby, COUNT(DISTINCT station_id) stations,
             SUM(completed) / COUNT(DISTINCT station_id) swaps_per_station, AVG(service_fail) fail_rate
      FROM swaps WHERE city = 'Bengaluru' AND wave = 'Launch' GROUP BY ALL ORDER BY ALL'''), 'q3_competitor_blr')
    comp['month'] = pd.to_datetime(comp.month)
    base = comp[comp.month == '2024-12-01'].set_index('competitor_nearby').swaps_per_station
    comp['index_dec24'] = comp.swaps_per_station / comp.competitor_nearby.map(base) * 100

    fw = save(q('''
      SELECT date_trunc('week', ts)::DATE AS week, station_firmware, COUNT(*) attempts, AVG(fail_system) system_fail_rate
      FROM swaps GROUP BY ALL ORDER BY ALL'''), 'q3_firmware_week')
    fw['week'] = pd.to_datetime(fw.week)
    fig, ax = plt.subplots(1, 2, figsize=(15, 4.2))
    for c_, d_ in comp.groupby('competitor_nearby'):
        ax[0].plot(d_.month, d_.index_dec24, marker='o', label='Competitor within 1.5 km' if c_ else 'No competitor')
    ax[0].axvline(pd.Timestamp('2025-01-15'), color='k', ls='--', lw=.8); ax[0].set_title('Bengaluru Launch stations: swaps per station (Dec-24 = 100)'); ax[0].legend(fontsize=8)
    for v_, d_ in fw.groupby('station_firmware'):
        ax[1].plot(d_.week, d_.system_fail_rate, label=v_)
    ax[1].yaxis.set_major_formatter(PCT); ax[1].set_title('System-error rate by station firmware (weekly)'); ax[1].legend(fontsize=8); mark_events(ax[1], ['Firmware update'])
    savefig(fig, 'q3_competitor_firmware')
""")

# ---------------------------------------------------------------- 6. batteries
md(r"""
## 6. Q4 Batteries: are specific suppliers, lots or ageing packs causing problems?
""")

code(r"""
with section('Q4 battery lots and suppliers'):
    lots = save(q('''
      SELECT supplier, lot, pack_type, COUNT(*) packs, MIN(mfg_date) first_mfg, MAX(mfg_date) last_mfg,
             MIN(commission_date) first_commission, AVG(current_soh) avg_current_soh, AVG(initial_soh) avg_initial_soh,
             COUNT(retired_date) retired, MAX(bad_lot) bad_lot
      FROM batteries GROUP BY ALL ORDER BY avg_current_soh'''), 'q4_battery_lots')
    display(lots.head(12))
    sup = save(q('''SELECT supplier, COUNT(*) packs, AVG(current_soh) avg_soh, SUM(bad_lot) packs_in_bad_lots, COUNT(retired_date) retired,
                         AVG(cost_inr / capacity_kwh) cost_per_kwh FROM batteries GROUP BY 1'''), 'q4_supplier_summary'); display(sup)
    KEY['q4_bad_lots'] = lots[lots.bad_lot == 1].lot.unique().tolist()
    KEY['q4_bad_lot_packs'] = int(lots[lots.bad_lot == 1].packs.sum())

    # health and range delivered by the pack the rider *returned* (battery_in), by age
    age = save(q('''
      SELECT CASE WHEN in_bad_lot = 1 THEN in_supplier || ' (bad lots)' ELSE in_supplier END grp, vehicle_class,
             date_diff('month', in_commission_date, ts::DATE) age_months, COUNT(*) swaps,
             AVG(soh_in) avg_soh, AVG(km) avg_km, AVG(energy_kwh) avg_kwh
      FROM swaps WHERE completed = 1 AND in_supplier IS NOT NULL GROUP BY ALL HAVING COUNT(*) > 300 ORDER BY ALL'''), 'q4_soh_km_by_age')
    rng = save(q('''
      SELECT CASE WHEN in_bad_lot = 1 THEN in_supplier || ' (bad lots)' ELSE in_supplier END grp, vehicle_class,
             COUNT(*) swaps, AVG(soh_in) avg_soh, AVG(km) avg_km, quantile_cont(km, 0.1) p10_km, AVG(soc_in) avg_soc_in
      FROM swaps WHERE completed = 1 AND in_supplier IS NOT NULL AND month >= DATE '2025-01-01' GROUP BY ALL ORDER BY ALL'''), 'q4_range_by_group_2025')
    display(rng)

    # complaints per 1,000 swaps that handed out a pack from that group
    tk = save(q('''
      WITH issued AS (
        SELECT CASE WHEN out_bad_lot = 1 THEN out_supplier || ' (bad lots)' ELSE out_supplier END grp, COUNT(*) swaps_issued
        FROM swaps WHERE completed = 1 AND out_supplier IS NOT NULL GROUP BY 1),
      tt AS (
        SELECT CASE WHEN b.bad_lot = 1 THEN b.supplier || ' (bad lots)' ELSE b.supplier END grp, tk.category_clean, COUNT(*) tickets
        FROM tickets tk JOIN batteries b ON tk.battery_id = b.battery_id GROUP BY ALL)
      SELECT tt.grp, tt.category_clean, tt.tickets, i.swaps_issued, tt.tickets * 1000.0 / i.swaps_issued per_1000_swaps
      FROM tt JOIN issued i ON tt.grp = i.grp ORDER BY ALL'''), 'q4_tickets_per_1000_by_battery_group')
    tkp = tk.pivot(index='grp', columns='category_clean', values='per_1000_swaps').round(2); display(tkp)

    exp = save(q('''SELECT month, AVG(out_bad_lot) share_swaps_bad_lot_pack FROM swaps WHERE completed = 1 GROUP BY 1 ORDER BY 1'''), 'q4_bad_lot_exposure_month')
    exp['month'] = pd.to_datetime(exp.month)
    tmon = q('''SELECT date_trunc('month', ts)::DATE AS month, category_clean, COUNT(*) n FROM tickets GROUP BY ALL ORDER BY ALL''')
    tmon['month'] = pd.to_datetime(tmon.month); tpiv = tmon.pivot(index='month', columns='category_clean', values='n').fillna(0)
    save(tpiv.reset_index(), 'q4_tickets_by_category_month')

    fig, ax = plt.subplots(1, 3, figsize=(18, 4.5))
    for g_, d_ in age[age.vehicle_class == '2W'].groupby('grp'):
        ax[0].plot(d_.age_months, d_.avg_soh, marker='o', label=g_)
    ax[0].set_title('2W pack health (SoH %) by age'); ax[0].set_xlabel('Months since commissioning'); ax[0].legend(fontsize=8)
    r2 = rng[rng.vehicle_class == '2W']
    ax[1].bar(r2.grp, r2.avg_km, color=[PALETTE[3] if 'bad' in g else PALETTE[0] for g in r2.grp]); ax[1].set_title('2W km per charge, 2025'); ax[1].tick_params(axis='x', labelsize=8)
    ax[2].plot(exp.month, exp.share_swaps_bad_lot_pack, color=PALETTE[3], marker='o', label='Swaps handing out a bad-lot pack')
    ax[2].yaxis.set_major_formatter(PCT); ax2b = ax[2].twinx()
    for c_ in [c for c in ['low_range', 'vehicle_performance'] if c in tpiv.columns]:
        ax2b.plot(tpiv.index, tpiv[c_], ls=':', label=f'{c_} tickets')
    ax[2].set_title('Bad-lot exposure vs range complaints'); ax[2].legend(loc='upper left', fontsize=8); ax2b.legend(loc='center left', fontsize=8)
    mark_events(ax[2], ['Kyron supplier'])
    savefig(fig, 'q4_battery_quality')
""")

# ---------------------------------------------------------------- 7. pricing & fleets
md(r"""
## 7. Q5 Pricing and fleets: how did price changes, peak pricing and fleet discounts affect margin and swap timing?

**Margin bridge.** Margin per swap = list price − discount − energy − station opex (+ a small rounding / surcharge term).
Comparing Q1 2024 with Q2 2025 splits the margin drop exactly into these pieces.
""")

code(r"""
with section('Q5 margin bridge'):
    P0, P1 = ("DATE '2024-01-01'", "DATE '2024-03-01'"), ("DATE '2025-04-01'", "DATE '2025-06-01'")
    m = monthly.copy(); m['month'] = pd.to_datetime(m.month)
    def period(a, b):
        g = m[(m.month >= a) & (m.month <= b)]; s = g.swaps.sum()
        return dict(list=g.list_revenue.sum() / s, discount=g.discounts.sum() / s, energy=g.energy_cost.sum() / s,
                    opex=(g.rent + g.maint).sum() / s, revenue=g.revenue.sum() / s,
                    margin=(g.revenue - g.energy_cost - g.rent - g.maint).sum() / s)
    a0, a1 = period('2024-01-01', '2024-03-01'), period('2025-04-01', '2025-06-01')
    for d_ in (a0, a1): d_['other'] = d_['revenue'] - (d_['list'] - d_['discount'])
    steps = [('Margin Q1-24', a0['margin']), ('List price', a1['list'] - a0['list']), ('Discounts', -(a1['discount'] - a0['discount'])),
             ('Energy cost', -(a1['energy'] - a0['energy'])), ('Station rent+maint', -(a1['opex'] - a0['opex'])),
             ('Surcharge/other', a1['other'] - a0['other']), ('Margin Q2-25', a1['margin'])]
    bridge = save(pd.DataFrame(steps, columns=['step', 'inr_per_swap']), 'q5_margin_bridge'); display(bridge.round(2))
    KEY['q5_bridge'] = {k: round(v, 2) for k, v in steps}

    fig, ax = plt.subplots(figsize=(10, 4.5)); cum = 0
    for i, (k, v) in enumerate(steps):
        if i in (0, len(steps) - 1):
            ax.bar(k, v, color=PALETTE[0]); cum = v if i == 0 else cum
        else:
            ax.bar(k, v, bottom=cum if v >= 0 else cum + v, color=PALETTE[2] if v >= 0 else PALETTE[3]); cum += v
        ax.text(i, (v if i in (0, len(steps) - 1) else cum) + .3, f'{v:+.1f}' if 0 < i < len(steps) - 1 else f'₹{v:.1f}', ha='center', fontsize=9)
    ax.set_title('What moved margin per swap (₹), Q1 2024 → Q2 2025'); ax.tick_params(axis='x', labelsize=8)
    savefig(fig, 'q5_margin_bridge')

    # who is behind the discount change?
    disc = save(q(f'''
      WITH p AS (SELECT *, CASE WHEN month BETWEEN {P0[0]} AND {P0[1]} THEN 'Q1-24' WHEN month BETWEEN {P1[0]} AND {P1[1]} THEN 'Q2-25' END AS period_label
                 FROM swaps WHERE completed = 1)
      SELECT period_label, partner_group, COUNT(*) swaps, SUM(discount) discount, AVG(discount) discount_per_swap,
             SUM(discount) / SUM(list_price) discount_rate, COUNT(*) * 1.0 / SUM(COUNT(*)) OVER (PARTITION BY period_label) swap_share,
             SUM(discount) / SUM(COUNT(*)) OVER (PARTITION BY period_label) discount_contribution_per_swap
      FROM p WHERE period_label IS NOT NULL GROUP BY ALL ORDER BY ALL'''), 'q5_discount_by_partner')
    display(disc.round(3))
""")

code(r"""
with section('Q5 fleet partners and ZipDrop amendment'):
    pm = save(q('''
      SELECT s.month, s.partner_group, COALESCE(p.partner_name, 'Retail') partner_name, COUNT(*) attempts, SUM(completed) swaps,
             SUM(amount) revenue, SUM(CASE WHEN completed = 1 THEN discount END) discount,
             SUM(CASE WHEN completed = 1 THEN discount END) / SUM(CASE WHEN completed = 1 THEN list_price END) discount_rate,
             (SUM(amount) - SUM(energy_cost)) / SUM(completed) contribution_per_swap,
             AVG((hr IN (SELECT hr FROM peak_hours))::INT) peak_share, AVG(service_fail) fail_rate
      FROM swaps s LEFT JOIN partners p ON s.partner_group = p.partner_id GROUP BY ALL ORDER BY ALL'''), 'q5_partner_month')
    pm['month'] = pd.to_datetime(pm.month)
    big = pm.groupby('partner_name').swaps.sum().sort_values(ascending=False); print(big.head(6))
    KEY['q5_biggest_partner'] = big.drop('Retail', errors='ignore').index[0]

    zd = save(q('''
      SELECT CASE WHEN ts < TIMESTAMP '2024-11-01' THEN 'before (Jul-Oct 24)' ELSE 'after (Nov 24-Feb 25)' END AS period_label,
             COUNT(*) attempts, SUM(completed) / 4.0 swaps_per_month, AVG(CASE WHEN completed = 1 THEN discount END) discount_per_swap,
             SUM(CASE WHEN completed = 1 THEN discount END) / SUM(CASE WHEN completed = 1 THEN list_price END) discount_rate,
             (SUM(amount) - SUM(energy_cost)) / SUM(completed) contribution_per_swap,
             AVG((hr IN (SELECT hr FROM peak_hours))::INT) peak_share, AVG(service_fail) fail_rate
      FROM swaps WHERE partner_group = 'FP-03' AND ts >= TIMESTAMP '2024-07-01' AND ts < TIMESTAMP '2025-03-01' GROUP BY 1'''), 'q5_zipdrop_before_after')
    display(zd)
    lost = q('''SELECT SUM(discount - 0.12 * list_price) extra_discount, COUNT(*) swaps FROM swaps
                WHERE partner_group = 'FP-03' AND completed = 1 AND ts >= TIMESTAMP '2024-11-01' ''').iloc[0]
    KEY['q5_zipdrop_extra_discount_inr'] = float(lost.extra_discount); KEY['q5_zipdrop_swaps_after'] = int(lost.swaps)
    print(f"Extra discount given to ZipDrop since Nov-24 vs the old 12% deal: ₹{lost.extra_discount:,.0f} on {lost.swaps:,} swaps")

    # Who uses peak hours, and does the network fail more where peak demand concentrates?
    zshare = save(q('''SELECT month, AVG((partner_group = 'FP-03')::INT) FILTER (WHERE hr IN (SELECT hr FROM peak_hours)) zipdrop_share_of_peak,
                               AVG((partner_group = 'FP-03')::INT) zipdrop_share_all FROM swaps GROUP BY 1 ORDER BY 1'''), 'q5_zipdrop_peak_share')
    zshare['month'] = pd.to_datetime(zshare.month)
    top = pm[pm.partner_name.isin(big.index[:6])]
    fig, ax = plt.subplots(1, 3, figsize=(18, 4.3))
    for n_, d_ in top.groupby('partner_name'):
        ax[0].plot(d_.month, d_.swaps / 1e3, marker='.', label=n_)
        ax[1].plot(d_.month, d_.contribution_per_swap, marker='.', label=n_)
    ax[0].set_title('Swaps per month by partner (000s)'); ax[1].set_title('Contribution per swap (₹, after energy)')
    ax[2].plot(zshare.month, zshare.zipdrop_share_of_peak, label='ZipDrop share of peak-hour swaps'); ax[2].plot(zshare.month, zshare.zipdrop_share_all, label='ZipDrop share of all swaps')
    ax[2].yaxis.set_major_formatter(PCT); ax[2].set_title('ZipDrop share of demand')
    for a_ in ax: a_.legend(fontsize=7); mark_events(a_, ['ZipDrop 28% deal'])
    savefig(fig, 'q5_partners')
""")

code(r"""
with section('Q5 peak / off-peak pricing pilot'):
    if not (PEAK_CODES or OFF_CODES):
        print('No peak/off-peak tariff codes found in tariff_code; see tariff_codes.csv')
    else:
        codes = ','.join(repr(c) for c in PEAK_CODES + OFF_CODES)
        pilot = q(f"SELECT DISTINCT city FROM swaps WHERE tariff_code IN ({codes})").city.tolist()
        p_start = pd.Timestamp(EVENTS['Peak pricing pilot'])
        print('pilot cities:', pilot, '| start:', p_start.date())
        KEY['q5_pilot_cities'] = pilot
        pp = save(q(f'''
          SELECT city IN ({','.join(repr(c) for c in pilot)}) AS pilot_city,
                 CASE WHEN ts < TIMESTAMP '{p_start}' THEN 'before' ELSE 'during' END AS period_label,
                 AVG((hr IN (SELECT hr FROM peak_hours))::INT) peak_share, AVG(service_fail) fail_rate,
                 AVG(service_fail) FILTER (WHERE hr IN (SELECT hr FROM peak_hours)) peak_fail_rate,
                 AVG(long_wait) FILTER (WHERE hr IN (SELECT hr FROM peak_hours)) peak_long_wait, AVG(amount) FILTER (WHERE completed = 1) avg_price
          FROM swaps WHERE partner_group = 'RETAIL'
            AND ts >= TIMESTAMP '{p_start - pd.DateOffset(months=3)}' AND ts < TIMESTAMP '{p_start + pd.DateOffset(months=3)}'
          GROUP BY ALL ORDER BY ALL'''), 'q5_peak_pilot_did')
        display(pp)
        ph = save(q(f'''
          SELECT city IN ({','.join(repr(c) for c in pilot)}) AS pilot_city, CASE WHEN ts < TIMESTAMP '{p_start}' THEN 'before' ELSE 'during' END AS period_label,
                 hr, COUNT(*) n FROM swaps WHERE partner_group = 'RETAIL'
            AND ts >= TIMESTAMP '{p_start - pd.DateOffset(months=3)}' AND ts < TIMESTAMP '{p_start + pd.DateOffset(months=3)}' GROUP BY ALL ORDER BY ALL'''), 'q5_peak_pilot_hours')
        ph['share'] = ph.n / ph.groupby(['pilot_city', 'period_label']).n.transform('sum')
        fig, ax = plt.subplots(1, 2, figsize=(15, 4), sharey=True)
        for i, (pc, d_) in enumerate(ph.groupby('pilot_city')):
            for per, e in d_.groupby('period_label'):
                ax[i].plot(e.hr, e.share, marker='.', label=per)
            ax[i].set_title(('Pilot' if pc else 'Non-pilot') + ' cities: retail swaps by hour'); ax[i].yaxis.set_major_formatter(PCT); ax[i].legend()
        savefig(fig, 'q5_peak_pilot')
""")

# ---------------------------------------------------------------- 8. churn
md(r"""
## 8. Q6 Churn: why do new riders stop using VoltRelay?

**New rider** = signed up between Jan 2024 and Mar 2025, so every rider has a full 90 days of data.
**Churned** = no completed swap in days 61–90 after signup.
**First-month experience** = what happened in the rider's first 30 days (failures, waits, bad-lot packs, tickets, home station).

Factors are ranked by **excess churners**: riders exposed × (churn rate if exposed − churn rate if not exposed).
A logistic regression then checks each factor while holding the others constant, which separates primary from secondary causes.
""")

code(r"""
with section('Q6 churn features'):
    con.execute(f'''
    CREATE OR REPLACE TABLE new_riders AS
    WITH nr AS (SELECT * FROM riders WHERE signup_date BETWEEN DATE '2024-01-01' AND DATE '2025-03-31'),
    s AS (SELECT sw.* EXCLUDE (signup_date), date_diff('day', nr.signup_date, sw.ts::DATE) AS d FROM swaps sw JOIN nr USING (rider_id)),
    f AS (
      SELECT rider_id,
        COUNT(*) FILTER (WHERE d BETWEEN 0 AND 29) attempts_30,
        SUM(completed) FILTER (WHERE d BETWEEN 0 AND 29) swaps_30,
        SUM(service_fail) FILTER (WHERE d BETWEEN 0 AND 29) fails_30,
        SUM(fail_no_battery) FILTER (WHERE d BETWEEN 0 AND 29) no_battery_30,
        SUM(fail_system) FILTER (WHERE d BETWEEN 0 AND 29) system_fail_30,
        SUM(long_wait) FILTER (WHERE d BETWEEN 0 AND 29) long_waits_30,
        AVG(queue_wait_sec) FILTER (WHERE d BETWEEN 0 AND 29) / 60 avg_wait_min_30,
        SUM(out_bad_lot) FILTER (WHERE d BETWEEN 0 AND 29 AND completed = 1) bad_lot_packs_30,
        AVG(km) FILTER (WHERE d BETWEEN 0 AND 29) avg_km_30,
        AVG(CASE WHEN hr IN (SELECT hr FROM peak_hours) THEN 1.0 ELSE 0 END) FILTER (WHERE d BETWEEN 0 AND 29) peak_share_30,
        SUM(completed) FILTER (WHERE d BETWEEN 60 AND 89) swaps_61_90,
        mode(station_id) FILTER (WHERE d BETWEEN 0 AND 29) home_station
      FROM s GROUP BY 1),
    t AS (
      SELECT tk.rider_id, COUNT(*) tickets_30,
             COUNT(*) FILTER (WHERE category_clean IN ('no_battery_available', 'long_queue')) availability_tickets_30,
             COUNT(*) FILTER (WHERE category_clean IN ('low_range', 'vehicle_performance')) range_tickets_30,
             COUNT(*) FILTER (WHERE category_clean = 'billing_dispute') billing_tickets_30,
             COUNT(*) FILTER (WHERE category_clean = 'app_issue') app_tickets_30,
             COUNT(*) FILTER (WHERE resolution_status = 'unresolved') unresolved_30
      FROM tickets tk JOIN nr ON tk.rider_id = nr.rider_id WHERE date_diff('day', nr.signup_date, tk.ts::DATE) BETWEEN 0 AND 29 GROUP BY 1)
    SELECT nr.rider_id, nr.signup_date, date_trunc('month', nr.signup_date)::DATE AS cohort, nr.partner_group, nr.vehicle_class,
           nr.plan_type, nr.signup_channel, nr.home_city, nr.declared_shift, nr.age_band, nr.kyc_verified,
           COALESCE(f.attempts_30, 0) attempts_30, COALESCE(f.swaps_30, 0) swaps_30, COALESCE(f.fails_30, 0) fails_30,
           COALESCE(f.no_battery_30, 0) no_battery_30, COALESCE(f.system_fail_30, 0) system_fail_30,
           COALESCE(f.long_waits_30, 0) long_waits_30, f.avg_wait_min_30, COALESCE(f.bad_lot_packs_30, 0) bad_lot_packs_30,
           f.avg_km_30, f.peak_share_30, COALESCE(t.tickets_30, 0) tickets_30, COALESCE(t.availability_tickets_30, 0) availability_tickets_30,
           COALESCE(t.range_tickets_30, 0) range_tickets_30, COALESCE(t.billing_tickets_30, 0) billing_tickets_30,
           COALESCE(t.app_tickets_30, 0) app_tickets_30, COALESCE(t.unresolved_30, 0) unresolved_30,
           st.expansion_wave home_wave, st.charger_generation home_gen, st.location_type home_location,
           (st.competitor_since IS NOT NULL AND st.competitor_since <= nr.signup_date + 90) AS home_competitor,
           (COALESCE(f.swaps_61_90, 0) = 0)::INT churned
    FROM nr LEFT JOIN f USING (rider_id) LEFT JOIN t USING (rider_id) LEFT JOIN stations st ON f.home_station = st.station_id''')
    nr = save(q('SELECT * FROM new_riders'), 'q6_new_riders')
    print(f'{len(nr):,} new riders, churn rate {nr.churned.mean():.1%}')
    KEY['q6_new_riders'] = len(nr); KEY['q6_churn_rate'] = float(nr.churned.mean())

    coh = save(nr.groupby('cohort').agg(riders=('rider_id', 'count'), churn_rate=('churned', 'mean'),
               never_swapped=('swaps_30', lambda x: (x == 0).mean()), fail_share=('fails_30', 'sum'),
               attempts=('attempts_30', 'sum'), bad_lot=('bad_lot_packs_30', lambda x: (x > 0).mean())).reset_index(), 'q6_cohort_churn')
    coh['first_month_fail_rate'] = coh.fail_share / coh.attempts
    coh['cohort'] = pd.to_datetime(coh.cohort)
    fig, ax = plt.subplots(figsize=(11, 4.2))
    ax.plot(coh.cohort, coh.churn_rate, marker='o', label='Churned by day 90', color=PALETTE[3])
    ax.plot(coh.cohort, coh.first_month_fail_rate, marker='o', label='Failure rate in first 30 days', color=PALETTE[1])
    ax.plot(coh.cohort, coh.bad_lot, marker='o', label='Got a bad-lot pack in first 30 days', color=PALETTE[4])
    ax.yaxis.set_major_formatter(PCT); ax.set_title('New-rider churn by signup month'); ax.legend(fontsize=8); mark_events(ax)
    savefig(fig, 'q6_cohort_churn')
""")

code(r"""
with section('Q6 churn drivers'):
    d = nr.copy()
    flags = {
        'Had a "no charged battery" failure': d.no_battery_30 > 0,
        'Waited > 10 min at least twice': d.long_waits_30 >= 2,
        'Had a system-error failure': d.system_fail_30 > 0,
        'Got a pack from a bad battery lot': d.bad_lot_packs_30 > 0,
        'Raised a range / vehicle ticket': d.range_tickets_30 > 0,
        'Raised an availability ticket': d.availability_tickets_30 > 0,
        'Raised a billing ticket': d.billing_tickets_30 > 0,
        'Raised an app ticket': d.app_tickets_30 > 0,
        'Ticket left unresolved': d.unresolved_30 > 0,
        'Home station from expansion waves': d.home_wave.fillna('Launch') != 'Launch',
        'Home station has Gen1 chargers': d.home_gen == 'Gen1',
        'Competitor near home station': d.home_competitor.fillna(False).astype(bool),
        'ZipDrop rider': d.partner_group == 'FP-03',
        'Retail (no fleet partner)': d.partner_group == 'RETAIL',
        'Pay-as-you-go plan': d.plan_type == 'pay_as_you_go',
        'Mostly swaps at peak hours': d.peak_share_30 > 0.5,
    }
    rows = []
    for name, m_ in flags.items():
        m_ = m_.fillna(False).astype(bool)
        if m_.sum() < 50 or (~m_).sum() < 50:
            continue
        ce, cu = d.churned[m_].mean(), d.churned[~m_].mean()
        rows.append(dict(factor=name, riders_exposed=int(m_.sum()), share_exposed=m_.mean(), churn_if_exposed=ce,
                         churn_if_not=cu, relative_risk=ce / cu, excess_churners=m_.sum() * (ce - cu)))
    drivers = save(pd.DataFrame(rows).sort_values('excess_churners', ascending=False), 'q6_churn_drivers_univariate')
    display(drivers.round(3))

    import statsmodels.formula.api as smf
    X = pd.DataFrame({f'f{i}': m_.fillna(False).astype(int) for i, m_ in enumerate(flags.values())})
    names = dict(zip(X.columns, flags.keys()))
    X['churned'] = d.churned.values; X['city'] = d.home_city.fillna('unknown').values; X['vehicle'] = d.vehicle_class.values
    X['cohort_q'] = pd.to_datetime(d.cohort).dt.to_period('Q').astype(str).values
    keep = [c for c in names if X[c].sum() >= 50 and (1 - X[c]).sum() >= 50]
    model = smf.logit('churned ~ ' + ' + '.join(keep) + ' + C(city) + C(vehicle) + C(cohort_q)', data=X).fit(disp=0)
    ci = model.conf_int()
    adj = pd.DataFrame({'factor': [names[c] for c in keep], 'odds_ratio': np.exp(model.params[keep]).values,
                        'or_low': np.exp(ci.loc[keep, 0]).values, 'or_high': np.exp(ci.loc[keep, 1]).values,
                        'p_value': model.pvalues[keep].values})
    adj = adj.merge(drivers[['factor', 'riders_exposed', 'excess_churners']], on='factor', how='left')
    adj = save(adj.sort_values('odds_ratio', ascending=False), 'q6_churn_drivers_adjusted')
    display(adj.round(3)); print(f'pseudo R2 = {model.prsquared:.3f}')
    KEY['q6_top_drivers'] = drivers.head(5)[['factor', 'churn_if_exposed', 'churn_if_not', 'excess_churners']].round(3).to_dict('records')

    top = drivers.head(10).iloc[::-1]
    fig, ax = plt.subplots(1, 2, figsize=(17, 5))
    ax[0].barh(top.factor, top.excess_churners, color=PALETTE[3]); ax[0].set_title('Extra churned riders linked to each first-month experience')
    ax[1].barh(top.factor, top.churn_if_exposed, color=PALETTE[3], label='Exposed'); ax[1].barh(top.factor, top.churn_if_not, color='#d0d7de', height=.4, label='Not exposed')
    ax[1].xaxis.set_major_formatter(PCT); ax[1].set_title('Churn rate: exposed vs not'); ax[1].legend(); ax[1].set_yticklabels([])
    savefig(fig, 'q6_churn_drivers')
""")

code(r"""
with section('Q6 ticket evidence'):
    tc = save(q('''
      SELECT category_clean, COUNT(*) tickets, AVG(resolution_hours) avg_resolution_h, AVG(csat) avg_csat,
             AVG((resolution_status = 'unresolved')::INT) unresolved_rate,
             AVG(b.bad_lot) FILTER (WHERE b.battery_id IS NOT NULL) bad_lot_share_of_linked_packs
      FROM tickets tk LEFT JOIN batteries b ON tk.battery_id = b.battery_id GROUP BY 1 ORDER BY 2 DESC'''), 'q6_ticket_categories')
    display(tc)
    base = q("SELECT AVG(out_bad_lot) FROM swaps WHERE completed = 1 AND ts >= TIMESTAMP '2024-07-01'").iloc[0, 0]
    print(f'Baseline: {base:.1%} of swaps since Jul-24 handed out a bad-lot pack.')
    KEY['q6_bad_lot_baseline_share'] = float(base)
""")

# ---------------------------------------------------------------- 9. wrap-up
md(r"""
## 9. Root causes and where to spend the next budget

The ranked root causes and recommendations are written up in the report (`reports/`). The numbers come from
`outputs/tables/` and `outputs/key_numbers.json`, which the cell below produces.
""")

code(r"""
KEY['errors'] = [e['section'] for e in ERRORS]
with open(f'{OUT}/key_numbers.json', 'w') as fh:
    json.dump(KEY, fh, indent=1, default=str)
with open(f'{OUT}/errors.txt', 'w') as fh:
    fh.write('\n\n'.join(f"## {e['section']}\n{e['error']}" for e in ERRORS) or 'no errors')
print('Sections with errors:', KEY['errors'] or 'none')
shutil.make_archive(os.path.join(WORK, 'outputs'), 'zip', OUT)
print('Wrote', os.path.join(WORK, 'outputs.zip'), f"({os.path.getsize(os.path.join(WORK, 'outputs.zip')) / 1e6:.1f} MB)")
if IN_COLAB:
    from google.colab import files
    files.download(os.path.join(WORK, 'outputs.zip'))
""")

# ---------------------------------------------------------------- write
nb = {"cells": [], "metadata": {"kernelspec": {"display_name": "Python 3", "language": "python", "name": "python3"},
                                "language_info": {"name": "python"}, "colab": {"provenance": []}},
      "nbformat": 4, "nbformat_minor": 5}
for i, (kind, src) in enumerate(CELLS):
    cell = {"cell_type": kind, "id": f"c{i:02d}", "metadata": {}, "source": src.splitlines(keepends=True)}
    if kind == "code":
        cell.update(execution_count=None, outputs=[])
    nb["cells"].append(cell)
path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "voltrelay_analysis.ipynb")
with open(path, "w", encoding="utf-8") as fh:
    json.dump(nb, fh, indent=1, ensure_ascii=False)
print("wrote", path, len(CELLS), "cells")
