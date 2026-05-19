"""
config.py
Central configuration for the MQTT → InfluxDB subscriber.

Edit INFLUX_* and MQTT_* values to match your setup.

InfluxDB free cloud tier:
  1. Sign up at https://cloud2.influxdata.com/signup
  2. Create a bucket called "ev_twin"
  3. Generate an API token (Data → API Tokens → Generate)
  4. Copy your organisation name from the account settings
  5. Paste all three values below

Local InfluxDB (Docker):
  docker run -d -p 8086:8086 --name influxdb influxdb:2
  Then open http://localhost:8086 and complete setup wizard.
  Use INFLUX_URL = "http://localhost:8086"
"""

# ── MQTT ───────────────────────────────────────────────────────────────────────
MQTT_BROKER     = "4927161c6b0c474a9aa19d86178cf2b1.s1.eu.hivemq.cloud"
MQTT_PORT       = 8883
MQTT_CLIENT_ID  = "influxdb_subscriber"
MQTT_USERNAME     = "bms_data"
MQTT_PASSWORD     = "Praveen@81433"
MQTT_USE_TLS      = True

# ── InfluxDB ───────────────────────────────────────────────────────────────────
INFLUX_URL      = "https://us-east-1-1.aws.cloud2.influxdata.com"          # or your cloud URL
INFLUX_TOKEN    = "laUt56G_syn-M4WHboxy-dTag3tf299RhiALdsJs5HUs2hLQT0nTmJUe3IJBtQUyboZFvJPqAAZzvCIcRJkMjg=="       # replace with real token
INFLUX_ORG      = "Digital_Twin"                  # replace with org name
INFLUX_BUCKET   = "ev_twin"

# ── Write settings ─────────────────────────────────────────────────────────────
WRITE_INTERVAL_MS   = 1000      # flush buffer to InfluxDB every N ms
BATCH_SIZE          = 50        # max points per write call
RETRY_MAX           = 5         # retries on write failure
RETRY_BACKOFF_S     = 2         # seconds between retries

# ── Topics to subscribe ────────────────────────────────────────────────────────
# Maps MQTT topic → (measurement name, tag set)
# All numeric fields in the JSON payload are written as fields.
# String/bool fields are written as tags.
TOPIC_MAP = {
    "ev/battery": {
        "measurement": "battery",
        "tags": {"vehicle_id": "ev_001", "session": ""},   # session filled at runtime
    },
    "ev/motor": {
        "measurement": "motor",
        "tags": {"vehicle_id": "ev_001", "session": ""},
    },
    "ev/gps": {
        "measurement": "gps",
        "tags": {"vehicle_id": "ev_001", "session": ""},
    },
    "ev/soc_predicted": {
        "measurement": "soc_model",
        "tags": {"vehicle_id": "ev_001", "model": "lstm", "session": ""},
    },
    "ev/soh_predicted": {
        "measurement": "soh_model",
        "tags": {"vehicle_id": "ev_001", "model": "xgboost", "session": ""},
    },
    "ev/anomaly_score": {
        "measurement": "anomaly",
        "tags": {"vehicle_id": "ev_001", "model": "autoencoder", "session": ""},
    },
    "ev/anomaly": {
        "measurement": "anomaly_alert",
        "tags": {"vehicle_id": "ev_001", "session": ""},
    },
    "ev/range": {
        "measurement": "range_model",
        "tags": {"vehicle_id": "ev_001", "model": "xgboost_range", "session": ""},
    },
    "ev/status": {
        "measurement": "status",
        "tags": {"vehicle_id": "ev_001", "session": ""},
    },
    "ev/fault": {
        "measurement": "fault_injected",
        "tags": {"vehicle_id": "ev_001", "session": ""},
    },
}

# ── Fields to exclude from InfluxDB writes ────────────────────────────────────
# These are either too large, always-null, or not useful for time-series queries
EXCLUDE_FIELDS = {
    "tick",        # use InfluxDB timestamp instead
    "_ts",         # internal dashboard timestamp
    "timestamp",   # ISO string from Arduino — use server timestamp
}

# ── Field type overrides ───────────────────────────────────────────────────────
# Fields listed here are written as TAGS (indexed strings) instead of fields.
# Keep this list short — too many tags degrades InfluxDB performance.
TAG_FIELDS = {
    "fault_type",
    "fault_active",
    "is_regen",
    "active_mode",
    "confidence",
    "likely_fault",
    "severity",
    "command",
    "target",
    "type",
}
