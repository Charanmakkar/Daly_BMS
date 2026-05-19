"""
queries.py
Pre-built Flux query helpers for the Streamlit dashboard and
ad-hoc analysis in the InfluxDB Data Explorer.

Usage in Streamlit:
    from queries import InfluxReader
    reader = InfluxReader()
    soc_df = reader.soc_history(minutes=10)

Usage in Jupyter:
    from queries import InfluxReader
    reader = InfluxReader()
    degradation_df = reader.soh_degradation_all_sessions()
"""

import pandas as pd
from influxdb_client import InfluxDBClient
import config


class InfluxReader:
    """
    Read-only InfluxDB client for Streamlit dashboard and analysis.
    Wraps common Flux queries as Python methods returning DataFrames.
    """

    def __init__(self):
        self._client   = InfluxDBClient(
            url=config.INFLUX_URL,
            token=config.INFLUX_TOKEN,
            org=config.INFLUX_ORG,
        )
        self._query_api = self._client.query_api()

    def _query_df(self, flux: str) -> pd.DataFrame:
        """Run a Flux query and return a clean DataFrame."""
        try:
            tables = self._query_api.query_data_frame(flux, org=config.INFLUX_ORG)
            if isinstance(tables, list):
                if not tables:
                    return pd.DataFrame()
                df = pd.concat(tables, ignore_index=True)
            else:
                df = tables
            # Drop InfluxDB metadata columns
            drop_cols = [c for c in df.columns
                         if c.startswith("_") and c not in ("_time", "_value", "_field")]
            return df.drop(columns=drop_cols, errors="ignore")
        except Exception as exc:
            print(f"[InfluxReader] Query failed: {exc}")
            return pd.DataFrame()

    # ── SoC ───────────────────────────────────────────────────────────────────

    def soc_history(self, minutes: int = 10,
                    session: str = None) -> pd.DataFrame:
        """
        Returns DataFrame with columns: _time, predicted_soc, coulomb_soc
        for comparison plotting.
        """
        session_filter = f'|> filter(fn: (r) => r.session == "{session}")' \
                         if session else ""
        flux = f"""
        import "join"

        lstm = from(bucket: "{config.INFLUX_BUCKET}")
            |> range(start: -{minutes}m)
            |> filter(fn: (r) => r._measurement == "soc_model"
                      and r._field == "predicted_soc")
            {session_filter}
            |> rename(columns: {{_value: "predicted_soc"}})

        coulomb = from(bucket: "{config.INFLUX_BUCKET}")
            |> range(start: -{minutes}m)
            |> filter(fn: (r) => r._measurement == "battery"
                      and r._field == "soc")
            {session_filter}
            |> rename(columns: {{_value: "coulomb_soc"}})

        join.time(left: lstm, right: coulomb, as: (l, r) => ({{
            _time:         l._time,
            predicted_soc: l.predicted_soc,
            coulomb_soc:   r.coulomb_soc,
        }}))
        """
        return self._query_df(flux)

    # ── SoH / RUL ─────────────────────────────────────────────────────────────

    def soh_latest(self) -> float | None:
        """Return the most recent SoH % value."""
        flux = f"""
        from(bucket: "{config.INFLUX_BUCKET}")
            |> range(start: -1h)
            |> filter(fn: (r) => r._measurement == "soh_model"
                      and r._field == "soh_pct")
            |> last()
        """
        df = self._query_df(flux)
        if df.empty or "_value" not in df.columns:
            return None
        return float(df["_value"].iloc[0])

    def soh_degradation_all_sessions(self) -> pd.DataFrame:
        """
        Returns all SoH readings across all sessions — used to plot
        long-run degradation curve across multiple days/weeks of data.
        """
        flux = f"""
        from(bucket: "{config.INFLUX_BUCKET}")
            |> range(start: -30d)
            |> filter(fn: (r) => r._measurement == "soh_model"
                      and r._field == "soh_pct")
            |> aggregateWindow(every: 5m, fn: last)
        """
        return self._query_df(flux)

    def rul_history(self, days: int = 7) -> pd.DataFrame:
        """RUL over time — shows remaining useful life trending downward."""
        flux = f"""
        from(bucket: "{config.INFLUX_BUCKET}")
            |> range(start: -{days}d)
            |> filter(fn: (r) => r._measurement == "soh_model"
                      and r._field == "rul_cycles"
                      and r._value >= 0)
            |> aggregateWindow(every: 10m, fn: last)
        """
        return self._query_df(flux)

    # ── Anomaly ───────────────────────────────────────────────────────────────

    def anomaly_score_history(self, minutes: int = 30) -> pd.DataFrame:
        """Rolling reconstruction error — for the error timeline chart."""
        flux = f"""
        from(bucket: "{config.INFLUX_BUCKET}")
            |> range(start: -{minutes}m)
            |> filter(fn: (r) => r._measurement == "anomaly"
                      and r._field == "global_error")
        """
        return self._query_df(flux)

    def fault_alerts(self, hours: int = 24) -> pd.DataFrame:
        """
        All fault alert events in the last N hours.
        Returns: _time, likely_fault, severity, global_error
        """
        flux = f"""
        from(bucket: "{config.INFLUX_BUCKET}")
            |> range(start: -{hours}h)
            |> filter(fn: (r) => r._measurement == "anomaly_alert"
                      and r._field == "global_error")
            |> keep(columns: ["_time", "likely_fault", "severity", "_value"])
            |> rename(columns: {{_value: "error"}})
            |> sort(columns: ["_time"], desc: true)
        """
        return self._query_df(flux)

    def fault_count_by_type(self, days: int = 7) -> pd.DataFrame:
        """
        Count of each fault type over last N days.
        Useful for a bar chart in the dashboard summary.
        """
        flux = f"""
        from(bucket: "{config.INFLUX_BUCKET}")
            |> range(start: -{days}d)
            |> filter(fn: (r) => r._measurement == "anomaly_alert"
                      and r._field == "global_error")
            |> group(columns: ["likely_fault"])
            |> count()
            |> rename(columns: {{_value: "count"}})
        """
        return self._query_df(flux)

    # ── Range ─────────────────────────────────────────────────────────────────

    def range_history(self, minutes: int = 30) -> pd.DataFrame:
        """Static, dynamic, and blended range estimates over time."""
        flux = f"""
        from(bucket: "{config.INFLUX_BUCKET}")
            |> range(start: -{minutes}m)
            |> filter(fn: (r) => r._measurement == "range_model"
                      and (r._field == "range_km"
                           or r._field == "static_km"
                           or r._field == "dynamic_km"))
            |> pivot(rowKey: ["_time"], columnKey: ["_field"], valueColumn: "_value")
        """
        return self._query_df(flux)

    # ── Battery raw ────────────────────────────────────────────────────────────

    def battery_history(self, minutes: int = 10,
                        fields: list = None) -> pd.DataFrame:
        """
        Raw battery telemetry. Default fields: voltage, current, temperature.
        """
        if fields is None:
            fields = ["voltage", "current", "temperature", "power_kw"]

        field_filter = " or ".join(
            [f'r._field == "{f}"' for f in fields]
        )
        flux = f"""
        from(bucket: "{config.INFLUX_BUCKET}")
            |> range(start: -{minutes}m)
            |> filter(fn: (r) => r._measurement == "battery"
                      and ({field_filter}))
            |> pivot(rowKey: ["_time"], columnKey: ["_field"], valueColumn: "_value")
        """
        return self._query_df(flux)

    # ── Cross-session analysis ────────────────────────────────────────────────

    def session_summary(self) -> pd.DataFrame:
        """
        Summary stats per session — useful for the capstone report.
        Returns: session, avg_soc, min_soh, n_alerts, avg_range_km
        """
        flux = f"""
        from(bucket: "{config.INFLUX_BUCKET}")
            |> range(start: -30d)
            |> filter(fn: (r) => r._measurement == "battery"
                      and r._field == "soc")
            |> group(columns: ["session"])
            |> mean()
            |> rename(columns: {{_value: "avg_soc"}})
        """
        return self._query_df(flux)

    def close(self):
        self._client.close()
