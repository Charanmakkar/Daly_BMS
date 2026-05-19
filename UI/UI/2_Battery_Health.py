"""
pages/2_Battery_Health.py
SoC trend (LSTM vs Coulomb), SoH degradation curve, RUL countdown.
"""

import time
import streamlit as st
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from mqtt_state import state

st.set_page_config(page_title="Battery Health", page_icon="🔋", layout="wide")

from queries import InfluxReader
reader = InfluxReader()
soh_all = reader.soh_degradation_all_sessions()
# → plot soh_all with Plotly

with st.sidebar:
    st.title("⚡ EV Digital Twin")
    auto_refresh = st.toggle("Auto-refresh (1s)", value=True, key="bat_refresh")

st.title("🔋 Battery Health")
st.caption("SoC estimation, SoH degradation, and Remaining Useful Life")

# ── Pull data ──────────────────────────────────────────────────────────────────
bat_hist      = pd.DataFrame(state.battery_hist.snapshot())
soc_pred_hist = pd.DataFrame(state.soc_pred_hist.snapshot())
soh_hist      = pd.DataFrame(state.soh_cycle_hist.snapshot())
soh_now       = state.get("soh_pred") or {}

# ── SoC comparison — LSTM vs Coulomb ──────────────────────────────────────────
st.subheader("SoC: LSTM correction vs Coulomb counting")

col1, col2, col3 = st.columns([1, 1, 1])
lstm_soc    = soh_now.get("soh_pct")   # reuse latest
soc_latest  = state.get("soc_pred") or {}
soc_lstm    = soc_latest.get("predicted_soc")
soc_coulomb = (state.get("battery") or {}).get("soc")
delta       = round(soc_lstm - soc_coulomb, 2) if soc_lstm and soc_coulomb else None

col1.metric("LSTM SoC",       f"{soc_lstm:.1f}%"    if soc_lstm    else "—",
            f"{delta:+.1f}% correction" if delta else "")
col2.metric("Coulomb SoC",    f"{soc_coulomb:.1f}%" if soc_coulomb else "—")
col3.metric("Correction Δ",   f"{delta:+.2f}%"      if delta       else "—",
            "LSTM leads" if delta and delta > 0 else "LSTM lags" if delta else "")

if len(bat_hist) > 5 and len(soc_pred_hist) > 5:
    bat_hist["t"]      = range(len(bat_hist))
    soc_pred_hist["t"] = range(len(soc_pred_hist))

    fig = go.Figure()
    if "soc" in bat_hist.columns:
        fig.add_trace(go.Scatter(
            x=bat_hist["t"], y=bat_hist["soc"],
            name="Coulomb counting",
            line=dict(color="#95a5a6", width=1.5, dash="dot")
        ))
    if "predicted_soc" in soc_pred_hist.columns:
        fig.add_trace(go.Scatter(
            x=soc_pred_hist["t"], y=soc_pred_hist["predicted_soc"],
            name="LSTM corrected",
            line=dict(color="#2ecc71", width=2.0)
        ))

    fig.add_hline(y=20, line_dash="dash", line_color="#f39c12",
                  annotation_text="Low (20%)")
    fig.add_hline(y=10, line_dash="dash", line_color="#e74c3c",
                  annotation_text="Critical (10%)")

    fig.update_layout(
        title="SoC over time — LSTM correction vs raw Coulomb counting",
        yaxis_title="SoC (%)", yaxis_range=[0, 105],
        height=280, margin=dict(t=40, b=20, l=40, r=20),
        legend=dict(orientation="h", y=-0.2)
    )
    st.plotly_chart(fig, use_container_width=True)

st.divider()

# ── SoH Degradation curve ─────────────────────────────────────────────────────
st.subheader("SoH degradation curve")

soh_pct  = soh_now.get("soh_pct")
rul      = soh_now.get("rul_cycles")
eol_cyc  = soh_now.get("projected_eol_cycle")
conf     = soh_now.get("confidence", "—")

