"""
pages/5_GPS_Map.py
Live GPS track, elevation profile, and trip summary.
"""

import time
import streamlit as st
import pandas as pd
import plotly.graph_objects as go
import plotly.express as px
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from mqtt_state import state

st.set_page_config(page_title="GPS Map", page_icon="🗺", layout="wide")

with st.sidebar:
    st.title("⚡ EV Digital Twin")
    auto_refresh = st.toggle("Auto-refresh (2s)", value=True, key="gps_refresh")

st.title("🗺 GPS Track & Trip Summary")
st.caption("Live position, route replay, and elevation profile")

# ── Pull data ──────────────────────────────────────────────────────────────────
gps_hist = pd.DataFrame(state.gps_hist.snapshot())
gps_now  = state.get("gps") or {}
mot_now  = state.get("motor") or {}

# ── Trip summary metrics ──────────────────────────────────────────────────────
st.subheader("Trip summary")
m1, m2, m3, m4, m5 = st.columns(5)
m1.metric("Distance",    f"{gps_now.get('distance_km', 0):.2f} km")
m2.metric("Trip time",   f"{gps_now.get('trip_time_s', 0)//60} min"
                         f" {gps_now.get('trip_time_s', 0)%60}s")
m3.metric("Elevation",   f"{gps_now.get('elevation_m', '—'):.0f} m"
                         if gps_now.get('elevation_m') else "—")
m4.metric("Heading",     f"{gps_now.get('heading_deg', '—'):.0f}°"
                         if gps_now.get('heading_deg') else "—")
m5.metric("Speed",       f"{mot_now.get('speed_kmh', '—'):.1f} km/h"
                         if mot_now.get('speed_kmh') else "—")

st.divider()

if len(gps_hist) > 5 and "latitude" in gps_hist.columns:
    gps_hist = gps_hist.dropna(subset=["latitude", "longitude"])
    gps_hist["t"] = range(len(gps_hist))

    map_col, elev_col = st.columns([2, 1])

    # ── GPS track map ─────────────────────────────────────────────────────────
    with map_col:
        st.subheader("GPS track")

        # Colour track by speed if available
        mot_hist = pd.DataFrame(state.motor_hist.snapshot())
        if len(mot_hist) == len(gps_hist) and "speed_kmh" in mot_hist.columns:
            gps_hist["speed_kmh"] = mot_hist["speed_kmh"].values
            color_col = "speed_kmh"
            color_label = "Speed (km/h)"
        else:
            gps_hist["speed_kmh"] = 50.0
            color_col = "speed_kmh"
            color_label = "Speed (km/h)"

        fig_map = px.scatter_mapbox(
            gps_hist,
            lat="latitude",
            lon="longitude",
            color=color_col,
            color_continuous_scale="RdYlGn_r",
            labels={color_col: color_label},
            zoom=13,
            height=380,
            mapbox_style="open-street-map",
        )
        # Mark start and current position
        fig_map.add_trace(go.Scattermapbox(
            lat=[gps_hist["latitude"].iloc[0]],
            lon=[gps_hist["longitude"].iloc[0]],
            mode="markers",
            marker=dict(size=12, color="green"),
            name="Start",
        ))
        fig_map.add_trace(go.Scattermapbox(
            lat=[gps_hist["latitude"].iloc[-1]],
            lon=[gps_hist["longitude"].iloc[-1]],
            mode="markers",
            marker=dict(size=14, color="blue", symbol="car"),
            name="Current",
        ))
        fig_map.update_layout(margin=dict(t=10, b=10, l=0, r=0))
        st.plotly_chart(fig_map, use_container_width=True)

    # ── Elevation profile ─────────────────────────────────────────────────────
    with elev_col:
        st.subheader("Elevation profile")
        if "elevation_m" in gps_hist.columns:
            fig_elev = go.Figure()
            fig_elev.add_trace(go.Scatter(
                x=gps_hist["distance_km"] if "distance_km" in gps_hist.columns
                  else gps_hist["t"],
                y=gps_hist["elevation_m"],
                fill="tozeroy",
                fillcolor="rgba(52,152,219,0.15)",
                line=dict(color="#3498db", width=1.5),
                name="Elevation"
            ))
            fig_elev.update_layout(
                height=200,
                xaxis_title="Distance (km)",
                yaxis_title="Elevation (m)",
                margin=dict(t=10, b=30, l=40, r=20),
                showlegend=False
            )
            st.plotly_chart(fig_elev, use_container_width=True)

        # Energy vs distance
        bat_hist = pd.DataFrame(state.battery_hist.snapshot())
        if len(bat_hist) > 5 and "power_kw" in bat_hist.columns:
            st.subheader("Energy draw vs time")
            bat_hist["t"] = range(len(bat_hist))
            fig_pow = go.Figure(go.Scatter(
                x=bat_hist["t"],
                y=bat_hist["power_kw"].clip(lower=0),
                fill="tozeroy",
                line=dict(color="#e74c3c", width=1.0),
                fillcolor="rgba(231,76,60,0.1)"
            ))
            fig_pow.update_layout(
                height=140,
                yaxis_title="kW",
                margin=dict(t=10, b=20, l=40, r=10),
                showlegend=False
            )
            st.plotly_chart(fig_pow, use_container_width=True)
else:
    st.info("GPS track will appear once position data is received.")

if auto_refresh:
    time.sleep(2)
    st.rerun()
