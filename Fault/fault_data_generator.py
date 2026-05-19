"""
fault_data_generator.py
Generates labelled faulted datasets for Autoencoder evaluation.

Since we cannot train on faults (Autoencoder is unsupervised), this module
is ONLY used at evaluation time to verify the model detects each fault type.

Two strategies depending on your data situation:
  A. You have fault CSVs:  run simulator.py --log, inject a fault at t=300s,
                           stop at t=600s. Pass those CSVs to load_fault_csvs().
  B. No fault CSVs yet:   use synthesize_faults() to inject physics-based
                          fault signatures into a normal session.

Strategy B (synthesis) is perfectly valid for a capstone — it tests whether
the Autoencoder generalises to unseen distributional shifts, not just replays
of training-time faults. Document this choice in your methodology section.
"""

import numpy as np
import pandas as pd
import copy


# ── Fault signature injectors (physics-based) ──────────────────────────────────

FAULT_INJECTORS = {
    "thermal_runaway": _inject_thermal_runaway,
    "overcurrent":     _inject_overcurrent,
    "cell_short":      _inject_cell_short,
    "overvoltage":     _inject_overvoltage,
    "locked_rotor":    _inject_locked_rotor,
    "overspeed":       _inject_overspeed,
}


def synthesize_faults(normal_df: pd.DataFrame,
                       fault_types: list = None,
                       inject_at_fraction: float = 0.3,
                       ) -> dict[str, pd.DataFrame]:
    """
    Synthesize faulted DataFrames from a normal session by injecting
    physics-based fault signatures starting at inject_at_fraction of the data.

    Args:
        normal_df:           output of load_normal_csv() — already feature-engineered
        fault_types:         list of fault names to generate (None = all)
        inject_at_fraction:  where in the session to start fault (0.3 = 30% in)

    Returns:
        dict mapping fault_name → faulted DataFrame (same schema as normal_df)
    """
    if fault_types is None:
        fault_types = list(FAULT_INJECTORS.keys())

    inject_idx  = int(len(normal_df) * inject_at_fraction)
    results     = {}

    for fault_type in fault_types:
        if fault_type not in FAULT_INJECTORS:
            print(f"[WARNING] Unknown fault type: {fault_type}. Skipping.")
            continue

        df_faulted = normal_df.copy()
        FAULT_INJECTORS[fault_type](df_faulted, inject_idx)
        df_faulted["fault_label"] = 0
        df_faulted.iloc[inject_idx:, df_faulted.columns.get_loc("fault_label")] = 1
        results[fault_type] = df_faulted
        print(f"  Synthesized [{fault_type}]: {len(df_faulted)} rows, "
              f"fault from index {inject_idx}")

    return results


def load_fault_csvs(csv_paths: dict[str, str]) -> dict[str, pd.DataFrame]:
    """
    Load pre-recorded fault CSVs (from simulator.py --log + fault_injector.py).

    Args:
        csv_paths: dict mapping fault_name → csv_path
                   e.g. {'thermal_runaway': 'logs/thermal_runaway_session.csv'}

    Returns:
        dict mapping fault_name → DataFrame (same schema as load_normal_csv output)
    """
    from anomaly_features import load_normal_csv
    results = {}
    for fault_name, path in csv_paths.items():
        df = load_normal_csv(path)
        # Mark all rows as faulted (session was recorded with fault active)
        df["fault_label"] = 1
        results[fault_name] = df
        print(f"  Loaded fault CSV [{fault_name}]: {len(df)} rows")
    return results


