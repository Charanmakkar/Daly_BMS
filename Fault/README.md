# Anomaly Detection — LSTM Autoencoder

Real-time EV fault detection using unsupervised deep learning.
Trains only on normal data — no fault labels needed at training time.

## How it works (one-paragraph summary for your report)

An LSTM Autoencoder is trained to compress and reconstruct sequences of 12
sensor features from normal EV operation. Because the model has only ever
seen normal data, it becomes very good at reconstructing normal patterns
and very bad at reconstructing anomalous ones. At inference time, the
reconstruction error (MSE between input and output) is monitored continuously.
When it exceeds the 99th-percentile threshold computed on validation data,
an alert fires. Per-subsystem reconstruction errors (battery, motor, derived,
ratios) provide an automatic first-level fault diagnosis.

## Files

| File | Purpose |
|---|---|
| `anomaly_features.py` | 12-feature engineering + sliding window builder |
| `autoencoder.py` | LSTM Autoencoder architecture, training, threshold calibration |
| `fault_data_generator.py` | Creates faulted datasets for evaluation |
| `train_anomaly.py` | End-to-end training + evaluation entry point |
| `inference_anomaly.py` | Live MQTT subscriber with real-time alerts |

## Setup

```bash
pip install -r requirements.txt
```

## Step 1 — Collect normal training data

Run the Arduino simulator for several sessions **without injecting any faults**.
Each session should be at least 10 minutes long (600+ seconds).

```bash
# Session 1
python simulator.py --duration 1800 --log    # 30 min

# Session 2
python simulator.py --duration 1800 --log    # another 30 min
```

You'll get CSV files like `ev_log_20260323_*.csv`. You need these.

## Step 2 — Optionally collect fault data (for evaluation only)

Run the simulator again and inject faults via fault_injector.py:
```bash
# Terminal 1
python simulator.py --duration 600 --log

# Terminal 2 (inject at ~60 seconds)
python fault_injector.py --broker localhost
# → choose option 1 (thermal runaway)
```

Collect one CSV per fault type for the best evaluation results.
If you skip this step, `--synthesize_faults` will generate synthetic fault data.

## Step 3 — Train

```bash
# With synthesized faults (quick start — no fault CSVs needed)
python train_anomaly.py \
    --normal_csvs ev_log_session1.csv ev_log_session2.csv \
    --synthesize_faults

# With real fault CSVs
python train_anomaly.py \
    --normal_csvs ev_log_normal_1.csv ev_log_normal_2.csv \
    --fault_csvs  thermal_runaway=ev_log_fault_thermal.csv \
                  overcurrent=ev_log_fault_overcurrent.csv \
                  locked_rotor=ev_log_fault_motor.csv
```

## Step 4 — Live inference

```bash
python inference_anomaly.py --broker 192.168.1.100
```

Normal heartbeat every 30 seconds:
```
[OK] tick=   30  err=0.000042  (0.2× threshold)
[OK] tick=   60  err=0.000039  (0.2× threshold)
```

When you inject a fault via fault_injector.py:
```
  *** ANOMALY ALERT [HIGH] ***  tick=  73  #1
  Error:     0.004120  (22.0× threshold)
  Diagnosis: thermal_runaway
  Flagged:   battery, derived, ratios
```

## The 12 features

| Feature | Group | What it captures |
|---|---|---|
| voltage | battery | Terminal voltage — drops with load and age |
| current | battery | Discharge current — spikes in overcurrent faults |
| temperature | battery | Cell temperature — key thermal runaway indicator |
| power_kw | battery | V×I — overall power demand |
| speed_kmh | motor | Vehicle speed — drops to 0 in locked_rotor |
| torque_nm | motor | Motor torque — spikes during stall |
| rpm | motor | Motor RPM — exceeds limit in overspeed |
| delta_voltage | derived | dV/dt — rate of voltage change |
| delta_temp | derived | dT/dt — temperature rate of change (early warning) |
| soc_rate | derived | dSoC/dt — abnormal drain rate |
| power_per_speed | ratios | kW per km/h — efficiency ratio |
| thermal_load | ratios | I²/T — heating vs temperature (fault signature) |

## Outputs after training

```
anomaly_output/
    autoencoder_weights.keras   ← model weights
    anomaly_scaler.pkl          ← feature scaler
    thresholds.json             ← global + per-subsystem thresholds
    detection_results.csv       ← detection rate per fault type
    error_distribution.png      ← normal vs faulted error distributions
    training_loss.png           ← reconstruction loss over epochs
```

## Expected detection rates

| Fault type | Expected detection |
|---|---|
| thermal_runaway | > 95% |
| overcurrent | > 90% |
| cell_short | > 85% |
| locked_rotor | > 90% |
| overspeed | > 80% |
| False positive rate | < 1% |

## For your capstone report

**Why unsupervised?** You only have 6 injected fault types from the simulator.
A real EV fleet faces hundreds of failure modes. An Autoencoder generalises to
ANY distributional shift — even faults it has never seen — because it detects
"different from normal", not "matches known fault X".

**Why LSTM vs Dense Autoencoder?** Faults develop over time — thermal runaway
starts gradual before it accelerates. An LSTM captures the temporal pattern of
normal operation; a Dense Autoencoder treats each timestep independently and
misses early-stage faults.

**Threshold percentile tradeoff:** 99th percentile gives ~1% false positive rate
on normal data. For a safety-critical system you'd lower to 97th (fewer missed
detections, more false alarms). This is a real engineering tradeoff worth
discussing in your report.
