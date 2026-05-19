"""
point_builder.py
Converts raw MQTT JSON payloads into InfluxDB Point objects.

Schema design:
  Each MQTT topic maps to one InfluxDB measurement.
  Numeric values  → fields  (queryable, plottable)
  String/bool     → tags    (indexed, filterable)
  Timestamp       → server time (nanosecond precision)

Special handling per topic:
  ev/battery         → adds soc_correction delta field if soc_predicted also seen
  ev/anomaly_alert   → subsystems dict is flattened to per-subsystem error fields
  ev/soh_predicted   → rul_cycles stored as integer field; confidence as tag
  ev/range           → active_mode as tag; all km values as fields
  ev/fault           → fault injection events logged for correlation

Flat field naming convention:
  Nested dicts (e.g. subsystems.battery.error) are flattened to
  subsystems_battery_error to satisfy InfluxDB's flat field model.
"""

import logging
import time
from influxdb_client import Point, WritePrecision

import config

log = logging.getLogger("point_builder")


def build_point(topic: str,
                payload: dict,
                session_id: str,
                ) -> list["Point"]:
    """
    Convert one MQTT payload dict into a list of InfluxDB Points.

    Most topics produce one Point. Some (anomaly_alert with subsystem
    breakdown) produce multiple Points for richer querying.

    Args:
        topic:      MQTT topic string  (e.g. "ev/battery")
        payload:    parsed JSON dict
        session_id: current session ID (e.g. "20260323_140000")

    Returns:
        list of influxdb_client.Point objects (may be empty on error)
    """
    topic_cfg = config.TOPIC_MAP.get(topic)
    if topic_cfg is None:
        return []

    measurement = topic_cfg["measurement"]
    base_tags   = dict(topic_cfg["tags"])
    base_tags["session"] = session_id

    ts = time.time_ns()   # nanosecond precision server timestamp

    try:
        if topic == "ev/anomaly":
            return _build_anomaly_alert_points(payload, base_tags, ts)
        elif topic == "ev/soh_predicted":
            return _build_soh_points(payload, base_tags, ts)
        else:
            return [_build_generic_point(measurement, payload, base_tags, ts)]
    except Exception as exc:
        log.warning(f"Failed to build point for {topic}: {exc}")
        return []


# ── Generic point builder ──────────────────────────────────────────────────────

def _build_generic_point(measurement: str,
                          payload: dict,
                          tags: dict,
                          ts_ns: int) -> Point:
    """
    Build a single InfluxDB point from a flat payload dict.

    Rules:
      - Keys in EXCLUDE_FIELDS are skipped entirely
      - Keys in TAG_FIELDS are added as tags (string values)
      - Numeric (int/float) values become fields
      - Booleans become int fields (1/0) — InfluxDB stores these efficiently
      - Nested dicts are flattened with underscore separator
    """
    p = Point(measurement).time(ts_ns, WritePrecision.NS)

    # Apply base tags
    for k, v in tags.items():
        if v:
            p.tag(k, str(v))

    flat = _flatten(payload)

    for key, val in flat.items():
        if key in config.EXCLUDE_FIELDS:
            continue

        if key in config.TAG_FIELDS:
            p.tag(key, str(val))
        elif isinstance(val, bool):
            p.field(key, int(val))
        elif isinstance(val, (int, float)):
            if val != val:    # NaN guard
                continue
            p.field(key, float(val))
        elif isinstance(val, str):
            # Strings become tags (low-cardinality) unless very long
            if len(val) < 64:
                p.tag(key, val)
    return p


# ── Specialist builders ────────────────────────────────────────────────────────

def _build_anomaly_alert_points(payload: dict,
                                  tags: dict,
                                  ts_ns: int) -> list[Point]:
    """
    Anomaly alert payloads contain a nested 'subsystems' dict.
    Flatten it into per-subsystem error fields on the main point,
    and also write one Point per flagged subsystem for easier querying.
    """
    points = []

    # Main alert point
    p = Point("anomaly_alert").time(ts_ns, WritePrecision.NS)
    for k, v in tags.items():
        if v:
            p.tag(k, str(v))

    p.tag("likely_fault",  str(payload.get("likely_fault", "unknown")))
    p.tag("severity",      str(payload.get("severity", "low")))
    p.field("global_error",  float(payload.get("global_error",  0.0)))
    p.field("threshold",     float(payload.get("threshold",     0.0)))
    p.field("error_ratio",   float(payload.get("error_ratio",   0.0)))
    p.field("alert",         int(payload.get("alert", False)))
    points.append(p)

    # Per-subsystem points for detailed drill-down
    subsystems = payload.get("subsystems") or {}
    for sys_name, sys_data in subsystems.items():
        if not isinstance(sys_data, dict):
            continue
        sp = Point("anomaly_subsystem").time(ts_ns, WritePrecision.NS)
        for k, v in tags.items():
            if v:
                sp.tag(k, str(v))
        sp.tag("subsystem",    sys_name)
        sp.tag("likely_fault", str(payload.get("likely_fault", "unknown")))
        sp.field("error",      float(sys_data.get("error",   0.0)))
        sp.field("flagged",    int(sys_data.get("flagged",   False)))
        points.append(sp)

    return points


def _build_soh_points(payload: dict,
                       tags: dict,
                       ts_ns: int) -> list[Point]:
    """
    SoH payloads have rul_cycles that might be '>2000' (a string).
    Convert it to -1 (unknown) for clean numeric storage.
    """
    clean = dict(payload)
    rul   = clean.get("rul_cycles")
    if isinstance(rul, str):
        clean["rul_cycles"] = -1     # '>2000' or None → -1 sentinel
    elif rul is None:
        clean["rul_cycles"] = -1

    return [_build_generic_point("soh_model", clean, tags, ts_ns)]


# ── Dict flattening ────────────────────────────────────────────────────────────

def _flatten(d: dict, prefix: str = "", sep: str = "_") -> dict:
    """
    Recursively flatten a nested dict.
    {"subsystems": {"battery": {"error": 0.01}}}
    → {"subsystems_battery_error": 0.01}
    """
    items = {}
    for key, val in d.items():
        new_key = f"{prefix}{sep}{key}" if prefix else key
        if isinstance(val, dict):
            items.update(_flatten(val, new_key, sep))
        else:
            items[new_key] = val
    return items
