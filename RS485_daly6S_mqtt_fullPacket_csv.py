import json
import time
import ssl
import csv
import os
from datetime import datetime

import paho.mqtt.client as mqtt
from dalybms import DalyBMS

# =====================================================
# MQTT CONFIGURATION
# =====================================================

BROKER = "4927161c6b0c474a9aa19d86178cf2b1.s1.eu.hivemq.cloud"
PORT = 8883

USERNAME = "bms_data"
PASSWORD = "Praveen@81433"

TOPIC = "bms/full"

# =====================================================
# DALY BMS CONFIGURATION
# =====================================================

RS485_PORT = "COM8"

# =====================================================
# CSV CONFIGURATION
# =====================================================

CSV_FILE = "bms_data_log.csv"

# =====================================================
# MQTT CALLBACKS
# =====================================================

def on_connect(client, userdata, flags, rc):

    if rc == 0:
        print("MQTT Connected Successfully")

    else:
        print(f"MQTT Connection Failed: {rc}")

# =====================================================
# MQTT CLIENT SETUP
# =====================================================

client = mqtt.Client()

client.username_pw_set(USERNAME, PASSWORD)

client.tls_set(
    cert_reqs=ssl.CERT_REQUIRED,
    tls_version=ssl.PROTOCOL_TLSv1_2
)

client.on_connect = on_connect

print("Connecting to MQTT Broker...")

client.connect(BROKER, PORT, 60)

client.loop_start()

# =====================================================
# DALY BMS SETUP
# =====================================================

bms = DalyBMS(request_retries=3)

print(f"Connecting to Daly BMS on {RS485_PORT}...")

try:

    bms.connect(RS485_PORT)

    print("BMS Connected Successfully")

except Exception as e:

    print(f"BMS Connection Failed: {e}")

    exit()

# =====================================================
# CREATE CSV FILE IF NOT EXISTS
# =====================================================

if not os.path.exists(CSV_FILE):

    with open(CSV_FILE, mode='w', newline='') as file:

        writer = csv.writer(file)

        writer.writerow([
            "timestamp",
            "voltage",
            "current",
            "soc",
            "max_cell_mv",
            "min_cell_mv",
            "delta_mv",
            "max_temp",
            "min_temp",
            "charge_mosfet",
            "discharge_mosfet",
            "mode",
            "capacity_ah_x100",
            "cycles",
            "cells",
            "temp_sensors",
            "charger_running",
            "load_running",
            "error_count",
            "cell_voltages",
            "temperatures",
            "balancing"
        ])

# =====================================================
# MQTT TIMER
# =====================================================

last_mqtt_publish = 0

# =====================================================
# MAIN LOOP
# =====================================================

while True:

    try:

        # =============================================
        # READ BMS DATA
        # =============================================

        soc = bms.get_soc()

        cell_voltages = bms.get_cell_voltages()

        temps = bms.get_temperatures()

        mosfet = bms.get_mosfet_status()

        status = bms.get_status()

        balancing = bms.get_balancing_status()

        errors = bms.get_errors()

        # =============================================
        # VALIDATION
        # =============================================

        if not soc:

            print("Failed to read SOC")

            continue

        # =============================================
        # PROCESS CELL VOLTAGES
        # =============================================

        cv = []

        if cell_voltages:

            cv = [
                int(v * 1000)
                for v in cell_voltages.values()
            ]

        # =============================================
        # PROCESS TEMPERATURES
        # =============================================

        t = []

        if temps:

            t = list(temps.values())

        # =============================================
        # CALCULATIONS
        # =============================================

        max_cell = max(cv) if cv else 0

        min_cell = min(cv) if cv else 0

        delta = max_cell - min_cell

        max_temp = max(t) if t else 0

        min_temp = min(t) if t else 0

        # =============================================
        # MOSFET STATES
        # =============================================

        charge_mosfet = 1 if mosfet and mosfet.get("charging_mosfet") else 0

        discharge_mosfet = 1 if mosfet and mosfet.get("discharging_mosfet") else 0

        # =============================================
        # MODE ENCODING
        # =============================================

        mode_map = {
            "stationary": 0,
            "charging": 1,
            "discharging": 2,
            "both": 3
        }

        mode = mode_map.get(
            mosfet.get("mode"),
            0
        ) if mosfet else 0

        # =============================================
        # BALANCING
        # =============================================

        bal = []

        if isinstance(balancing, dict):

            if "error" not in balancing:

                bal = [
                    1 if x else 0
                    for x in balancing.values()
                ]

        # =============================================
        # ERROR COUNT
        # =============================================

        error_count = len(errors) if errors else 0

        # =============================================
        # CREATE PAYLOAD
        # =============================================

        payload = {

            "v": int(soc.get("total_voltage", 0) * 100),

            "i": int(soc.get("current", 0) * 10),

            "s": int(soc.get("soc_percent", 0) * 10),

            "cv": cv,

            "mx": max_cell,

            "mn": min_cell,

            "d": delta,

            "t": t,

            "ht": max_temp,

            "lt": min_temp,

            "cm": charge_mosfet,

            "dm": discharge_mosfet,

            "m": mode,

            "cap": int(
                mosfet.get("capacity_ah", 0) * 100
            ) if mosfet else 0,

            "cy": status.get("cycles", 0) if status else 0,

            "nc": status.get("cells", 0) if status else 0,

            "nt": status.get("temperature_sensors", 0) if status else 0,

            "ch": 1 if status and status.get("charger_running") else 0,

            "ld": 1 if status and status.get("load_running") else 0,

            "bal": bal,

            "e": error_count,

            "ts": int(time.time())
        }

        # =============================================
        # WRITE TO CSV
        # =============================================

        with open(CSV_FILE, mode='a', newline='') as file:

            writer = csv.writer(file)

            writer.writerow([

                datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f"),

                payload["v"],

                payload["i"],

                payload["s"],

                payload["mx"],

                payload["mn"],

                payload["d"],

                payload["ht"],

                payload["lt"],

                payload["cm"],

                payload["dm"],

                payload["m"],

                payload["cap"],

                payload["cy"],

                payload["nc"],

                payload["nt"],

                payload["ch"],

                payload["ld"],

                payload["e"],

                json.dumps(payload["cv"]),

                json.dumps(payload["t"]),

                json.dumps(payload["bal"])

            ])

        # =============================================
        # MQTT EVERY 2 SECONDS
        # =============================================

        current_time = time.time()

        if current_time - last_mqtt_publish >= 2:

            payload_json = json.dumps(
                payload,
                separators=(',', ':')
            )

            client.publish(TOPIC, payload_json)

            print("\nMQTT Published:")
            print(payload_json)

            last_mqtt_publish = current_time

        # =============================================
        # FASTEST SAFE LOOP
        # =============================================

        time.sleep(0.05)

    except Exception as e:

        print(f"Runtime Error: {e}")

        time.sleep(1)