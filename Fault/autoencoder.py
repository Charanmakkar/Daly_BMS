"""
autoencoder.py
LSTM Autoencoder for EV anomaly detection.

Architecture:
                              ┌──────────────────────────────────────┐
  Input                       │           Encoder                    │
  (W, F)  ──► LSTM(64,seq) ──► LSTM(32,seq) ──► LSTM(16,no-seq)    │
                              └──────────────────────────────────────┘
                                        │ bottleneck (16-d)
                              ┌──────────────────────────────────────┐
                              │           Decoder                    │
              RepeatVector ◄── Dense(32) ◄── LSTM(16,seq) ◄── ...   │
                              └──────────────────────────────────────┘
                                        │
  Output  ◄── TimeDistributed(Dense(F)) ← reconstructed (W, F)

Loss: MSE between input and reconstruction

Why an Autoencoder and not a classifier?
  A classifier needs labelled fault examples from YOUR specific system.
  You only have 6 fault types from fault_injector.py — nowhere near
  enough to train a reliable classifier.

  An Autoencoder trains only on NORMAL data. It learns to compress
  and reconstruct normal patterns efficiently. When a fault occurs,
  the pattern is unlike anything it has seen — reconstruction fails,
  MSE spikes. No fault labels needed during training.

Threshold calibration:
  After training on normal data, we run the model on a clean
  validation set and compute the 99th percentile of reconstruction
  error. Anything above this is flagged as anomalous. This gives
  roughly 1% false positive rate on normal operation.
"""

import os
import json
import numpy as np
import pickle

import tensorflow as tf
from tensorflow import keras
from tensorflow.keras import layers

from anomaly_features import (
    WINDOW_SIZE, N_FEATURES, FEATURE_COLS, FEATURE_GROUPS
)


# ── Model factory ──────────────────────────────────────────────────────────────

def build_autoencoder(window_size:   int   = WINDOW_SIZE,
                      n_features:    int   = N_FEATURES,
                      enc_units:     list  = [64, 32, 16],
                      dec_units:     list  = [16, 32],
                      dropout_rate:  float = 0.15,
                      learning_rate: float = 1e-3) -> keras.Model:
    """
    Build and compile the LSTM Autoencoder.

    Encoder:  3 stacked LSTMs compressing (W, F) → 16-d bottleneck
    Decoder:  RepeatVector restores time dimension, LSTMs reconstruct,
              TimeDistributed Dense outputs per-timestep feature values

    Args:
        window_size:   timesteps per window (must match training)
        n_features:    number of input features
        enc_units:     LSTM units for each encoder layer [64, 32, 16]
        dec_units:     LSTM units for each decoder layer [16, 32]
        dropout_rate:  applied after each LSTM
        learning_rate: Adam initial LR

    Returns:
        Compiled Keras model
    """
    inputs = keras.Input(shape=(window_size, n_features), name="sensor_window")

    # ── Encoder ───────────────────────────────────────────────────────────────
    x = layers.LSTM(enc_units[0], return_sequences=True,  name="enc_lstm_1")(inputs)
    x = layers.Dropout(dropout_rate, name="enc_drop_1")(x)
    x = layers.LSTM(enc_units[1], return_sequences=True,  name="enc_lstm_2")(x)
    x = layers.Dropout(dropout_rate, name="enc_drop_2")(x)
    # Bottleneck: compress entire sequence to a single vector
    x = layers.LSTM(enc_units[2], return_sequences=False, name="bottleneck")(x)

    # ── Decoder ───────────────────────────────────────────────────────────────
    # Repeat bottleneck vector across time dimension
    x = layers.RepeatVector(window_size, name="repeat")(x)
    x = layers.LSTM(dec_units[0], return_sequences=True,  name="dec_lstm_1")(x)
    x = layers.Dropout(dropout_rate, name="dec_drop_1")(x)
    x = layers.LSTM(dec_units[1], return_sequences=True,  name="dec_lstm_2")(x)

    # Reconstruct all features at every timestep
    output = layers.TimeDistributed(
        layers.Dense(n_features, activation="linear"),
        name="reconstruction"
    )(x)

    model = keras.Model(inputs=inputs, outputs=output, name="lstm_autoencoder")
    model.compile(
        optimizer=keras.optimizers.Adam(learning_rate=learning_rate),
        loss="mse",
        metrics=[keras.metrics.MeanAbsoluteError(name="mae")]
    )
    return model


