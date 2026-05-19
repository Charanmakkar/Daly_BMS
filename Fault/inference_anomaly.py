"""
inference_anomaly.py
Live anomaly detection from Arduino MQTT stream.

Every second:
  1. Receive ev/battery + ev/motor MQTT messages
  2. Push into rolling window buffer (20 timesteps)
  3. Run Autoencoder reconstruction
  4. Compute global MSE + per-subsystem MSE
  5. If global MSE > threshold → publish alert to ev/anomaly

Published topics:
    ev/anomaly         — alert events with subsystem diagnosis
    ev/anomaly_score   — continuous reconstruction error (for dashboard)

Alert payload example:
    {
        "alert":         true,
        "severity":      "high",
        "global_error":  0.00412,
        "threshold":     0.00187,
        "subsystems": {
            "battery": {"error": 0.00501, "flagged": true},
            "motor":   {"error": 0.00081, "flagged": false},
            "derived": {"error": 0.00388, "flagged": true},
            "ratios":  {"error": 0.00612, "flagged": true}
        },
        "likely_fault":  "thermal_runaway",
        "tick":          3142
    }

Usage:
    python inference_anomaly.py
    python inference_anomaly.py --broker 192.168.1.100 --model_dir anomaly_output
"""

import argparse
import json
import collections
import numpy as np
import paho.mqtt.client as mqtt

DEFAULT_BROKER    = "localhost"
DEFAULT_PORT      = 1883
DEFAULT_MODEL_DIR = "anomaly_output"

TOPIC_BATTERY     = "ev/battery"
TOPIC_MOTOR       = "ev/motor"
TOPIC_ANOMALY     = "ev/anomaly"
TOPIC_SCORE       = "ev/anomaly_score"

# Fault signature heuristics for likely_fault diagnosis
# Map which subsystems being flagged → probable fault type
FAULT_SIGNATURE_MAP = [
    ({"battery", "derived", "ratios"},  "thermal_runaway"),
    ({"battery", "ratios"},             "overcurrent"),
    ({"battery"},                       "cell_short_or_overvoltage"),
    ({"motor", "ratios"},               "locked_rotor"),
    ({"motor"},                         "overspeed"),
    ({"derived"},                       "sensor_drift"),
]


def parse_args():
    p = argparse.ArgumentParser(description="Live anomaly detection")
    p.add_argument("--broker",    default=DEFAULT_BROKER)
    p.add_argument("--port",      type=int, default=DEFAULT_PORT)
    p.add_argument("--model_dir", default=DEFAULT_MODEL_DIR)
    p.add_argument("--verbose",   action="store_true",
                   help="Print every window's score, not just alerts")
    return p.parse_args()


# ── Anomaly detector ──────────────────────────────────────────────────────────

class AnomalyDetector:
    """
    Loads trained Autoencoder + thresholds and runs per-window inference.
    Maintains a rolling buffer for the sliding window.
    """

    def __init__(self, model_dir: str):
        self.model_dir  = model_dir
        self.model      = None
        self.scaler     = None
        self.thresholds = None
        self.buffer     = None
        self._load()

    def _load(self):
        import os, json, pickle
        import tensorflow as tf
        from anomaly_features import MinMaxScaler, WINDOW_SIZE

        weights = os.path.join(self.model_dir, "autoencoder_weights.keras")
        scaler  = os.path.join(self.model_dir, "anomaly_scaler.pkl")
        thr     = os.path.join(self.model_dir, "thresholds.json")

        for path, label in [(weights, "weights"), (scaler, "scaler"), (thr, "thresholds")]:
            if not os.path.exists(path):
                raise FileNotFoundError(
                    f"Missing {label}: {path}\nRun train_anomaly.py first."
                )

        print(f"[Anomaly] Loading model …")
        self.model      = tf.keras.models.load_model(weights)
        self.scaler     = MinMaxScaler.load(scaler)
        with open(thr) as f:
            self.thresholds = json.load(f)

        self.window_size = WINDOW_SIZE
        self.buffer      = collections.deque(maxlen=self.window_size)

        global_thr = self.thresholds["global_threshold"]
        print(f"[Anomaly] Ready.  Global threshold = {global_thr:.6f}  "
              f"Window = {self.window_size}s")

    def push(self, battery_msg: dict,
             motor_msg: dict = None,
             tick: int = 0) -> dict:
        """
        Push one reading into the buffer and return an analysis result.

        Returns dict with:
          - alert (bool)
          - severity ('none','low','medium','high')
          - global_error, threshold
          - subsystems breakdown
          - likely_fault (str)
        """
        entry = {
            "voltage":     float(battery_msg.get("voltage",      350.0)),
            "current":     float(battery_msg.get("current",        5.0)),
            "temperature": float(battery_msg.get("temperature",   25.0)),
            "power_kw":    float(battery_msg.get("power_kw",       0.0)),
            "soc":         float(battery_msg.get("soc",           95.0)),
            "speed_kmh":   float(motor_msg.get("speed_kmh",  0.0)) if motor_msg else 0.0,
            "torque_nm":   float(motor_msg.get("torque_nm",  0.0)) if motor_msg else 0.0,
            "rpm":         float(motor_msg.get("rpm",         0.0)) if motor_msg else 0.0,
        }
        self.buffer.append(entry)

        if len(self.buffer) < self.window_size:
            return {
                "alert":        False,
                "severity":     "none",
                "warming_up":   True,
                "buffer_fill":  len(self.buffer),
                "window_size":  self.window_size,
            }

        from anomaly_features import build_inference_window, FEATURE_COLS, FEATURE_GROUPS

        X = build_inference_window(
            list(self.buffer), self.scaler, self.window_size
        )
        if X is None:
            return {"alert": False, "severity": "none", "warming_up": True}

        # Reconstruct
        X_rec = self.model.predict(X, verbose=0)

        # Global error
        global_error  = float(np.mean((X_rec - X) ** 2))
        global_thresh = self.thresholds["global_threshold"]
        is_alert      = global_error > global_thresh

        # Per-subsystem errors and flags
        group_thresholds = self.thresholds.get("feature_groups", {})
        subsystems       = {}
        flagged_groups   = set()

        for group_name, group_feats in FEATURE_GROUPS.items():
            feat_idx = [FEATURE_COLS.index(f) for f in group_feats
                        if f in FEATURE_COLS]
            g_err  = float(np.mean(
                (X_rec[:, :, feat_idx] - X[:, :, feat_idx]) ** 2
            ))
            g_thr  = group_thresholds.get(group_name, {}).get("threshold", global_thresh)
            flagged = g_err > g_thr

            subsystems[group_name] = {
                "error":   round(g_err, 6),
                "flagged": flagged,
            }
            if flagged:
                flagged_groups.add(group_name)

        # Severity level
        error_ratio = global_error / max(global_thresh, 1e-10)
        if error_ratio > 5:
            severity = "high"
        elif error_ratio > 2:
            severity = "medium"
        elif is_alert:
            severity = "low"
        else:
            severity = "none"

        # Likely fault diagnosis from flagged subsystems
        likely_fault = _diagnose(flagged_groups) if is_alert else "none"

        result = {
            "alert":         is_alert,
            "severity":      severity,
            "global_error":  round(global_error,  6),
            "threshold":     round(global_thresh, 6),
            "error_ratio":   round(error_ratio,   2),
            "subsystems":    subsystems,
            "likely_fault":  likely_fault,
            "tick":          tick,
            "warming_up":    False,
        }
        return result


