"""
anomaly_features.py
Feature engineering for the Autoencoder anomaly detector.

Design philosophy:
  The Autoencoder learns what NORMAL looks like across multiple subsystems
  simultaneously. If ANY subsystem behaves abnormally, the reconstruction
  error for that subsystem's features spikes — giving both a global alert
  and a per-subsystem diagnosis.

Feature groups (12 features total):
  ┌─────────────────────────────────────────────────────────────────┐
  │  Battery (4)    voltage, current, temperature, power_kw        │
  │  Motor   (3)    speed_kmh, torque_nm, rpm                      │
  │  Derived (3)    delta_voltage, delta_temp, soc_rate_of_change  │
  │  Ratios  (2)    power_per_speed, thermal_efficiency            │
  └─────────────────────────────────────────────────────────────────┘

The ratios are the most powerful features for fault detection:
  power_per_speed        = kW / speed — high if motor is inefficient
                           spikes during locked_rotor, overcurrent
  thermal_efficiency     = current² / temp_rise — measures if heat
                           generation is proportional to current load
                           deviates sharply during thermal_runaway

Window approach:
  Same as SoC model — a sliding window of WINDOW_SIZE timesteps.
  The Autoencoder sees the full temporal pattern, not just a snapshot.
  This catches faults that develop over several seconds (e.g. thermal
  runaway starts gradual before it accelerates).
"""

import numpy as np
import pandas as pd
import pickle


# ── Configuration ──────────────────────────────────────────────────────────────
WINDOW_SIZE  = 20       # shorter than SoC — faults are abrupt, need fast detection
STEP_SIZE    = 1        # maximum window overlap for dense training data

FEATURE_COLS = [
    # Battery subsystem
    "voltage",
    "current",
    "temperature",
    "power_kw",
    # Motor subsystem
    "speed_kmh",
    "torque_nm",
    "rpm",
    # Derived temporal features
    "delta_voltage",
    "delta_temp",
    "soc_rate",
    # Cross-subsystem ratios
    "power_per_speed",
    "thermal_load",
]

N_FEATURES = len(FEATURE_COLS)

# Feature groups used for per-subsystem reconstruction error breakdown
FEATURE_GROUPS = {
    "battery": ["voltage", "current", "temperature", "power_kw"],
    "motor":   ["speed_kmh", "torque_nm", "rpm"],
    "derived": ["delta_voltage", "delta_temp", "soc_rate"],
    "ratios":  ["power_per_speed", "thermal_load"],
}


# ── Scaler (same lightweight class as SoC model) ──────────────────────────────

class MinMaxScaler:
    def __init__(self):
        self.min_ = None
        self.range_ = None

    def fit(self, X):
        self.min_   = X.min(axis=0)
        max_        = X.max(axis=0)
        self.range_ = np.where(max_ - self.min_ == 0, 1.0, max_ - self.min_)
        return self

    def transform(self, X):
        return (X - self.min_) / self.range_

    def fit_transform(self, X):
        return self.fit(X).transform(X)

    def save(self, path):
        with open(path, "wb") as f:
            pickle.dump(self, f)

    @staticmethod
    def load(path):
        with open(path, "rb") as f:
            return pickle.load(f)


# ── Feature engineering ────────────────────────────────────────────────────────

def engineer_features(df: pd.DataFrame) -> pd.DataFrame:
    """
    Add derived features and cross-subsystem ratios to a raw DataFrame.

    Expects columns: voltage, current, temperature, power_kw,
                     speed_kmh, torque_nm, rpm, soc (0-100 scale), time_s
    """
    df = df.copy().sort_values("time_s").reset_index(drop=True)

    # Temporal deltas (per-second rates of change)
    df["delta_voltage"] = df["voltage"].diff().fillna(0.0)
    df["delta_temp"]    = df["temperature"].diff().fillna(0.0)
    df["soc_rate"]      = df["soc"].diff().fillna(0.0)   # SoC change per second

    # Cross-subsystem ratios
    # Power-per-speed: how much power needed per km/h
    # Near-zero speed → clip to avoid division by zero
    df["power_per_speed"] = df["power_kw"] / df["speed_kmh"].clip(lower=1.0)

    # Thermal load: current² (heating source) normalised by temperature
    # High = lots of heating without proportional temp rise (initial fault sign)
    df["thermal_load"]    = (df["current"] ** 2) / df["temperature"].clip(lower=1.0)

    return df


