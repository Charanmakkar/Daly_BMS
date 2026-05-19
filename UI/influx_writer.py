"""
influx_writer.py
Thread-safe InfluxDB write manager with batching, retry, and backpressure.

Design:
  - A write queue collects Point objects from the MQTT thread.
  - A background flush thread drains the queue every WRITE_INTERVAL_MS.
  - Failed writes are retried up to RETRY_MAX times with exponential backoff.
  - If the queue grows beyond MAX_QUEUE_SIZE, old points are dropped
    (backpressure) so the subscriber never runs out of memory.

Why batching?
  InfluxDB Cloud free tier has a rate limit of ~5 MB/5 min.
  Writing every individual MQTT message (1 Hz × 9 topics = 9 writes/s)
  would hit that limit in minutes.
  Batching 50 points per write reduces API calls by 50× and keeps
  latency under 1 second for a 1-Hz stream.
"""

import time
import threading
import queue
import logging
from datetime import datetime, timezone

from influxdb_client import InfluxDBClient, Point, WritePrecision
from influxdb_client.client.write_api import SYNCHRONOUS

import config

log = logging.getLogger("influx_writer")

MAX_QUEUE_SIZE = 5000    # drop oldest if queue exceeds this


class InfluxWriter:
    """
    Manages an InfluxDB write connection and a background flush thread.

    Usage:
        writer = InfluxWriter()
        writer.start()
        writer.enqueue(point)   # from MQTT thread
        writer.stop()           # on shutdown
    """

    def __init__(self):
        self._client    = None
        self._write_api = None
        self._queue     = queue.Queue(maxsize=MAX_QUEUE_SIZE)
        self._running   = False
        self._thread    = None
        self._stats     = {
            "enqueued":   0,
            "written":    0,
            "dropped":    0,
            "errors":     0,
            "retries":    0,
        }
        self._stats_lock = threading.Lock()

    def start(self):
        """Connect to InfluxDB and start the flush thread."""
        log.info(f"Connecting to InfluxDB at {config.INFLUX_URL} …")
        self._client = InfluxDBClient(
            url=config.INFLUX_URL,
            token=config.INFLUX_TOKEN,
            org=config.INFLUX_ORG,
            timeout=10_000,   # ms
        )
        self._write_api = self._client.write_api(write_options=SYNCHRONOUS)

        # Verify connection by querying health
        try:
            health = self._client.health()
            if health.status == "pass":
                log.info(f"InfluxDB connected ✓  (version {health.version})")
            else:
                log.warning(f"InfluxDB health check: {health.status}")
        except Exception as exc:
            log.warning(f"InfluxDB health check failed: {exc}  "
                        f"(will still attempt writes)")

        self._running = True
        self._thread  = threading.Thread(
            target=self._flush_loop,
            daemon=True,
            name="influx-flush"
        )
        self._thread.start()
        log.info("Flush thread started.")

    def stop(self):
        """Flush remaining points and shut down cleanly."""
        log.info("Stopping InfluxDB writer …")
        self._running = False
        if self._thread:
            self._thread.join(timeout=10)
        # Final flush
        self._flush_batch(drain=True)
        if self._write_api:
            self._write_api.close()
        if self._client:
            self._client.close()
        log.info(f"Stopped. Stats: {self.stats()}")

    def enqueue(self, point: Point):
        """
        Add a point to the write queue (called from MQTT thread).
        If queue is full, drop the oldest point (backpressure).
        """
        try:
            self._queue.put_nowait(point)
            with self._stats_lock:
                self._stats["enqueued"] += 1
        except queue.Full:
            # Drop oldest, make room
            try:
                self._queue.get_nowait()
            except queue.Empty:
                pass
            try:
                self._queue.put_nowait(point)
                with self._stats_lock:
                    self._stats["dropped"] += 1
            except queue.Full:
                pass

    def stats(self) -> dict:
        with self._stats_lock:
            return dict(self._stats)

    # ── Internal ──────────────────────────────────────────────────────────────

    def _flush_loop(self):
        interval = config.WRITE_INTERVAL_MS / 1000.0
        while self._running:
            time.sleep(interval)
            self._flush_batch()

    def _flush_batch(self, drain: bool = False):
        """Drain up to BATCH_SIZE points from queue and write to InfluxDB."""
        points = []
        limit  = self._queue.qsize() if drain else config.BATCH_SIZE

        for _ in range(limit):
            try:
                points.append(self._queue.get_nowait())
            except queue.Empty:
                break

        if not points:
            return

        self._write_with_retry(points)

    def _write_with_retry(self, points: list):
        """Write a batch of points with exponential backoff retry."""
        backoff = config.RETRY_BACKOFF_S
        for attempt in range(config.RETRY_MAX):
            try:
                self._write_api.write(
                    bucket=config.INFLUX_BUCKET,
                    org=config.INFLUX_ORG,
                    record=points,
                    write_precision=WritePrecision.NS,
                )
                with self._stats_lock:
                    self._stats["written"] += len(points)
                if attempt > 0:
                    log.info(f"Write succeeded after {attempt} retries.")
                return
            except Exception as exc:
                with self._stats_lock:
                    self._stats["errors"]  += 1
                    self._stats["retries"] += 1
                if attempt < config.RETRY_MAX - 1:
                    log.warning(f"Write failed (attempt {attempt+1}): {exc}  "
                                f"Retrying in {backoff}s …")
                    time.sleep(backoff)
                    backoff = min(backoff * 2, 60)
                else:
                    log.error(f"Write failed after {config.RETRY_MAX} attempts. "
                              f"Dropping {len(points)} points.")