def _diagnose(flagged_groups: set) -> str:
    """Map a set of flagged subsystem names to a likely fault label."""
    for required_flags, fault_name in FAULT_SIGNATURE_MAP:
        if required_flags.issubset(flagged_groups):
            return fault_name
    if flagged_groups:
        return "unknown_anomaly"
    return "none"


# ── MQTT integration ──────────────────────────────────────────────────────────

class LiveAnomalyInference:
    def __init__(self, broker: str, port: int,
                 model_dir: str, verbose: bool):
        self.detector = AnomalyDetector(model_dir)
        self.broker   = broker
        self.port     = port
        self.verbose  = verbose
        self._motor   = {}
        self._tick    = 0
        self._client  = None
        self._alert_count = 0

    def start(self):
        self._client = mqtt.Client(client_id="anomaly_detector")
        self._client.on_connect = self._on_connect
        self._client.on_message = self._on_message
        print(f"\n[MQTT] Connecting to {self.broker}:{self.port} …")
        self._client.connect(self.broker, self.port, keepalive=60)
        self._client.loop_forever()

    def _on_connect(self, client, userdata, flags, rc):
        if rc == 0:
            client.subscribe(TOPIC_BATTERY)
            client.subscribe(TOPIC_MOTOR)
            print(f"[MQTT] Subscribed. Warming up for "
                  f"{self.detector.window_size} seconds …\n")

    def _on_message(self, client, userdata, msg):
        try:
            payload = json.loads(msg.payload.decode())
        except Exception:
            return

        if msg.topic == TOPIC_MOTOR:
            self._motor = payload
            return
        if msg.topic != TOPIC_BATTERY:
            return

        self._tick = int(payload.get("tick", self._tick + 1))
        result     = self.detector.push(payload, self._motor, self._tick)

        if result.get("warming_up"):
            return

        # Publish continuous score for dashboard
        score_payload = {
            "global_error":  result["global_error"],
            "threshold":     result["threshold"],
            "error_ratio":   result["error_ratio"],
            "alert":         result["alert"],
            "tick":          self._tick,
        }
        client.publish(TOPIC_SCORE, json.dumps(score_payload), qos=0)

        # Verbose: print every window
        if self.verbose:
            flag = "ALERT" if result["alert"] else "     "
            print(f"[{flag}] tick={self._tick:>5}  "
                  f"err={result['global_error']:.6f}  "
                  f"ratio={result['error_ratio']:.1f}x  "
                  f"fault={result['likely_fault']}")

        # Alert: print + publish
        if result["alert"]:
            self._alert_count += 1
            client.publish(TOPIC_ANOMALY, json.dumps(result), qos=1)

            sev    = result["severity"].upper()
            fault  = result["likely_fault"]
            ratio  = result["error_ratio"]
            flags  = [k for k, v in result["subsystems"].items() if v["flagged"]]
            print(
                f"\n  *** ANOMALY ALERT [{sev}] ***  tick={self._tick}  "
                f"#{self._alert_count}\n"
                f"  Error:     {result['global_error']:.6f}  "
                f"({ratio:.1f}× threshold)\n"
                f"  Diagnosis: {fault}\n"
                f"  Flagged:   {', '.join(flags) if flags else 'global'}\n"
            )
        elif not self.verbose and self._tick % 30 == 0:
            # Heartbeat: print score every 30 ticks when no alert
            print(f"[OK] tick={self._tick:>5}  "
                  f"err={result['global_error']:.6f}  "
                  f"({result['error_ratio']:.1f}× threshold)")


# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    args = parse_args()
    engine = LiveAnomalyInference(
        broker    = args.broker,
        port      = args.port,
        model_dir = args.model_dir,
        verbose   = args.verbose,
    )
    try:
        engine.start()
    except KeyboardInterrupt:
        print("\n[Anomaly Detection] Stopped.")


if __name__ == "__main__":
    main()