# ── Training ───────────────────────────────────────────────────────────────────

def train_autoencoder(model: keras.Model,
                      X_train: np.ndarray,
                      X_val:   np.ndarray,
                      output_dir:  str = "anomaly_output",
                      epochs:      int = 80,
                      batch_size:  int = 64,
                      ) -> keras.callbacks.History:
    """
    Train the Autoencoder on NORMAL data only.
    Target = Input (self-supervised reconstruction).

    Args:
        model:      compiled Autoencoder from build_autoencoder()
        X_train:    (N, W, F) normal training windows
        X_val:      (N, W, F) normal validation windows
        output_dir: where to save weights and logs

    Returns:
        Keras History
    """
    os.makedirs(output_dir, exist_ok=True)
    weights_path = os.path.join(output_dir, "autoencoder_weights.keras")

    cb_list = [
        keras.callbacks.ModelCheckpoint(
            filepath=weights_path,
            monitor="val_loss",
            save_best_only=True,
            verbose=1,
        ),
        keras.callbacks.EarlyStopping(
            monitor="val_loss",
            patience=10,
            restore_best_weights=True,
            verbose=1,
        ),
        keras.callbacks.ReduceLROnPlateau(
            monitor="val_loss",
            factor=0.5,
            patience=5,
            min_lr=1e-6,
            verbose=1,
        ),
        keras.callbacks.CSVLogger(
            os.path.join(output_dir, "training_log.csv")
        ),
    ]

    print(f"\nTraining Autoencoder on NORMAL data only.")
    print(f"Train: {X_train.shape[0]:,} windows  |  Val: {X_val.shape[0]:,} windows\n")

    # Self-supervised: target = input
    history = model.fit(
        X_train, X_train,
        validation_data=(X_val, X_val),
        epochs=epochs,
        batch_size=batch_size,
        callbacks=cb_list,
        verbose=1,
    )

    hist_path = os.path.join(output_dir, "training_history.json")
    with open(hist_path, "w") as f:
        json.dump(
            {k: [float(v) for v in vals]
             for k, vals in history.history.items()},
            f, indent=2
        )
    print(f"\nWeights saved  → {weights_path}")
    print(f"History saved  → {hist_path}")
    return history


# ── Threshold calibration ──────────────────────────────────────────────────────

def calibrate_threshold(model: keras.Model,
                         X_normal: np.ndarray,
                         percentile: float = 99.0,
                         output_dir: str = "anomaly_output",
                         ) -> dict:
    """
    Compute the anomaly detection threshold from NORMAL validation data.

    Method:
      1. Run the model on all normal validation windows
      2. Compute per-window MSE (reconstruction error)
      3. Set threshold = percentile of that distribution
         (99th percentile → ~1% false-positive rate on normal data)

    Also computes per-feature-group thresholds for sub-system diagnostics.

    Returns:
        dict with 'global_threshold' and per-group thresholds
    """
    print(f"\nCalibrating threshold on {X_normal.shape[0]:,} normal windows …")

    X_reconstructed = model.predict(X_normal, verbose=0)

    # Global reconstruction error (MSE per window)
    errors = np.mean((X_reconstructed - X_normal) ** 2, axis=(1, 2))  # (N,)

    threshold = float(np.percentile(errors, percentile))

    print(f"  Reconstruction error — "
          f"mean={errors.mean():.6f}  "
          f"std={errors.std():.6f}  "
          f"p99={threshold:.6f}")

    # Per feature-group thresholds for subsystem diagnosis
    group_thresholds = {}
    for group_name, group_feats in FEATURE_GROUPS.items():
        feat_indices = [FEATURE_COLS.index(f) for f in group_feats
                        if f in FEATURE_COLS]
        if not feat_indices:
            continue
        grp_errors = np.mean(
            (X_reconstructed[:, :, feat_indices] - X_normal[:, :, feat_indices]) ** 2,
            axis=(1, 2)
        )
        group_thresholds[group_name] = {
            "threshold": float(np.percentile(grp_errors, percentile)),
            "mean":      float(grp_errors.mean()),
            "std":       float(grp_errors.std()),
        }

    thresholds = {
        "global_threshold": threshold,
        "percentile":       percentile,
        "n_calibration":    int(len(errors)),
        "error_mean":       float(errors.mean()),
        "error_std":        float(errors.std()),
        "feature_groups":   group_thresholds,
    }

    os.makedirs(output_dir, exist_ok=True)
    thr_path = os.path.join(output_dir, "thresholds.json")
    with open(thr_path, "w") as f:
        json.dump(thresholds, f, indent=2)
    print(f"  Global threshold ({percentile}th pct): {threshold:.6f}")
    print(f"  Thresholds saved → {thr_path}")
    return thresholds


