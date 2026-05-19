"""
pages/4_Range_Prediction.py
Dual-mode range estimate, static/dynamic blend indicator, trend chart.
"""

import time
import streamlit as st
import pandas as pd
import plotly.graph_objects as go
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from mqtt_state import state

st.set_page_config(page_title="Range Prediction", page_icon="🗺️", layout="wide")

with st.sidebar:
    st.title("⚡ EV Digital Twin")
    auto_refresh = st.toggle("Auto-refresh (1s)", value=True, key="rng_refresh")

st.title("🗺️ Range Prediction")
st.caption("Dual-mode XGBoost estimator — static at start, dynamic after 5 min")

# ── Pull data ──────────────────────────────────────────────────────────────────
rng_hist  = pd.DataFrame(state.range_hist.snapshot())
latest    = state.get("range") or {}
bat       = state.get("battery") or {}

range_km    = latest.get("range_km")
static_km   = latest.get("static_km")
dynamic_km  = latest.get("dynamic_km")
dyn_weight  = latest.get("dynamic_weight", 0.0)
sta_weight  = latest.get("static_weight",  1.0)
mode        = latest.get("active_mode", "—")
conf        = latest.get("confidence", "—")
cons        = latest.get("consumption_wh_km")
trend       = latest.get("trend_km_per_min")
elapsed     = latest.get("elapsed_s", 0)

# ── Primary range display ──────────────────────────────────────────────────────
col_main, col_side = st.columns([2, 1])

with col_main:
    if range_km is not None:
        # Big range number with mode badge
        mode_color = {"static": "#95a5a6",
                      "blending": "#f39c12",
                      "dynamic": "#2ecc71"}.get(mode, "#95a5a6")

        st.markdown(f"""
        <div style='text-align:center; padding: 24px;
                    background: var(--secondary-background-color);
                    border-radius: 16px; margin-bottom: 16px'>
          <div style='font-size: 72px; font-weight: 700;
                      line-height: 1.0; color: var(--text-color)'>
            {range_km:.0f}
          </div>
          <div style='font-size: 24px; color: var(--text-color);
                      opacity: 0.7; margin-bottom: 12px'>km remaining</div>
          <span style='background: {mode_color}; color: white;
                       padding: 4px 14px; border-radius: 20px;
                       font-size: 13px; font-weight: 600'>
            {mode.upper()} MODE
          </span>
        </div>
        """, unsafe_allow_html=True)
    else:
        st.info("Range estimator initialising — waiting for first reading …")

with col_side:
    st.metric("Consumption",   f"{cons:.0f} Wh/km" if cons else "—")
    st.metric("Confidence",    conf.capitalize()    if conf else "—")
    st.metric("Elapsed",       f"{elapsed}s"        if elapsed else "—")

    if trend is not None:
        arrow = "↓" if trend < -0.5 else "↑" if trend > 0.5 else "→"
        st.metric("Range trend", f"{arrow} {abs(trend):.1f} km/min")

st.divider()

# ── Static vs dynamic blend ────────────────────────────────────────────────────
st.subheader("Static / dynamic blend")
st.caption("Smoothly transitions from historical estimate → live data over 5 minutes")

blend_cols = st.columns([3, 1])
with blend_cols[0]:
    # Custom blend bar
    dyn_pct = round(dyn_weight * 100)
    sta_pct = 100 - dyn_pct
    st.markdown(f"""
    <div style='margin:8px 0 4px; font-size:13px'>
      Static ({sta_pct}%) ← Blending → Dynamic ({dyn_pct}%)
    </div>
    <div style='display:flex; border-radius:8px; overflow:hidden;
                height:24px; border: 1px solid rgba(0,0,0,0.1)'>
      <div style='width:{sta_pct}%; background:#95a5a6;
                  display:flex; align-items:center; justify-content:center;
                  font-size:11px; color:white; font-weight:600'>
        {"Static" if sta_pct > 15 else ""}
      </div>
      <div style='width:{dyn_pct}%; background:#2ecc71;
                  display:flex; align-items:center; justify-content:center;
                  font-size:11px; color:white; font-weight:600'>
        {"Dynamic" if dyn_pct > 15 else ""}
      </div>
    </div>
    """, unsafe_allow_html=True)

with blend_cols[1]:
    c1, c2 = st.columns(2)
    c1.metric("Static",  f"{static_km:.0f} km"  if static_km  else "—")
    c2.metric("Dynamic", f"{dynamic_km:.0f} km" if dynamic_km else "—")

st.divider()

# ── Range trend chart ──────────────────────────────────────────────────────────
st.subheader("Range estimate over time")