def build_sequences(df: pd.DataFrame,
                    window_size: int = WINDOW_SIZE,
                    step: int = STEP_SIZE,
                    scaler: MinMaxScaler = None,
                    fit_scaler: bool = True,
                    ) -> tuple[np.ndarray, MinMaxScaler]:
    """
    Build sliding-window sequences for Autoencoder training.

    For anomaly detection:
      - Only NORMAL (fault-free) data is used for training.
      - Faulted data is only used at evaluation time to verify
        that reconstruction error spikes correctly.

    Returns:
        X:       (n_samples, window_size, n_features)  float32
        scaler:  fitted MinMaxScaler
    """
    df = engineer_features(df)

    missing = [c for c in FEATURE_COLS if c not in df.columns]
    if missing:
        raise ValueError(f"Missing columns after feature engineering: {missing}")

    X_list = []
    n      = len(df)

    if n < window_size:
        raise ValueError(f"DataFrame has {n} rows but window_size={window_size}. "
                         f"Need at least {window_size} rows of normal data.")

    features = df[FEATURE_COLS].values.astype(np.float32)

    for start in range(0, n - window_size + 1, step):
        X_list.append(features[start : start + window_size])

    X = np.stack(X_list, axis=0)    # (N, W, F)

    N, W, F = X.shape
    X_flat  = X.reshape(-1, F)

    if fit_scaler:
        scaler = MinMaxScaler()
        X_flat = scaler.fit_transform(X_flat)
    else:
        if scaler is None:
            raise ValueError("Pass a fitted scaler when fit_scaler=False.")
        X_flat = scaler.transform(X_flat)

    X = X_flat.reshape(N, W, F)
    print(f"Anomaly sequences: X={X.shape}  "
          f"(window={window_size}, features={F})")
    return X, scaler


def build_inference_window(buffer: list[dict],
                           scaler: MinMaxScaler,
                           window_size: int = WINDOW_SIZE,
                           ) -> np.ndarray | None:
    """
    Build one inference window from a rolling deque of MQTT messages.

    Args:
        buffer:      list of dicts with raw MQTT fields, oldest first.
                     Must have at least window_size entries.
        scaler:      fitted MinMaxScaler from training
        window_size: must match training

    Returns:
        (1, window_size, n_features) float32 array or None if buffer too short
    """
    if len(buffer) < window_size:
        return None

    recent = list(buffer)[-window_size:]

    rows      = []
    prev_v    = recent[0].get("voltage",     350.0)
    prev_t    = recent[0].get("temperature",  25.0)
    prev_soc  = recent[0].get("soc",          95.0)

    for entry in recent:
        v    = float(entry.get("voltage",      350.0))
        i    = float(entry.get("current",        5.0))
        t    = float(entry.get("temperature",   25.0))
        pkw  = float(entry.get("power_kw",       0.0))
        spd  = float(entry.get("speed_kmh",      0.0))
        trq  = float(entry.get("torque_nm",      0.0))
        rpm  = float(entry.get("rpm",            0.0))
        soc  = float(entry.get("soc",           95.0))

        dv   = v   - prev_v
        dt   = t   - prev_t
        dsoc = soc - prev_soc

        pps  = pkw / max(spd, 1.0)
        thl  = (i ** 2) / max(t, 1.0)

        rows.append([v, i, t, pkw, spd, trq, rpm, dv, dt, dsoc, pps, thl])
        prev_v, prev_t, prev_soc = v, t, soc

    X = np.array(rows, dtype=np.float32)
    X = scaler.transform(X)
    return X[np.newaxis, :, :]     # (1, W, F)


# ── Simulated fault data builder ───────────────────────────────────────────────

def load_normal_csv(csv_path: str) -> pd.DataFrame:
    """
    Load a normal (fault-free) Arduino simulator CSV and return
    a DataFrame ready for feature engineering.

    Expected columns: tick, batt_voltage, batt_current, batt_temperature,
                      batt_soc, motor_speed_kmh, motor_torque_nm, motor_rpm
    """
    import pandas as pd
    raw = pd.read_csv(csv_path).sort_values("tick").reset_index(drop=True)

    # Compute power_kw if not present
    if "batt_power_kw" in raw.columns:
        power = raw["batt_power_kw"]
    else:
        power = (raw["batt_voltage"] * raw["batt_current"].abs()) / 1000.0

    df = pd.DataFrame({
        "time_s":      raw["tick"].astype(float),
        "voltage":     raw["batt_voltage"],
        "current":     raw["batt_current"],
        "temperature": raw["batt_temperature"],
        "power_kw":    power,
        "speed_kmh":   raw.get("motor_speed_kmh",  0.0),
        "torque_nm":   raw.get("motor_torque_nm",  0.0),
        "rpm":         raw.get("motor_rpm",         0.0),
        "soc":         raw["batt_soc"],          # already in % (0-100)
        "fault_label": 0,                         # 0 = normal
    })
    print(f"Loaded normal CSV: {len(df):,} rows from {csv_path}")
    return df
