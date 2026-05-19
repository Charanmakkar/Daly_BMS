# MQTT → InfluxDB Subscriber

Persists all EV Digital Twin telemetry to InfluxDB for long-run analysis,
Grafana dashboards, and cross-session comparison.

## Files

| File | Purpose |
|---|---|
| `config.py` | All settings — MQTT broker, InfluxDB credentials, schema |
| `subscriber.py` | Main process — MQTT listener + write pipeline |
| `influx_writer.py` | Thread-safe batched writer with retry and backpressure |
| `point_builder.py` | Converts MQTT JSON → InfluxDB Point objects |
| `queries.py` | Reusable Flux query helpers for Streamlit + analysis |
| `grafana_dashboard.json` | Pre-built Grafana dashboard — import directly |

## Setup

### 1. Install InfluxDB

**Cloud (recommended — free tier, no setup)**
1. Sign up at https://cloud2.influxdata.com/signup
2. Create a bucket: Data → Buckets → Create Bucket → name it `ev_twin`
3. Create a token: Data → API Tokens → Generate API Token → All Access
4. Copy your org name from account settings

**Local with Docker**
```bash
docker run -d -p 8086:8086 --name influxdb influxdb:2
# Open http://localhost:8086 and complete wizard
# Create bucket: ev_twin
# Create token: All Access
```

### 2. Configure credentials

Edit `config.py`:
```python
INFLUX_URL   = "https://us-east-1-1.aws.cloud2.influxdata.com"  # your cloud URL
INFLUX_TOKEN = "your-api-token-here"
INFLUX_ORG   = "your-org-name"
INFLUX_BUCKET = "ev_twin"
```

### 3. Install Python deps

```bash
pip install -r requirements.txt
```

### 4. Test connection (dry run)

```bash
python subscriber.py --dry_run
```

You'll see InfluxDB line-protocol output printed to the console —
no actual writes to the database. Confirm the format looks right.

### 5. Run for real

```bash
python subscriber.py
```

Or with options:

```bash
python subscriber.py \
    --broker 192.168.1.100 \
    --session trip_session_01 \
    --verbose
```

### 6. Set up Grafana (optional but impressive for demo)

```bash
docker run -d -p 3000:3000 --name grafana grafana/grafana
# Open http://localhost:3000  (admin / admin)
# Add datasource: Configuration → Data Sources → Add → InfluxDB
#   Query language: Flux
#   URL: http://localhost:8086 (or cloud URL)
#   Token / Org / Default bucket: as configured
# Import dashboard: + → Import → Upload JSON → select grafana_dashboard.json
```

## InfluxDB schema

All data is stored in the `ev_twin` bucket:

| Measurement | Source topic | Key fields |
|---|---|---|
| `battery` | ev/battery | voltage, current, temperature, soc, power_kw |
| `motor` | ev/motor | speed_kmh, torque_nm, rpm, mech_power_kw |
| `gps` | ev/gps | latitude, longitude, elevation_m, distance_km |
| `soc_model` | ev/soc_predicted | predicted_soc, coulomb_soc, correction_delta |
| `soh_model` | ev/soh_predicted | soh_pct, rul_cycles, projected_eol_cycle |
| `anomaly` | ev/anomaly_score | global_error, threshold, error_ratio, alert |
| `anomaly_alert` | ev/anomaly | global_error, error_ratio; tags: likely_fault, severity |
| `anomaly_subsystem` | ev/anomaly | error, flagged; tags: subsystem, likely_fault |
| `range_model` | ev/range | range_km, static_km, dynamic_km, consumption_wh_km |
| `fault_injected` | ev/fault | tags: type, target |

**Tags on every measurement:** `vehicle_id`, `session`

## Querying with queries.py

```python
from queries import InfluxReader

reader = InfluxReader()

# SoC comparison chart
soc_df  = reader.soc_history(minutes=10)

# All fault alerts today
alerts  = reader.fault_alerts(hours=24)

# Long-run SoH degradation (for the report)
soh_df  = reader.soh_degradation_all_sessions()

# Fault summary by type
summary = reader.fault_count_by_type(days=7)
print(summary)
```

## Useful Flux queries for InfluxDB Data Explorer

```flux
// Mean battery temperature per session
from(bucket: "ev_twin")
  |> range(start: -7d)
  |> filter(fn: (r) => r._measurement == "battery" and r._field == "temperature")
  |> group(columns: ["session"])
  |> mean()

// Reconstruction error spikes (anomaly events only)
from(bucket: "ev_twin")
  |> range(start: -24h)
  |> filter(fn: (r) => r._measurement == "anomaly" and r._field == "error_ratio")
  |> filter(fn: (r) => r._value > 1.0)

// LSTM SoC correction delta over time
from(bucket: "ev_twin")
  |> range(start: -30m)
  |> filter(fn: (r) => r._measurement == "soc_model" and r._field == "correction_delta")
```

## Connecting to the Streamlit dashboard

Add `queries.py` to your dashboard folder and import `InfluxReader` in any page:

```python
# In pages/2_Battery_Health.py — add long-run SoH chart
from queries import InfluxReader
reader = InfluxReader()
soh_all = reader.soh_degradation_all_sessions()
# → plot soh_all with Plotly
```

This adds persistent history that survives dashboard restarts —
the in-memory `SafeDeque` only holds 5 minutes; InfluxDB holds everything.

## Health output (every 30 seconds)

```
10:42:15  INFO     subscriber   [Health] msgs=2,340 | written=2,280  dropped=0  errors=0
10:42:45  INFO     subscriber   [Health] msgs=2,430 | written=2,370  dropped=0  errors=0
```

`written` slightly lags `msgs` due to batch interval — this is expected.
`dropped > 0` means InfluxDB can't keep up — increase `WRITE_INTERVAL_MS`.
`errors > 0` means writes are failing — check token and network.
