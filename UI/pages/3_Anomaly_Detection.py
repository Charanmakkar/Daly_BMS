"""
pages/3_Anomaly_Detection.py
Live anomaly score, per-subsystem breakdown, alert timeline.
"""

import time
import streamlit as st
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from mqtt_state import state

st.set_page_config(page_title="Anomaly Detection", page_icon="🚨", layout="wide")

with st.sidebar:
    st.title("⚡ EV Digital Twin")
    auto_refresh = st.toggle("Auto-refresh (1s)", value=True, key="anm_refresh")

st.title("🚨 Anomaly Detection")
st.caption("LSTM Autoencoder — reconstruction error monitoring and fault diagnosis")

# ── Pull data ──────────────────────────────────────────────────────────────────
anm_hist   = pd.DataFrame(state.anomaly_score_hist.snapshot())
alerts     = state.alert_log.snapshot()
latest_anm = state.get("anomaly_score") or {}
last_alert = state.get("last_alert")    or {}

# ── Current status banner ─────────────────────────────────────────────────────
err   = latest_anm.get("global_error")
thr   = latest_anm.get("threshold")
alert = latest_anm.get("alert", False)

if err is not None and thr is not None and thr > 0:
    ratio = err / thr
    if ratio > 1.0:
        fault = last_alert.get("likely_fault", "unknown anomaly")
        st.error(f"⚠ ANOMALY DETECTED — {ratio:.1f}× threshold — "
                 f"Diagnosis: **{fault}**")
    elif ratio > 0.7:
        st.warning(f"🔶 Elevated reconstruction error ({ratio:.2f}× threshold) "
                   f"— monitoring closely")
    else:
        st.success(f"✅ Normal operation — error {ratio:.2f}× threshold")
else:
    st.info("Waiting for anomaly detector to initialise (20 second warmup) …")

st.divider()

# ── Summary metrics ────────────────────────────────────────────────────────────
st.subheader("Detection metrics")
c1, c2, c3, c4, c5 = st.columns(5)

c1.metric("Reconstruction error", f"{err:.6f}" if err else "—")
c2.metric("Threshold",            f"{thr:.6f}" if thr else "—")
c3.metric("Error ratio",          f"{ratio:.2f}×" if err and thr else "—",
          "ALERT" if alert else "Normal")
c4.metric("Total alerts",         len(alerts))
c5.metric("Last fault",           last_alert.get("likely_fault", "None"))

st.divider()

# ── Reconstruction error time series ──────────────────────────────────────────
st.subheader("Reconstruction error over time")

if len(anm_hist) > 5 and "global_error" in anm_hist.columns:
    anm_hist["t"] = range(len(anm_hist))
    threshold_val = anm_hist["threshold"].iloc[-1] if "threshold" in anm_hist.columns else None

    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=anm_hist["t"],
        y=anm_hist["global_error"],
        mode="lines",
        name="Reconstruction MSE",
        line=dict(color="#3498db", width=1.2),
        fill="tozeroy",
        fillcolor="rgba(52,152,219,0.08)"
    ))

    # Mark alert points
    if "alert" in anm_hist.columns:
        alert_rows = anm_hist[anm_hist["alert"] == True]
        if len(alert_rows):
            fig.add_trace(go.Scatter(
                x=alert_rows["t"],
                y=alert_rows["global_error"],
                mode="markers",
                name="Alert",
                marker=dict(color="#e74c3c", size=8, symbol="x")
            ))

    if threshold_val:
        fig.add_hline(
            y=threshold_val,
            line_dash="dash",
            line_color="#e74c3c",
            annotation_text="Alert threshold (99th pct)",
            annotation_position="top right"
        )

    fig.update_layout(
        height=280,
        yaxis_title="MSE (reconstruction error)",
        xaxis_title="Seconds",
        margin=dict(t=20, b=20, l=40, r=20),
        legend=dict(orientation="h", y=-0.2),
        yaxis_type="log"   # log scale shows normal vs fault contrast better
    )
    st.plotly_chart(fig, use_container_width=True)
    st.caption("Log scale — fault events appear as dramatic spikes")
else:
    st.info("Waiting for anomaly detection history …")

# ── Per-subsystem breakdown ────────────────────────────────────────────────────
st.subheader("Subsystem reconstruction breakdown")
st.caption("Which subsystem is driving the anomaly?")

subsystems = last_alert.get("subsystems") or {}
if not subsystems and latest_anm:
    # Try to get from latest score if no alert
    pass

if subsystems:
    groups = list(subsystems.keys())
    errors = [subsystems[g]["error"] for g in groups]
    flags  = [subsystems[g]["flagged"] for g in groups]
    colors = ["#e74c3c" if f else "#3498db" for f in flags]

    fig2 = go.Figure(go.Bar(
        x=groups,
        y=errors,
        marker_color=colors,
        text=[f"{'⚠ FLAGGED' if f else 'normal'}" for f in flags],
        textposition="outside"
    ))
    if thr:
        fig2.add_hline(y=thr, line_dash="dash",
                       line_color="#e74c3c",
                       annotation_text="Threshold")
    fig2.update_layout(
        title="Per-subsystem reconstruction error",
        height=280,
        yaxis_title="MSE",
        margin=dict(t=40, b=20, l=40, r=20),
    )
    st.plotly_chart(fig2, use_container_width=True)
else:
    sub_cols = st.columns(4)
    for i, (group, label) in enumerate(
        [("battery","Battery"), ("motor","Motor"),
         ("derived","Derived"), ("ratios","Ratios")]):
        sub_cols[i].metric(label, "—")

st.divider()

# ── Alert log table ────────────────────────────────────────────────────────────
st.subheader(f"Fault alert log ({len(alerts)} events)")

if alerts:
    rows = []
    for a in reversed(alerts):
        sev    = a.get("severity", "—")
        fault  = a.get("likely_fault", "unknown")
        tick   = a.get("tick", "?")
        ratio_ = a.get("error_ratio", 0)
        flags  = [k for k, v in a.get("subsystems", {}).items() if v.get("flagged")]
        rows.append({
            "Tick":       tick,
            "Severity":   sev.upper(),
            "Fault":      fault,
            "Error ratio": f"{ratio_:.1f}×",
            "Subsystems": ", ".join(flags) if flags else "global",
        })
    df_alerts = pd.DataFrame(rows)

    # Colour-code severity
    def highlight_sev(row):
        if row["Severity"] == "HIGH":
            return ["background-color: #fadbd8"] * len(row)
        elif row["Severity"] == "MEDIUM":
            return ["background-color: #fef9e7"] * len(row)
        return [""] * len(row)

    st.dataframe(
        df_alerts.style.apply(highlight_sev, axis=1),
        use_container_width=True,
        hide_index=True,
    )
else:
    st.success("No fault alerts recorded in this session.")

st.divider()

# ── Fault type reference ───────────────────────────────────────────────────────
with st.expander("Fault type reference"):
    st.markdown("""
    | Fault type | Subsystems flagged | Key indicator |
    |---|---|---|
    | `thermal_runaway` | battery, derived, ratios | Temperature rising >2°C/s |
    | `overcurrent` | battery, ratios | Current 2.5× normal |
    | `cell_short` | battery | Voltage drop + current spike |
    | `overvoltage` | battery | Voltage above safe limit |
    | `locked_rotor` | motor, ratios | Speed=0, current spike |
    | `overspeed` | motor | RPM >130% of rated |
    | `unknown_anomaly` | varies | Pattern not matching known faults |
    """)

if auto_refresh:
    time.sleep(1)
    st.rerun()
