"""
pages/1_Live_Telemetry.py
Real-time battery and motor telemetry — gauges + rolling charts.
"""

import time
import streamlit as st
import pandas as pd
import plotly.graph_objects as go
import plotly.express as px
from plotly.subplots import make_subplots
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from mqtt_state import state, manager

st.set_page_config(page_title="Live Telemetry", page_icon="📡", layout="wide")

# ── Sidebar connection guard ───────────────────────────────────────────────────
with st.sidebar:
    st.title("⚡ EV Digital Twin")
    if "mqtt_started" not in st.session_state or not st.session_state.mqtt_started:
        st.warning("Go to Home page and connect first.")
    auto_refresh = st.toggle("Auto-refresh (1s)", value=True, key="live_refresh")

st.title("📡 Live Telemetry")
st.caption("Battery and motor data streaming from Arduino UNO R4 WiFi")

# ── Pull latest data ───────────────────────────────────────────────────────────
bat  = state.get("battery") or {}
mot  = state.get("motor")   or {}
soc_pred = state.get("soc_pred") or {}

bat_hist  = pd.DataFrame(state.battery_hist.snapshot())
mot_hist  = pd.DataFrame(state.motor_hist.snapshot())

# ── Gauge row ──────────────────────────────────────────────────────────────────
st.subheader("Live gauges")
g1, g2, g3, g4 = st.columns(4)

def gauge(title, value, min_val, max_val, unit,
          threshold_warn=None, threshold_crit=None):
    """Build a Plotly gauge figure."""
    color = "#2ecc71"
    if threshold_crit and value >= threshold_crit:
        color = "#e74c3c"
    elif threshold_warn and value >= threshold_warn:
        color = "#f39c12"

    steps = [{"range": [min_val, max_val], "color": "#f0f0f0"}]
    if threshold_warn:
        steps = [
            {"range": [min_val, threshold_warn],              "color": "#d5f5e3"},
            {"range": [threshold_warn, threshold_crit or max_val], "color": "#fef9e7"},
        ]
        if threshold_crit:
            steps.append({"range": [threshold_crit, max_val], "color": "#fadbd8"})

    fig = go.Figure(go.Indicator(
        mode="gauge+number",
        value=value,
        title={"text": f"{title}<br><span style='font-size:0.8em'>{unit}</span>"},
        gauge={
            "axis":  {"range": [min_val, max_val]},
            "bar":   {"color": color, "thickness": 0.25},
            "steps": steps,
            "threshold": {
                "line":  {"color": "#e74c3c", "width": 3},
                "thickness": 0.8,
                "value": threshold_crit or max_val * 0.9,
            },
        },
        number={"suffix": f" {unit}", "font": {"size": 22}},
    ))
    fig.update_layout(height=200, margin=dict(t=40, b=10, l=20, r=20))
    return fig

soc_v  = soc_pred.get("predicted_soc") or bat.get("soc") or 0.0
temp_v = bat.get("temperature") or 25.0
spd_v  = mot.get("speed_kmh") or 0.0
pwr_v  = bat.get("power_kw") or 0.0

g1.plotly_chart(gauge("State of Charge", soc_v,  0, 100, "%",
                       threshold_warn=20, threshold_crit=10), use_container_width=True)
g2.plotly_chart(gauge("Temperature",     temp_v, 0,  80, "°C",
                       threshold_warn=40, threshold_crit=55), use_container_width=True)
g3.plotly_chart(gauge("Speed",           spd_v,  0, 150, "km/h"), use_container_width=True)
g4.plotly_chart(gauge("Power draw",      abs(pwr_v), 0, 250, "kW",
                       threshold_warn=180, threshold_crit=220), use_container_width=True)

st.divider()

# ── Instant metrics row ────────────────────────────────────────────────────────
st.subheader("Instantaneous values")
c1, c2, c3, c4, c5, c6 = st.columns(6)
c1.metric("Voltage",    f"{bat.get('voltage', '—'):.1f} V"   if bat.get('voltage')    else "—")
c2.metric("Current",    f"{bat.get('current', '—'):.1f} A"   if bat.get('current')    else "—")
c3.metric("LSTM SoC",   f"{soc_pred.get('predicted_soc', '—'):.1f}%" if soc_pred.get('predicted_soc') else "—")
c4.metric("Torque",     f"{mot.get('torque_nm', '—'):.1f} Nm" if mot.get('torque_nm') else "—")
c5.metric("RPM",        f"{mot.get('rpm', '—'):.0f}"          if mot.get('rpm')        else "—")
c6.metric("Regen",      "Yes ♻" if mot.get("is_regen") else "No")