# ── Evaluation on labelled data ────────────────────────────────────────────────

def evaluate_detection(model: keras.Model,
                        X_normal: np.ndarray,
                        X_faulted_dict: dict[str, np.ndarray],
                        thresholds: dict,
                        output_dir: str = "anomaly_output",
                        ) -> pd.DataFrame:
    """
    Evaluate detection performance on labelled normal + faulted windows.

    Args:
        model:           trained Autoencoder
        X_normal:        (N, W, F) normal test windows
        X_faulted_dict:  {'thermal_runaway': (N, W, F), 'overcurrent': ...}
                         keyed by fault type name
        thresholds:      output of calibrate_threshold()

    Returns:
        DataFrame with per-fault-type detection rates
    """
    import pandas as pd

    thr = thresholds["global_threshold"]
    records = []

    # Normal windows — should NOT be flagged
    X_rec_n  = model.predict(X_normal, verbose=0)
    err_n    = np.mean((X_rec_n - X_normal) ** 2, axis=(1, 2))
    fp_rate  = float(np.mean(err_n > thr))
    records.append({
        "fault_type":     "normal",
        "n_windows":      len(X_normal),
        "detection_rate": 0.0,
        "false_pos_rate": round(fp_rate * 100, 1),
        "mean_error":     round(float(err_n.mean()), 6),
        "max_error":      round(float(err_n.max()), 6),
    })
    print(f"\n  Normal:  false positive rate = {fp_rate*100:.1f}%")

    # Faulted windows — should be flagged
    for fault_name, X_fault in X_faulted_dict.items():
        X_rec_f  = model.predict(X_fault, verbose=0)
        err_f    = np.mean((X_rec_f - X_fault) ** 2, axis=(1, 2))
        det_rate = float(np.mean(err_f > thr))
        records.append({
            "fault_type":     fault_name,
            "n_windows":      len(X_fault),
            "detection_rate": round(det_rate * 100, 1),
            "false_pos_rate": 0.0,
            "mean_error":     round(float(err_f.mean()), 6),
            "max_error":      round(float(err_f.max()), 6),
        })
        print(f"  {fault_name:<22} detection = {det_rate*100:.1f}%  "
              f"mean_err = {err_f.mean():.6f}")

    result_df = pd.DataFrame(records)
    os.makedirs(output_dir, exist_ok=True)
    result_df.to_csv(
        os.path.join(output_dir, "detection_results.csv"), index=False
    )
    return result_df


# ── Per-window reconstruction analysis ────────────────────────────────────────

def reconstruct_errors(model: keras.Model,
                        X: np.ndarray,
                        ) -> tuple[np.ndarray, np.ndarray]:
    """
    Compute per-window global error and per-feature-group errors.

    Returns:
        global_errors:  (N,)     MSE per window
        group_errors:   (N, G)   MSE per window per feature group
    """
    X_rec  = model.predict(X, verbose=0)
    global_errors = np.mean((X_rec - X) ** 2, axis=(1, 2))

    group_errors = []
    for group_feats in FEATURE_GROUPS.values():
        idx  = [FEATURE_COLS.index(f) for f in group_feats if f in FEATURE_COLS]
        gerr = np.mean((X_rec[:, :, idx] - X[:, :, idx]) ** 2, axis=(1, 2))
        group_errors.append(gerr)

    return global_errors, np.stack(group_errors, axis=1)


