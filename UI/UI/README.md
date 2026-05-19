# EV Digital Twin — Streamlit Dashboard

Live monitoring dashboard for all four ML models. Connects to the MQTT
broker and displays real-time telemetry, predictions, and fault alerts.

## Pages

| Page | Key content |
|---|---|
| **Home** | System overview, live metrics, recent alerts |
| **1 Live Telemetry** | Gauges (SoC, temp, speed, power) + rolling charts |
| **2 Battery Health** | SoC comparison, SoH degradation curve, RUL countdown |
| **3 Anomaly Detection** | Reconstruction error feed, subsystem breakdown, alert log |
| **4 Range Prediction** | Dual-mode range gauge, blend indicator, trend chart |
| **5 GPS Map** | Live position track, elevation profile, energy vs distance |

## Setup

```bash
pip install -r requirements.txt
```

## Running the full system

Start all components in separate terminals:

```bash
# Terminal 1 — Arduino simulator (or real board)
python simulator.py

# Terminal 2 — SoC inference (LSTM, every second)
cd soc_model && python inference.py --broker localhost

# Terminal 3 — SoH / RUL inference (every 5 min)
cd soh_model && python inference_soh.py --broker localhost

# Terminal 4 — Anomaly detection (every second)
cd anomaly_model && python inference_anomaly.py --broker localhost

# Terminal 5 — Range prediction (every second)
cd range_model && python inference_range.py --broker localhost

# Terminal 6 — Dashboard
cd dashboard && streamlit run app.py
```

Open http://localhost:8501 in your browser.
Enter broker IP (localhost if running on same machine) and click Connect.

## For the live demo (fault injection)

```bash
# Terminal 7 — inject faults while presenting
python fault_injector.py --broker localhost
# → choose option 1 (thermal_runaway)
# → watch Anomaly Detection page alert within 20 seconds
```

## MQTT topics consumed

| Topic | Source | Dashboard use |
|---|---|---|
| `ev/battery` | Arduino | Live gauges, rolling charts |
| `ev/motor` | Arduino | Speed gauge, GPS speed colour |
| `ev/gps` | Arduino | GPS map, elevation profile |
| `ev/soc_predicted` | SoC model | LSTM SoC metric, SoC chart |
| `ev/soh_predicted` | SoH model | SoH degradation, RUL countdown |
| `ev/anomaly_score` | Anomaly model | Error chart, ratio metric |
| `ev/anomaly` | Anomaly model | Alert log, subsystem breakdown |
| `ev/range` | Range model | Range gauge, blend bar, trend |
| `ev/status` | Arduino | Sidebar snapshot |

## Architecture note

`mqtt_state.py` runs one background MQTT thread per Streamlit session.
All pages import the same `state` singleton — they read thread-safe
snapshots via `state.battery_hist.snapshot()` etc.
Streamlit's `st.rerun()` on a 1-second timer drives the auto-refresh.