def build_faulted_sequences(faulted_dfs: dict[str, pd.DataFrame],
                             scaler,
                             window_size: int,
                             fault_only: bool = True,
                             ) -> dict[str, np.ndarray]:
    """
    Build windowed sequences from faulted DataFrames.

    Args:
        faulted_dfs:  output of synthesize_faults() or load_fault_csvs()
        scaler:       fitted MinMaxScaler from normal training
        window_size:  must match Autoencoder training
        fault_only:   if True, only include windows where ALL timesteps are faulted.
                      This gives clean faulted sequences for detection rate measurement.

    Returns:
        dict mapping fault_name → (N, W, F) array of faulted windows
    """
    from anomaly_features import build_sequences, engineer_features, FEATURE_COLS
    results = {}

    for fault_name, df in faulted_dfs.items():
        df_eng = engineer_features(df)

        if fault_only:
            # Only windows where fault_label == 1 for all timesteps
            # We do this by filtering to faulted rows, then windowing
            fault_rows = df_eng[df_eng["fault_label"] == 1].copy()
            if len(fault_rows) < window_size:
                print(f"  [{fault_name}] Not enough faulted rows ({len(fault_rows)}). "
                      f"Using all rows.")
                fault_rows = df_eng.copy()
        else:
            fault_rows = df_eng.copy()

        features = fault_rows[FEATURE_COLS].values.astype(np.float32)
        X_list   = []
        n = len(features)
        for start in range(0, n - window_size + 1, 1):
            X_list.append(features[start : start + window_size])

        if not X_list:
            print(f"  [{fault_name}] No windows generated. Skipping.")
            continue

        X      = np.stack(X_list, axis=0)
        N, W, F = X.shape
        X_flat = X.reshape(-1, F)
        X_flat = scaler.transform(X_flat)
        X      = X_flat.reshape(N, W, F)

        results[fault_name] = X
        print(f"  Faulted sequences [{fault_name}]: {X.shape[0]} windows")

    return results


# ── Physics-based fault injectors ─────────────────────────────────────────────

def _inject_thermal_runaway(df: pd.DataFrame, start_idx: int):
    """Temperature rises ~2°C/s from start_idx, regardless of current."""
    n = len(df)
    for i in range(start_idx, n):
        elapsed = i - start_idx
        df.iloc[i, df.columns.get_loc("temperature")] += 2.0 * elapsed
        # Slight voltage drop as temp rises
        df.iloc[i, df.columns.get_loc("voltage")] -= 0.01 * elapsed


def _inject_overcurrent(df: pd.DataFrame, start_idx: int):
    """Current multiplied by 2.5× from start_idx."""
    mask = df.index >= df.index[start_idx]
    df.loc[mask, "current"] *= 2.5
    # Recompute power
    df.loc[mask, "power_kw"] = (
        df.loc[mask, "voltage"] * df.loc[mask, "current"].abs() / 1000.0
    )


def _inject_cell_short(df: pd.DataFrame, start_idx: int):
    """Voltage drops sharply, current spikes — cell short circuit signature."""
    n = len(df)
    for i in range(start_idx, n):
        elapsed = i - start_idx
        drop    = min(60.0, 0.3 * elapsed)   # voltage drops up to 60 V
        df.iloc[i, df.columns.get_loc("voltage")] = max(
            280.0,   # MIN_V
            df.iloc[i, df.columns.get_loc("voltage")] - drop
        )
        df.iloc[i, df.columns.get_loc("current")] += 30.0


def _inject_overvoltage(df: pd.DataFrame, start_idx: int):
    """Voltage spikes above safe limit — simulates charger malfunction."""
    mask = df.index >= df.index[start_idx]
    df.loc[mask, "voltage"] = df.loc[mask, "voltage"].clip(lower=410.0) + 15.0
    df.loc[mask, "temperature"] += 0.5 * np.arange(mask.sum())


def _inject_locked_rotor(df: pd.DataFrame, start_idx: int):
    """Motor stalls: speed drops to 0, torque spikes, current surges."""
    mask = df.index >= df.index[start_idx]
    df.loc[mask, "speed_kmh"] = 0.0
    df.loc[mask, "rpm"]       = 0.0
    df.loc[mask, "torque_nm"] = df.loc[mask, "torque_nm"].abs() * 3.0
    df.loc[mask, "current"]   += 80.0
    df.loc[mask, "power_kw"]  = (
        df.loc[mask, "voltage"] * df.loc[mask, "current"].abs() / 1000.0
    )


def _inject_overspeed(df: pd.DataFrame, start_idx: int):
    """Motor RPM exceeds safe limit by 30%."""
    mask = df.index >= df.index[start_idx]
    df.loc[mask, "rpm"]       *= 1.35
    df.loc[mask, "speed_kmh"] *= 1.35
    df.loc[mask, "torque_nm"] *= 0.7   # RPM up, torque down (power constant)
