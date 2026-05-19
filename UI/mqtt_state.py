"""
mqtt_state.py
Thread-safe shared state fed by a background MQTT subscriber.

All four Streamlit pages import this module and read from `AppState`.
The MQTT thread writes to it; Streamlit reads from it on every rerun.

Topics consumed:
    ev/battery        → raw battery telemetry from Arduino
    ev/motor          → motor / speed telemetry
    ev/gps            → GPS position and elevation
    ev/soc_predicted  → LSTM-corrected SoC from inference.py
    ev/soh_predicted  → XGBoost SoH + RUL from inference_soh.py
    ev/anomaly_score  → continuous reconstruction error
    ev/anomaly        → alert events (only when fault detected)
    ev/range          → range estimate from inference_range.py
    ev/status         → quick-view summary from Arduino
"""

import json
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Optional
import ssl

import paho.mqtt.client as mqtt


# ── Configuration ─────────────────────────────────────────────────────────────
MQTT_BROKER         = "4927161c6b0c474a9aa19d86178cf2b1.s1.eu.hivemq.cloud"
MQTT_PORT           = 8883
DEFAULT_MODEL_DIR   = "model_output"
WINDOW_SIZE         = 30
TOPIC_BATTERY       = "ev/battery"
TOPIC_MOTOR         = "ev/motor"
TOPIC_SOC_PRED      = "ev/soc_predicted"
TOPIC_STATUS        = "ev/soc_status"

# ── HiveMQ Cloud credentials ──────────────────────────────────────────────────
MQTT_USERNAME     = "bms_data"
MQTT_PASSWORD     = "Praveen@81433"
MQTT_USE_TLS      = True
HISTORY_LEN       = 300          # 5 minutes of 1-Hz data kept in memory
ALERT_HISTORY_LEN = 50           # last 50 fault alerts

TOPICS = [
    "ev/battery",
    "ev/motor",
    "ev/gps",
    "ev/soc_predicted",
    "ev/soh_predicted",
    "ev/anomaly_score",
    "ev/anomaly",
    "ev/range",
    "ev/status",
]


# ── Thread-safe rolling buffer ─────────────────────────────────────────────────

class SafeDeque:
    """A deque protected by a lock for cross-thread access."""
    def __init__(self, maxlen: int):
        self._dq   = deque(maxlen=maxlen)
        self._lock = threading.Lock()

    def append(self, item):
        with self._lock:
            self._dq.append(item)

    def snapshot(self) -> list:
        """Return a shallow copy — safe to iterate in Streamlit."""
        with self._lock:
            return list(self._dq)

    def latest(self):
        with self._lock:
            return self._dq[-1] if self._dq else None

    def __len__(self):
        with self._lock:
            return len(self._dq)


# ── Application state ──────────────────────────────────────────────────────────

