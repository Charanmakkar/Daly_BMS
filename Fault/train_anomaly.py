"""
train_anomaly.py
End-to-end Autoencoder training, threshold calibration, and evaluation.

Usage:
    # Recommended: run multiple normal sessions, one CSV per session
    python train_anomaly.py --normal_csvs logs/normal_1.csv logs/normal_2.csv

    # With fault CSVs for evaluation (run fault_injector.py during recording)
    python train_anomaly.py \\
        --normal_csvs logs/normal_1.csv logs/normal_2.csv \\
        --fault_csvs  thermal_runaway=logs/fault_thermal.csv \\
                      overcurrent=logs/fault_overcurrent.csv

    # Synthesize faults automatically from normal data (no fault CSVs needed)
    python train_anomaly.py --normal_csvs logs/normal_1.csv --synthesize_faults

Outputs saved to anomaly_output/:
    autoencoder_weights.keras    ← load for inference
    anomaly_scaler.pkl           ← load for inference
    thresholds.json              ← global + per-group thresholds
    detection_results.csv        ← per-fault detection rates
    error_distribution.png
    training_loss.png
"""

import argparse
import os
import sys
import numpy as np


def parse_args():
    p = argparse.ArgumentParser(description="Train anomaly detection Autoencoder")
    p.add_argument("--normal_csvs",     nargs="+", required=True,
                   help="One or more normal-session CSV files (no faults)")
    p.add_argument("--fault_csvs",      nargs="*", default=[],
                   help="fault_type=path pairs, e.g. thermal_runaway=fault.csv")
    p.add_argument("--synthesize_faults", action="store_true",
                   help="Synthesize faults from normal data for evaluation")
    p.add_argument("--output_dir",      default="anomaly_output")
    p.add_argument("--window",          type=int,   default=20)
    p.add_argument("--epochs",          type=int,   default=80)
    p.add_argument("--batch",           type=int,   default=64)
    p.add_argument("--threshold_pct",   type=float, default=99.0,
                   help="Percentile for threshold calibration (default: 99)")
    p.add_argument("--no_plot",         action="store_true")
    return p.parse_args()


def main():
    args = parse_args()

    print("=" * 56)
    print("  Anomaly Detection — Autoencoder Training Pipeline")
    print("=" * 56)

    from anomaly_features import (
        load_normal_csv, build_sequences, MinMaxScaler, WINDOW_SIZE
    )
    from autoencoder import (
        build_autoencoder, train_autoencoder,
        calibrate_threshold, evaluate_detection,
        plot_reconstruction_errors, plot_training_history,
        reconstruct_errors,
    )
    from fault_data_generator import (
        synthesize_faults, load_fault_csvs, build_faulted_sequences
    )

    window = args.window

    # ── Step 1: Load and merge all normal CSV sessions ───────────────────────
    print(f"\n[1/6] Loading {len(args.normal_csvs)} normal session(s) …")
    import pandas as pd
    normal_dfs = []
    for csv_path in args.normal_csvs:
        if not os.path.exists(csv_path):
            print(f"  [WARNING] Not found: {csv_path} — skipping")
            continue
        df = load_normal_csv(csv_path)
        normal_dfs.append(df)

    if not normal_dfs:
        print("ERROR: No normal CSV files found. "
              "Run simulator.py --log to generate them.")
        sys.exit(1)

    normal_df = pd.concat(normal_dfs, ignore_index=True)
    normal_df = normal_df.sort_values("time_s").reset_index(drop=True)
    print(f"  Total normal rows: {len(normal_df):,}")

    # ── Step 2: Build sequences (train / val / test split by time) ───────────
    print(f"\n[2/6] Building sliding windows (window={window}) …")
    n = len(normal_df)
    train_df = normal_df.iloc[:int(n * 0.70)]
    val_df   = normal_df.iloc[int(n * 0.70):int(n * 0.85)]
    test_df  = normal_df.iloc[int(n * 0.85):]

    X_train, scaler = build_sequences(train_df, window_size=window, fit_scaler=True)
    X_val,   _      = build_sequences(val_df,   window_size=window,
                                      scaler=scaler, fit_scaler=False)
    X_test,  _      = build_sequences(test_df,  window_size=window,
                                      scaler=scaler, fit_scaler=False)

    os.makedirs(args.output_dir, exist_ok=True)
    scaler_path = os.path.join(args.output_dir, "anomaly_scaler.pkl")
    scaler.save(scaler_path)
    print(f"  Scaler saved → {scaler_path}")

    # ── Step 3: Build model ──────────────────────────────────────────────────
    print(f"\n[3/6] Building Autoencoder …")
    from anomaly_features import N_FEATURES
    model = build_autoencoder(window_size=window, n_features=N_FEATURES)
    model.summary()

    # ── Step 4: Train ────────────────────────────────────────────────────────
    print(f"\n[4/6] Training …")
    train_autoencoder(model, X_train, X_val,
                      output_dir=args.output_dir,
                      epochs=args.epochs,
                      batch_size=args.batch)

    # ── Step 5: Calibrate threshold ──────────────────────────────────────────
    print(f"\n[5/6] Calibrating detection threshold …")
    thresholds = calibrate_threshold(
        model, X_val,
        percentile=args.threshold_pct,
        output_dir=args.output_dir,
    )

    # ── Step 6: Evaluate on faulted data ────────────────────────────────────
    print(f"\n[6/6] Evaluating fault detection …")

    # Parse --fault_csvs  "fault_name=path" pairs
    fault_csv_paths = {}
    for item in args.fault_csvs:
        if "=" not in item:
            print(f"  [WARNING] Invalid fault CSV format: '{item}'. "
                  f"Expected fault_name=path")
            continue
        name, path = item.split("=", 1)
        fault_csv_paths[name.strip()] = path.strip()

    faulted_dfs = {}
    if fault_csv_paths:
        faulted_dfs.update(load_fault_csvs(fault_csv_paths))

    if args.synthesize_faults or not faulted_dfs:
        print("  Synthesizing fault signatures from normal test data …")
        faulted_dfs.update(synthesize_faults(test_df.reset_index(drop=True)))

    faulted_seqs = build_faulted_sequences(
        faulted_dfs, scaler=scaler, window_size=window
    )

    # Get normal test errors for evaluation
    normal_errors, _ = reconstruct_errors(model, X_test)
    faulted_errors   = {}
    for name, X_f in faulted_seqs.items():
        errs, _ = reconstruct_errors(model, X_f)
        faulted_errors[name] = errs

    result_df = evaluate_detection(
        model, X_test, faulted_seqs, thresholds,
        output_dir=args.output_dir
    )

    print("\n  Detection summary:")
    print(result_df[["fault_type", "detection_rate",
                      "false_pos_rate", "mean_error"]].to_string(index=False))

    # ── Plots ─────────────────────────────────────────────────────────────────
    if not args.no_plot:
        plot_reconstruction_errors(
            normal_errors, faulted_errors,
            threshold=thresholds["global_threshold"],
            save_path=os.path.join(args.output_dir, "error_distribution.png"),
        )
        plot_training_history(
            os.path.join(args.output_dir, "training_history.json"),
            save_path=os.path.join(args.output_dir, "training_loss.png"),
        )

    print(f"\nAll outputs saved to: {args.output_dir}/")
    print("Training complete.")


if __name__ == "__main__":
    main()