# ── Save / load helpers ───────────────────────────────────────────────────────

def save_thresholds(thresholds: dict, output_dir: str):
    path = os.path.join(output_dir, "thresholds.json")
    os.makedirs(output_dir, exist_ok=True)
    with open(path, "w") as f:
        json.dump(thresholds, f, indent=2)


def load_thresholds(output_dir: str) -> dict:
    path = os.path.join(output_dir, "thresholds.json")
    with open(path) as f:
        return json.load(f)


# ── Plotting ──────────────────────────────────────────────────────────────────

def plot_reconstruction_errors(normal_errors: np.ndarray,
                                faulted_dict: dict[str, np.ndarray],
                                threshold:    float,
                                save_path:    str = None):
    """
    Distribution plot: normal errors vs faulted errors with threshold line.
    This is your key diagnostic chart — shows clear separation between
    normal and faulted reconstruction error distributions.
    """
    try:
        import matplotlib.pyplot as plt
        import matplotlib.patches as mpatches
    except ImportError:
        print("matplotlib not installed — skipping plot.")
        return

    fig, axes = plt.subplots(1, 2, figsize=(14, 5))

    # ── Left: error distribution by fault type ───────────────────────────────
    ax = axes[0]
    ax.hist(normal_errors, bins=60, alpha=0.7, label="Normal",
            color="steelblue", density=True)

    colors = ["tomato", "darkorange", "purple", "green", "brown", "crimson"]
    for (fault_name, errs), color in zip(faulted_dict.items(), colors):
        ax.hist(errs, bins=40, alpha=0.6, label=fault_name,
                color=color, density=True)

    ax.axvline(threshold, color="black", linestyle="--",
               linewidth=1.5, label=f"Threshold ({threshold:.5f})")
    ax.set_xlabel("Reconstruction error (MSE)")
    ax.set_ylabel("Density")
    ax.set_title("Reconstruction error distribution")
    ax.legend(fontsize=8)
    ax.grid(True, alpha=0.3)

    # ── Right: time-series error for a mixed sequence ─────────────────────────
    ax2 = axes[1]
    # Concatenate normal + one fault type to show transition
    fault_name  = list(faulted_dict.keys())[0]
    fault_errs  = faulted_dict[fault_name]

    n_show = min(200, len(normal_errors))
    f_show = min(200, len(fault_errs))
    combined = np.concatenate([
        normal_errors[:n_show],
        fault_errs[:f_show]
    ])
    t = np.arange(len(combined))

    ax2.plot(t, combined, linewidth=0.8, color="steelblue", alpha=0.9)
    ax2.axhline(threshold, color="red", linestyle="--",
                linewidth=1.2, label="Alert threshold")
    ax2.axvspan(n_show, len(combined), alpha=0.08, color="red",
                label=f"Fault: {fault_name}")
    ax2.set_xlabel("Window index (time →)")
    ax2.set_ylabel("Reconstruction error (MSE)")
    ax2.set_title("Error over time — normal then faulted")
    ax2.legend(fontsize=9)
    ax2.grid(True, alpha=0.3)

    plt.tight_layout()
    if save_path:
        plt.savefig(save_path, dpi=150)
        print(f"Plot saved → {save_path}")
    else:
        plt.show()


def plot_training_history(history_json: str, save_path: str = None):
    try:
        import matplotlib.pyplot as plt
        import json as _json
    except ImportError:
        return

    with open(history_json) as f:
        hist = _json.load(f)

    epochs = range(1, len(hist["loss"]) + 1)
    fig, ax = plt.subplots(figsize=(8, 4))
    ax.plot(epochs, hist["loss"],     label="Train loss")
    ax.plot(epochs, hist["val_loss"], label="Val loss")
    ax.set_xlabel("Epoch")
    ax.set_ylabel("MSE (reconstruction)")
    ax.set_title("Autoencoder training loss")
    ax.legend()
    ax.grid(True, alpha=0.3)
    plt.tight_layout()
    if save_path:
        plt.savefig(save_path, dpi=150)
        print(f"Plot saved → {save_path}")
    else:
        plt.show()


# avoid circular import when plotting
try:
    import pandas as pd
except ImportError:
    pass