if len(rng_hist) > 5 and "range_km" in rng_hist.columns:
    rng_hist["t"] = range(len(rng_hist))

    fig = go.Figure()

    # Static estimate (flat or nearly flat line)
    if "static_km" in rng_hist.columns:
        fig.add_trace(go.Scatter(
            x=rng_hist["t"], y=rng_hist["static_km"],
            name="Static estimate",
            line=dict(color="#95a5a6", width=1.5, dash="dot"),
            opacity=0.7
        ))

    # Dynamic estimate
    if "dynamic_km" in rng_hist.columns:
        fig.add_trace(go.Scatter(
            x=rng_hist["t"], y=rng_hist["dynamic_km"],
            name="Dynamic estimate",
            line=dict(color="#2ecc71", width=1.5, dash="dash"),
            opacity=0.8
        ))

    # Final blended range (main line)
    fig.add_trace(go.Scatter(
        x=rng_hist["t"], y=rng_hist["range_km"],
        name="Blended range",
        line=dict(color="#3498db", width=2.5),
        fill="tozeroy",
        fillcolor="rgba(52,152,219,0.06)"
    ))

    # Shade regions by mode
    if "active_mode" in rng_hist.columns:
        for mode_name, color in [
            ("static",   "rgba(149,165,166,0.06)"),
            ("blending", "rgba(243,156,18,0.06)"),
            ("dynamic",  "rgba(46,204,113,0.06)"),
        ]:
            mask = rng_hist["active_mode"] == mode_name
            if mask.any():
                mode_rows = rng_hist[mask]
                fig.add_trace(go.Scatter(
                    x=list(mode_rows["t"]) + list(mode_rows["t"])[::-1],
                    y=list(mode_rows["range_km"] + 5) + list(mode_rows["range_km"] - 5)[::-1],
                    fill="toself",
                    fillcolor=color,
                    line=dict(width=0),
                    showlegend=False,
                    hoverinfo="skip",
                ))

    fig.update_layout(
        height=300,
        yaxis_title="Range (km)",
        xaxis_title="Seconds elapsed",
        margin=dict(t=20, b=20, l=40, r=20),
        legend=dict(orientation="h", y=-0.2),
    )
    st.plotly_chart(fig, use_container_width=True)

else:
    st.info("Range history will appear after the first few readings.")

st.divider()

# ── Environmental context ──────────────────────────────────────────────────────
st.subheader("Environmental context")

gps = state.get("gps") or {}
ec1, ec2, ec3, ec4 = st.columns(4)
ec1.metric("Elevation",   f"{gps.get('elevation_m', '—'):.0f} m"   if gps.get('elevation_m') else "—")
ec2.metric("Distance",    f"{gps.get('distance_km', '—'):.2f} km"  if gps.get('distance_km') else "—")
ec3.metric("SoC (LSTM)",  f"{(state.get('soc_pred') or {}).get('predicted_soc', '—'):.1f}%" 
           if (state.get('soc_pred') or {}).get('predicted_soc') else "—")
ec4.metric("SoH",         f"{(state.get('soh_pred') or {}).get('soh_pct', '—')}%"
           if (state.get('soh_pred') or {}).get('soh_pct') else "—")

# ── Consumption breakdown ──────────────────────────────────────────────────────
with st.expander("How is range calculated?"):
    soh_v = (state.get("soh_pred") or {}).get("soh_pct", 100.0) or 100.0
    soc_v = (state.get("soc_pred") or {}).get("predicted_soc", 95.0) or 95.0
    eff_cap = soh_v / 100.0 * 26250   # 75 Ah × 350 V = 26,250 Wh
    energy_avail = soc_v / 100.0 * eff_cap

    st.markdown(f"""
    **Formula:**
    `Range = Energy available (Wh) ÷ Predicted consumption (Wh/km)`

    | Component | Value |
    |---|---|
    | Nominal capacity | 26,250 Wh (75 Ah × 350 V) |
    | SoH factor | {soh_v:.1f}% → effective capacity = {eff_cap:,.0f} Wh |
    | Current SoC | {soc_v:.1f}% → energy available = {energy_avail:,.0f} Wh |
    | Predicted consumption | {f"{cons:.0f} Wh/km" if cons else "pending"} |
    | **Estimated range** | **{f"{energy_avail/cons:.0f} km" if cons else "pending"}** |

    The consumption model accounts for: speed (drag ∝ v²), temperature (battery
    capacity + HVAC load), elevation (mgh energy for climbs), and your actual
    rolling 5-minute consumption measured from the live data.
    """)

if auto_refresh:
    time.sleep(1)
    st.rerun()