st.divider()

# ── Rolling time-series charts ─────────────────────────────────────────────────
st.subheader("Rolling 5-minute history")

if len(bat_hist) > 5:
    bat_hist["t"] = range(len(bat_hist))

    # Battery charts — 2×2 grid
    fig = make_subplots(rows=2, cols=2,
                        subplot_titles=["Voltage (V)",
                                        "Current (A)",
                                        "Temperature (°C)",
                                        "Power (kW)"],
                        vertical_spacing=0.15,
                        horizontal_spacing=0.08)

    kw = dict(mode="lines", line=dict(width=1.5))
    fig.add_trace(go.Scatter(x=bat_hist["t"], y=bat_hist.get("voltage", []),
                             name="Voltage",     line_color="#3498db", **kw),
                  row=1, col=1)
    fig.add_trace(go.Scatter(x=bat_hist["t"], y=bat_hist.get("current", []),
                             name="Current",     line_color="#e67e22", **kw),
                  row=1, col=2)
    fig.add_trace(go.Scatter(x=bat_hist["t"], y=bat_hist.get("temperature", []),
                             name="Temperature", line_color="#e74c3c", **kw),
                  row=2, col=1)
    fig.add_trace(go.Scatter(x=bat_hist["t"], y=bat_hist.get("power_kw", []),
                             name="Power",       line_color="#9b59b6", **kw),
                  row=2, col=2)

    # Temp threshold line
    if "temperature" in bat_hist.columns:
        fig.add_hline(y=45, line_dash="dash", line_color="#e74c3c",
                      annotation_text="Warn 45°C", row=2, col=1)

    # LSTM SoC overlay on voltage chart
    soc_hist = pd.DataFrame(state.soc_pred_hist.snapshot())
    if len(soc_hist) > 5 and "predicted_soc" in soc_hist.columns:
        soc_hist["t"] = range(len(soc_hist))
        fig.add_trace(
            go.Scatter(x=soc_hist["t"],
                       y=soc_hist["predicted_soc"],
                       name="LSTM SoC %",
                       mode="lines",
                       line=dict(color="#2ecc71", width=1.5, dash="dot"),
                       yaxis="y5"),
            row=1, col=1
        )

    fig.update_layout(height=400, showlegend=True,
                      margin=dict(t=40, b=20, l=40, r=20),
                      legend=dict(orientation="h", y=-0.12))
    st.plotly_chart(fig, use_container_width=True)

else:
    st.info("Waiting for data… (need at least 5 seconds of history)")

# ── Motor speed + regen chart ──────────────────────────────────────────────────
if len(mot_hist) > 5:
    mot_hist["t"] = range(len(mot_hist))
    fig2 = go.Figure()
    fig2.add_trace(go.Scatter(
        x=mot_hist["t"], y=mot_hist.get("speed_kmh", []),
        name="Speed km/h", fill="tozeroy",
        line=dict(color="#3498db", width=1.5)
    ))
    if "is_regen" in mot_hist.columns:
        regen_mask = mot_hist["is_regen"].astype(bool)
        fig2.add_trace(go.Scatter(
            x=mot_hist.loc[regen_mask, "t"],
            y=mot_hist.loc[regen_mask, "speed_kmh"],
            mode="markers",
            name="Regen ♻",
            marker=dict(color="#2ecc71", size=5, symbol="circle")
        ))
    fig2.update_layout(title="Vehicle speed with regen markers",
                       height=220,
                       margin=dict(t=40, b=20, l=40, r=20),
                       yaxis_title="km/h")
    st.plotly_chart(fig2, use_container_width=True)

# ── Auto-refresh ───────────────────────────────────────────────────────────────
if auto_refresh:
    time.sleep(1)
    st.rerun()
