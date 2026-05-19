"""
subscriber.py
Main MQTT → InfluxDB subscriber process.

Run this alongside your inference scripts:
    python subscriber.py
    python subscriber.py --broker 192.168.1.100 --session my_trip_01
    python subscriber.py --dry_run               # print points, no DB write

Architecture:
  MQTT thread    → receives messages, builds Points, enqueues
  Flush thread   → drains queue, writes batches to InfluxDB
  Health thread  → prints stats every 30 seconds
  Main thread    → handles signals, coordinates shutdown

Session IDs:
  Each run of subscriber.py gets a unique session ID (timestamp-based
  unless --session is specified). This lets you query across sessions
  in Grafana: "show me all thermal runaway events across all sessions".
"""

import argparse
import json
import logging
import signal
import sys
import threading
import time
import ssl
from datetime import datetime

import paho.mqtt.client as mqtt

from influx_writer import InfluxWriter
from point_builder  import build_point
import config

# ── Logging ────────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(name)s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("subscriber")


# ── CLI ────────────────────────────────────────────────────────────────────────

def parse_args():
    p = argparse.ArgumentParser(
        description="MQTT → InfluxDB subscriber for EV Digital Twin"
    )
    p.add_argument("--broker",    default=config.MQTT_BROKER,
                   help=f"MQTT broker IP (default: {config.MQTT_BROKER})")
    p.add_argument("--port",      type=int, default=config.MQTT_PORT)
    p.add_argument("--influx_url", default=config.INFLUX_URL)
    p.add_argument("--token",     default=config.INFLUX_TOKEN,
                   help="InfluxDB API token")
    p.add_argument("--org",       default=config.INFLUX_ORG)
    p.add_argument("--bucket",    default=config.INFLUX_BUCKET)
    p.add_argument("--session",   default=None,
                   help="Session label (default: timestamp)")
    p.add_argument("--dry_run",   action="store_true",
                   help="Print points to stdout without writing to InfluxDB")
    p.add_argument("--verbose",   action="store_true",
                   help="Log every received message")
    return p.parse_args()


# ── Main subscriber class ──────────────────────────────────────────────────────

class EVSubscriber:
    def __init__(self, args):
        self.args       = args
        self.session_id = args.session or datetime.now().strftime("%Y%m%d_%H%M%S")
        self.writer     = None
        self.client     = None
        self._running   = False
        self._msg_count = 0
        self._msg_lock  = threading.Lock()

    def start(self):
        log.info("=" * 56)
        log.info("  EV Digital Twin — MQTT → InfluxDB Subscriber")
        log.info("=" * 56)
        log.info(f"  Session ID  : {self.session_id}")
        log.info(f"  MQTT broker : {self.args.broker}:{self.args.port}")
        log.info(f"  InfluxDB    : {self.args.influx_url}  "
                 f"bucket={self.args.bucket}")
        log.info(f"  Dry run     : {self.args.dry_run}")
        log.info("=" * 56)

        # Override config from CLI args
        config.INFLUX_URL    = self.args.influx_url
        config.INFLUX_TOKEN  = self.args.token
        config.INFLUX_ORG    = self.args.org
        config.INFLUX_BUCKET = self.args.bucket

        # Start InfluxDB writer (unless dry run)
        if not self.args.dry_run:
            self.writer = InfluxWriter()
            self.writer.start()

        # Start MQTT client
        self.client = mqtt.Client(client_id=config.MQTT_CLIENT_ID)
        if config.MQTT_USERNAME:
            self.client.username_pw_set(config.MQTT_USERNAME, config.MQTT_PASSWORD)

             # ── HiveMQ Cloud: credentials + TLS ──────────────────────────────────
        #self._client.username_pw_set(MQTT_USERNAME, MQTT_PASSWORD)
        self.client.tls_set(tls_version=ssl.PROTOCOL_TLS)
        # ─────────────────────────────────────────────────────────────────────

        self.client.on_connect    = self._on_connect
        self.client.on_message    = self._on_message
        self.client.on_disconnect = self._on_disconnect

        self._running = True

        # Start health reporter thread
        health_thread = threading.Thread(
            target=self._health_loop, daemon=True, name="health"
        )
        health_thread.start()

        # Connect with auto-reconnect loop
        self._connect_loop()

    def stop(self):
        log.info("Shutting down …")
        self._running = False
        if self.client:
            self.client.loop_stop()
            self.client.disconnect()
        if self.writer:
            self.writer.stop()
        log.info(f"Done. Total messages processed: {self._msg_count:,}")

    # ── MQTT callbacks ─────────────────────────────────────────────────────────

    def _on_connect(self, client, userdata, flags, rc):
        codes = {0: "connected ✓", 1: "bad protocol", 2: "ID rejected",
                 3: "server unavailable", 4: "bad credentials", 5: "not authorised"}
        status = codes.get(rc, f"rc={rc}")
        if rc == 0:
            log.info(f"MQTT {status}")
            # Subscribe to all configured topics
            for topic in config.TOPIC_MAP:
                client.subscribe(topic, qos=0)
            log.info(f"Subscribed to {len(config.TOPIC_MAP)} topics")
        else:
            log.error(f"MQTT connection failed: {status}")

    def _on_message(self, client, userdata, msg):
        try:
            payload = json.loads(msg.payload.decode())
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            log.debug(f"Bad payload on {msg.topic}: {exc}")
            return

        with self._msg_lock:
            self._msg_count += 1

        if self.args.verbose:
            log.info(f"  {msg.topic:<28} {str(payload)[:80]}")

        # Build InfluxDB point(s)
        points = build_point(msg.topic, payload, self.session_id)

        if self.args.dry_run:
            for p in points:
                print(f"  [DRY RUN] {p.to_line_protocol()}")
            return

        for p in points:
            if self.writer:
                self.writer.enqueue(p)

    def _on_disconnect(self, client, userdata, rc):
        if rc != 0:
            log.warning(f"MQTT unexpected disconnect (rc={rc}). "
                        f"Will reconnect …")

    # ── Connection loop ────────────────────────────────────────────────────────

    def _connect_loop(self):
        backoff = 2
        while self._running:
            try:
                log.info(f"Connecting to MQTT broker {self.args.broker}:{self.args.port} …")
                self.client.connect(self.args.broker, self.args.port, keepalive=60)
                self.client.loop_start()
                # Block main thread while running
                while self._running:
                    time.sleep(1)
                return
            except Exception as exc:
                log.warning(f"MQTT connect failed: {exc}. Retry in {backoff}s …")
                time.sleep(backoff)
                backoff = min(backoff * 2, 60)

    # ── Health reporter ────────────────────────────────────────────────────────

    def _health_loop(self):
        while self._running:
            time.sleep(30)
            if not self._running:
                break
            with self._msg_lock:
                msg_count = self._msg_count

            stats_str = ""
            if self.writer:
                s = self.writer.stats()
                stats_str = (f" | written={s['written']:,}  "
                             f"dropped={s['dropped']}  "
                             f"errors={s['errors']}")

            log.info(f"[Health] msgs={msg_count:,}{stats_str}")


# ── Entry point ────────────────────────────────────────────────────────────────

def main():
    args       = parse_args()
    subscriber = EVSubscriber(args)

    # Graceful shutdown on Ctrl+C or SIGTERM
    def handle_signal(sig, frame):
        log.info(f"\nSignal {sig} received — shutting down …")
        subscriber.stop()
        sys.exit(0)

    signal.signal(signal.SIGINT,  handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    subscriber.start()


if __name__ == "__main__":
    main()