c1, c2, c3, c4 = st.columns(4)
c1.metric("Current SoH",        f"{soh_pct:.2f}%"    if soh_pct  else "—")
c2.metric("Remaining cycles",   f"{rul}"              if rul      else "—")
c3.metric("Projected EOL",      f"Cycle {eol_cyc}"   if eol_cyc  else "—")
c4.metric("Confidence",         conf.capitalize()     if conf     else "—")

if len(soh_hist) > 2 and "soh_pct" in soh_hist.columns:
    soh_hist["cycle"] = range(len(soh_hist))

    fig2 = go.Figure()
    fig2.add_trace(go.Scatter(
        x=soh_hist["cycle"], y=soh_hist["soh_pct"],
        mode="lines+markers",
        name="Measured SoH",
        line=dict(color="#3498db", width=2.0),
        marker=dict(size=6)
    ))

    # RUL trendline: polynomial extrapolation if enough points
    import numpy as np
    if len(soh_hist) >= 5:
        cyc  = soh_hist["cycle"].values.astype(float)
        sohs = soh_hist["soh_pct"].values.astype(float)
        coeffs = np.polyfit(cyc, sohs, 2)
        future = np.arange(cyc[-1], cyc[-1] + max(200, rul or 100))
        trend  = np.poly1d(coeffs)(future)
        trend  = np.clip(trend, 70, 105)
        fig2.add_trace(go.Scatter(
            x=future, y=trend,
            name="Degradation trend",
            line=dict(color="#e74c3c", width=1.5, dash="dash"),
            opacity=0.7
        ))

    fig2.add_hline(y=80, line_dash="dash", line_color="#e74c3c",
                   annotation_text="EOL threshold (80%)",
                   annotation_position="bottom right")
    fig2.update_layout(
        title="Battery SoH over cycles — degradation tracking",
        xaxis_title="Cycle number",
        yaxis_title="State of Health (%)",
        yaxis_range=[70, 105],
        height=320,
        margin=dict(t=40, b=20, l=40, r=20),
        legend=dict(orientation="h", y=-0.2)
    )
    st.plotly_chart(fig2, use_container_width=True)

    # ── RUL countdown visual ─────────────────────────────────────────────────
    if rul is not None and isinstance(rul, int):
        st.subheader("Remaining useful life")
        progress = max(0.0, min(1.0, 1.0 - rul / max(rul + len(soh_hist), 1)))
        pct_used = round(progress * 100, 1)
        bar_color = "#2ecc71" if pct_used < 60 else "#f39c12" if pct_used < 85 else "#e74c3c"

        st.markdown(f"""
        <div style='margin: 8px 0 4px'>
          <strong>Battery life consumed: {pct_used:.1f}%</strong>
          &nbsp;·&nbsp; {rul} cycles remaining until EOL
        </div>
        <div style='background:#eee;border-radius:8px;height:20px;overflow:hidden'>
          <div style='width:{pct_used}%;background:{bar_color};height:100%;
                      border-radius:8px;transition:width 0.5s'></div>
        </div>
        """, unsafe_allow_html=True)
else:
    st.info("SoH data will appear after the first completed cycle "
            "(every 5 minutes by default).")

st.divider()

# ── Key battery parameters ────────────────────────────────────────────────────
st.subheader("Current battery parameters")
bat = state.get("battery") or {}
p1, p2, p3, p4 = st.columns(4)
p1.metric("Terminal voltage",   f"{bat.get('voltage', '—'):.2f} V"   if bat.get('voltage') else "—")
p2.metric("OCV",                f"{bat.get('ocv', '—'):.2f} V"       if bat.get('ocv')     else "—")
p3.metric("Internal resistance est.",
          f"{bat.get('r_internal_est', '—'):.4f} Ω"
          if bat.get("r_internal_est") else "—")
p4.metric("Effective capacity",
          f"{bat.get('capacity_ah', '—'):.2f} Ah"
          if bat.get("capacity_ah") else "—")

if auto_refresh:
    time.sleep(1)
    st.rerun()