class AppState:
    """
    Singleton shared state — import and use `state` at module level.

    Streamlit pages call state.battery_hist.snapshot() etc. to get
    the latest data without blocking the MQTT thread.
    """
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._init()
        return cls._instance

    def _init(self):
        # Rolling time-series histories
        self.battery_hist      = SafeDeque(HISTORY_LEN)
        self.motor_hist        = SafeDeque(HISTORY_LEN)
        self.gps_hist          = SafeDeque(HISTORY_LEN)
        self.soc_pred_hist     = SafeDeque(HISTORY_LEN)
        self.anomaly_score_hist = SafeDeque(HISTORY_LEN)
        self.range_hist        = SafeDeque(HISTORY_LEN)

        # Latest single values (fast access for gauges)
        self._lock             = threading.Lock()
        self._latest           = {}

        # Alert log
        self.alert_log         = SafeDeque(ALERT_HISTORY_LEN)

        # SoH history (per cycle — slower cadence)
        self.soh_cycle_hist    = SafeDeque(200)

        # Connection status
        self.connected         = False
        self.last_message_ts   = 0.0
        self.message_count     = 0

    def update(self, topic: str, payload: dict):
        """Called by MQTT thread — updates appropriate buffers."""
        ts = time.time()
        self.last_message_ts = ts
        self.message_count  += 1

        payload["_ts"] = ts  # add wall-clock timestamp

        if topic == "ev/battery":
            self.battery_hist.append(payload)
            self._set("battery", payload)

        elif topic == "ev/motor":
            self.motor_hist.append(payload)
            self._set("motor", payload)

        elif topic == "ev/gps":
            self.gps_hist.append(payload)
            self._set("gps", payload)

        elif topic == "ev/soc_predicted":
            self.soc_pred_hist.append(payload)
            self._set("soc_pred", payload)

        elif topic == "ev/soh_predicted":
            self.soh_cycle_hist.append(payload)
            self._set("soh_pred", payload)

        elif topic == "ev/anomaly_score":
            self.anomaly_score_hist.append(payload)
            self._set("anomaly_score", payload)

        elif topic == "ev/anomaly":
            self.alert_log.append(payload)
            self._set("last_alert", payload)

        elif topic == "ev/range":
            self.range_hist.append(payload)
            self._set("range", payload)

        elif topic == "ev/status":
            self._set("status", payload)

    def _set(self, key: str, value: dict):
        with self._lock:
            self._latest[key] = value

    def get(self, key: str) -> Optional[dict]:
        with self._lock:
            return self._latest.get(key)

    def is_stale(self, max_age_s: float = 5.0) -> bool:
        """True if no message received recently — connection may be lost."""
        return (time.time() - self.last_message_ts) > max_age_s


# ── MQTT client ────────────────────────────────────────────────────────────────

class MQTTManager:
    """
    Manages the MQTT connection in a background daemon thread.
    Call start() once at app startup (guarded by st.session_state).
    """
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._started = False
            cls._instance._client  = None
        return cls._instance

    def start(self, broker: str = MQTT_BROKER, port: int = MQTT_PORT):
        if self._started:
            return
        self._started = True
        self._state   = AppState()

        thread = threading.Thread(
            target=self._run, args=(broker, port),
            daemon=True, name="mqtt-thread"
        )
        print(f"[MQTT] Starting MQTT thread to connect to {broker}:{port} …")
        thread.start()

    def _run(self, broker: str, port: int):
        print(f"[MQTT] MQTT thread running. Connecting to {broker}:{port} …")
        client = mqtt.Client(client_id="dashboard_subscriber")
        print("[MQTT] MQTT client created.")
        print(f"[MQTT] Setting username and password for HiveMQ Cloud: {MQTT_USERNAME}")
        client.username_pw_set(MQTT_USERNAME, MQTT_PASSWORD)
        if MQTT_USE_TLS:
            print("[MQTT] Enabling TLS for HiveMQ Cloud.")
            client.tls_set(tls_version=ssl.PROTOCOL_TLS_CLIENT)
            client.tls_insecure_set(False)
        

        client.on_connect    = self._on_connect
        client.on_message    = self._on_message
        client.on_disconnect = self._on_disconnect

        retry_delay = 2
        while True:
            try:
                client.connect(broker, port, keepalive=30)
                print(f"[MQTT] Connected with client ID: {client._client_id.decode()}")
                self._client = client
                client.loop_forever()
            except Exception as exc:
                self._state.connected = False
                print(f"[MQTT] Connection failed: {exc}. "
                      f"Retrying in {retry_delay}s …")
                time.sleep(retry_delay)
                retry_delay = min(retry_delay * 2, 10)

    def _on_connect(self, client, userdata, flags, rc):
        print(f"[MQTT] Connection result: {mqtt.connack_string(rc)} (code {rc})")
        if rc == 0:
            AppState().connected = True
            for topic in TOPICS:
                client.subscribe(topic, qos=0)
            print(f"[MQTT] Connected. Subscribed to {len(TOPICS)} topics.")
        else:
            AppState().connected = False

    def _on_message(self, client, userdata, msg):
        try:
            payload = json.loads(msg.payload.decode())
            AppState().update(msg.topic, payload)
        except Exception:
            pass

    def _on_disconnect(self, client, userdata, rc):
        AppState().connected = False
        if rc != 0:
            print("[MQTT] Unexpected disconnect — reconnecting …")


# Module-level singletons — import these in each page
state   = AppState()
manager = MQTTManager()
