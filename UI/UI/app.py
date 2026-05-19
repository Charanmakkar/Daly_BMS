"""
app.py  —  EV Digital Twin Dashboard (Streamlit)
Main entry point. Run with:  streamlit run app.py

Pages:
  app.py                 →  Home / system overview
  pages/1_Live.py        →  Real-time telemetry gauges + charts
  pages/2_Battery.py     →  SoC trend, SoH degradation, RUL
  pages/3_Anomaly.py     →  Reconstruction error feed + fault log
  pages/4_Range.py       →  Dual-mode range prediction

Layout: sidebar with connection config + status badge.
        Main area changes per page.
"""

import time
import streamlit as st
import pandas as pd

from mqtt_state import state, manager

# ── Page config (must be first Streamlit call) ─────────────────────────────────
st.set_page_config(
    page_title="EV Digital Twin",
    page_icon="⚡",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── Custom CSS ─────────────────────────────────────────────────────────────────
st.markdown("""
<style>
  .metric-card {
    background: var(--secondary-background-color);
    border-radius: 12px;
    padding: 16px 20px;
    margin-bottom: 8px;
  }
  .alert-high   { border-left: 4px solid #e74c3c; padding-left: 12px; }
  .alert-medium { border-left: 4px solid #f39c12; padding-left: 12px; }
  .alert-low    { border-left: 4px solid #3498db; padding-left: 12px; }
  .status-ok    { color: #2ecc71; font-weight: 600; }
  .status-warn  { color: #f39c12; font-weight: 600; }
  .status-error { color: #e74c3c; font-weight: 600; }
</style>
""", unsafe_allow_html=True)


# ── Sidebar — connection config ────────────────────────────────────────────────
with st.sidebar:
    st.title("⚡ EV Digital Twin")
    st.caption("Capstone Project Dashboard")
    st.divider()

    # Connection settings
    st.subheader("MQTT Connection")
    broker = st.text_input("Broker IP", value="localhost", key="broker_ip")
    port   = st.number_input("Port", value=1883, min_value=1, max_value=65535,
                              key="broker_port")

    # Start MQTT (only once per session)
    if "mqtt_started" not in st.session_state:
        st.session_state.mqtt_started = False

    if not st.session_state.mqtt_started:
        if st.button("Connect", type="primary", use_container_width=True):
            manager.start(broker=broker, port=port)
            st.session_state.mqtt_started = True
            st.rerun()
    else:
        if state.connected and not state.is_stale():
            st.markdown('<p class="status-ok">● Connected</p>',
                        unsafe_allow_html=True)
        elif state.is_stale(max_age_s=10):
            st.markdown('<p class="status-warn">● No data (stale)</p>',
                        unsafe_allow_html=True)
        else:
            st.markdown('<p class="status-error">● Disconnected</p>',
                        unsafe_allow_html=True)

    st.divider()

    # Live status snapshot
    st.subheader("System snapshot")
    status = state.get("status") or {}
    bat    = state.get("battery") or {}
    motor  = state.get("motor") or {}
    soc_p  = state.get("soc_pred") or {}
    rng    = state.get("range") or {}

    soc_val  = soc_p.get("predicted_soc") or bat.get("soc", "—")
    soh_val  = state.get("soh_pred") or {}

    col1, col2 = st.columns(2)
    col1.metric("SoC",   f"{soc_val:.1f}%" if isinstance(soc_val, float) else soc_val)
    col2.metric("SoH",   f"{soh_val.get('soh_pct', '—')}%" if soh_val else "—")
    col1.metric("Speed", f"{motor.get('speed_kmh', '—')} km/h" if motor else "—")
    col2.metric("Range", f"{rng.get('range_km', '—')} km" if rng else "—")

    # Fault indicator
    last_alert = state.get("last_alert")
    if last_alert and not state.is_stale(max_age_s=30):
        st.warning(f"⚠ {last_alert.get('likely_fault', 'FAULT')} detected")

    st.divider()
    st.caption(f"Messages: {state.message_count:,}")
    if state.last_message_ts:
        age = time.time() - state.last_message_ts
        st.caption(f"Last update: {age:.1f}s ago")

    # Auto-refresh toggle
    auto_refresh = st.toggle("Auto-refresh (1s)", value=True, key="auto_refresh")


# ── Home page ──────────────────────────────────────────────────────────────────
st.title("EV Digital Twin — System Overview")
st.caption("Real-time monitoring of battery health, range, and fault detection")

if not st.session_state.mqtt_started:
    st.info("👈 Enter your MQTT broker IP and click **Connect** to start.")
    st.divider()

# ── Architecture card ──────────────────────────────────────────────────────────
st.subheader("Pipeline overview")

col1, col2, col3, col4 = st.columns(4)
with col1:
    st.markdown("""
    **Model 1 — SoC**
    LSTM · 1 Hz
    Corrects Coulomb
    counting drift
    """)
with col2:
    st.markdown("""
    **Model 2 — SoH / RUL**
    XGBoost · per cycle
    Capacity fade +
    remaining life
    """)
with col3:
    st.markdown("""
    **Model 3 — Anomaly**
    Autoencoder · 1 Hz
    Unsupervised fault
    detection
    """)
with col4:
    st.markdown("""
    **Model 4 — Range**
    XGBoost · 1 Hz
    Static + dynamic
    dual-mode estimate
    """)

st.divider()

# ── Live summary metrics ───────────────────────────────────────────────────────
st.subheader("Live metrics")

m1, m2, m3, m4, m5, m6 = st.columns(6)

# SoC
soc_lstm   = soc_p.get("predicted_soc")
soc_coulomb = bat.get("soc")
if soc_lstm is not None and soc_coulomb is not None:
    delta_soc = round(soc_lstm - soc_coulomb, 1)
    m1.metric("SoC (LSTM)",
              f"{soc_lstm:.1f}%",
              f"{delta_soc:+.1f}% vs Coulomb")
else:
    m1.metric("SoC", str(soc_coulomb or "—"))

# SoH
soh_now = soh_val.get("soh_pct") if soh_val else None
m2.metric("SoH", f"{soh_now:.1f}%" if soh_now else "—")

# RUL
rul_now = soh_val.get("rul_cycles") if soh_val else None
m3.metric("RUL", f"{rul_now} cycles" if rul_now else "—")

# Range
range_now = rng.get("range_km") if rng else None
m4.metric("Range",
          f"{range_now:.0f} km" if range_now else "—",
          rng.get("active_mode", "") if rng else "")

# Battery temp
temp_now = bat.get("temperature")
m5.metric("Bat temp",
          f"{temp_now:.1f}°C" if temp_now else "—",
          "⚠ High" if temp_now and temp_now > 45 else "Normal")

# Anomaly score
anm = state.get("anomaly_score") or {}
err = anm.get("global_error")
thr = anm.get("threshold")
if err is not None and thr is not None and thr > 0:
    ratio = err / thr
    m6.metric("Anomaly score",
              f"{ratio:.1f}×",
              "🔴 ALERT" if ratio > 1.0 else "✅ Normal")
else:
    m6.metric("Anomaly score", "—")

st.divider()

# ── Recent alerts ──────────────────────────────────────────────────────────────
st.subheader("Recent fault alerts")
alerts = state.alert_log.snapshot()
if alerts:
    for alert in reversed(alerts[-5:]):   # show 5 most recent
        sev   = alert.get("severity", "low")
        fault = alert.get("likely_fault", "unknown")
        tick  = alert.get("tick", "?")
        ratio = alert.get("error_ratio", 0)
        flags = [k for k, v in alert.get("subsystems", {}).items()
                 if v.get("flagged")]
        css_class = f"alert-{sev}"
        st.markdown(
            f'<div class="{css_class}"><strong>[{sev.upper()}]</strong> '
            f'Tick {tick} — <em>{fault}</em> — '
            f'{ratio:.1f}× threshold — subsystems: {", ".join(flags)}</div>',
            unsafe_allow_html=True
        )
else:
    st.success("✅ No fault alerts in this session")

# ── Auto-refresh ───────────────────────────────────────────────────────────────
if auto_refresh and st.session_state.mqtt_started:
    time.sleep(1)
    st.rerun()
