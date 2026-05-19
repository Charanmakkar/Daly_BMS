import json
import time
import ssl
import math
import random
import csv
import os
from datetime import datetime

import paho.mqtt.client as mqtt
from dalybms import DalyBMS

# =========================================================
# MQTT CONFIGURATION
# =========================================================

BROKER = "4927161c6b0c474a9aa19d86178cf2b1.s1.eu.hivemq.cloud"
PORT = 8883

USERNAME = "bms_data"
PASSWORD = "Praveen@81433"

TOPIC = "ev/battery"

# =========================================================
# DALY BMS CONFIGURATION
# =========================================================

RS485_PORT = "COM8"

# =========================================================
# BATTERY MODEL PARAMETERS
# =========================================================

BATTERY_NOMINAL_VOLTAGE = 22.2
BATTERY_CAPACITY_AH = 14.91

BATTERY_INTERNAL_RESISTANCE = 0.045
BATTERY_HEAT_COEFF = 0.25
BATTERY_THERMAL_MASS = 1200.0
BATTERY_AMBIENT_TEMP = 25.0

# =========================================================
# STATE VARIABLES
# =========================================================

battery_temperature = BATTERY_AMBIENT_TEMP

total_charge_throughput_ah = 0.0

battery_soh = 100.0

tick = 0

# =========================================================
# CSV FILE
# =========================================================

CSV_FILE = "ev_battery_complete_log.csv"

# =========================================================
# MQTT CALLBACKS
# =========================================================

def on_connect(client, userdata, flags, rc):

    if rc == 0:

        print("Connected to HiveMQ Cloud")

    else:

        print(f"MQTT Connection Failed: {rc}")

# =========================================================
# MQTT CLIENT SETUP
# =========================================================

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

# =========================================================
# DALY BMS CONNECTION
# =========================================================

bms = DalyBMS(request_retries=3)

print(f"Connecting to Daly BMS on {RS485_PORT}...")

try:

    bms.connect(RS485_PORT)

    print("BMS Connected Successfully")

except Exception as e:

    print(f"BMS Connection Failed: {e}")

    exit()

# =========================================================
# CREATE CSV FILE
# =========================================================

if not os.path.exists(CSV_FILE):

    with open(CSV_FILE, mode='w', newline='') as file:

        writer = csv.writer(file)

        writer.writerow([

            "timestamp",
            "soc",
            "soh",
            "voltage",
            "current",
            "temperature",
            "power_kw",
            "capacity_ah",
            "range_km",
            "ocv",
            "v_drop",
            "internal_resistance",
            "heat_w",
            "cooling_w",
            "temp_rise",
            "energy_kwh",
            "throughput_ah",
            "efficiency",
            "c_rate",
            "fault_active",
            "fault_type",
            "max_cell_v",
            "min_cell_v",
            "cell_delta_mv",
            "tick"
        ])

# =========================================================
# MQTT TIMER
# =========================================================

last_mqtt_publish = 0

# =========================================================
# MAIN LOOP
# =========================================================

while True:

    try:

        # =================================================
        # READ DALY BMS
        # =================================================

        soc_data = bms.get_soc()

        cell_voltages = bms.get_cell_voltages()

        temps = bms.get_temperatures()

        mosfet = bms.get_mosfet_status()

        errors = bms.get_errors()

        if not soc_data:

            print("Failed to read SOC data")

            time.sleep(1)

            continue

        # =================================================
        # BASIC VALUES
        # =================================================

        voltage = soc_data.get("total_voltage", 0)

        current = soc_data.get("current", 0)

        soc = soc_data.get("soc_percent", 0)

        capacity_ah = mosfet.get(
            "capacity_ah",
            BATTERY_CAPACITY_AH
        ) if mosfet else BATTERY_CAPACITY_AH

        # =================================================
        # CELL VOLTAGES
        # =================================================

        cv = []

        if cell_voltages:

            cv = list(cell_voltages.values())

        max_cell_v = max(cv) if cv else 0

        min_cell_v = min(cv) if cv else 0

        cell_delta_mv = (
            (max_cell_v - min_cell_v) * 1000
        ) if cv else 0

        # =================================================
        # TEMPERATURES
        # =================================================

        t = []

        if temps:

            t = list(temps.values())

        avg_temp = (
            sum(t) / len(t)
        ) if t else BATTERY_AMBIENT_TEMP

        # =================================================
        # POWER
        # =================================================

        power_kw = (
            voltage * current
        ) / 1000.0

        # =================================================
        # OCV ESTIMATION
        # =================================================

        ocv = sum(cv) if cv else voltage

        # =================================================
        # INTERNAL VOLTAGE DROP
        # =================================================

        v_drop = abs(
            current *
            BATTERY_INTERNAL_RESISTANCE
        )

        # =================================================
        # HEAT GENERATION
        # =================================================

        heat_w = (
            current * current *
            BATTERY_INTERNAL_RESISTANCE
        )

        # =================================================
        # COOLING POWER
        # =================================================

        cooling_w = (
            BATTERY_HEAT_COEFF *
            (
                battery_temperature -
                BATTERY_AMBIENT_TEMP
            )
        )

        # =================================================
        # THERMAL MODEL
        # =================================================

        dt = 0.05

        battery_temperature += (
            (
                heat_w -
                cooling_w
            ) / BATTERY_THERMAL_MASS
        ) * dt

        temp_rise = (
            battery_temperature -
            BATTERY_AMBIENT_TEMP
        )

        # =================================================
        # BATTERY THROUGHPUT
        # =================================================

        total_charge_throughput_ah += abs(
            current * dt / 3600.0
        )

        # =================================================
        # SOH DEGRADATION
        # =================================================

        battery_soh = max(
            80.0,
            100.0 - (
                total_charge_throughput_ah * 0.0001
            )
        )

        # =================================================
        # ENERGY REMAINING
        # =================================================

        energy_kwh = (
            (soc / 100.0) *
            capacity_ah *
            BATTERY_NOMINAL_VOLTAGE
        ) / 1000.0

        # =================================================
        # RANGE ESTIMATION
        # =================================================

        range_km = (
            energy_kwh * 1000
        ) / 180.0

        # =================================================
        # EFFICIENCY
        # =================================================

        efficiency = (
            (voltage / ocv) * 100
        ) if ocv > 0 else 0

        # =================================================
        # C-RATE
        # =================================================

        c_rate = (
            current / capacity_ah
        ) if capacity_ah > 0 else 0

        # =================================================
        # FAULTS
        # =================================================

        error_count = len(errors) if errors else 0

        fault_active = (
            True if error_count > 0 else False
        )

        # =================================================
        # ADD SENSOR NOISE
        # =================================================

        voltage += random.gauss(0, 0.02)

        current += random.gauss(0, 0.05)

        avg_temp += random.gauss(0, 0.2)

        # =================================================
        # CREATE FINAL MQTT PAYLOAD
        # =================================================

        payload = {

            "soc": round(soc, 2),

            "soh": round(battery_soh, 2),

            "voltage": round(voltage, 3),

            "current": round(current, 3),

            "temperature": round(avg_temp, 2),

            "power_kw": round(power_kw, 3),

            "capacity_ah": round(capacity_ah, 2),

            "range_km": round(range_km, 1),

            "ocv": round(ocv, 3),

            "v_drop": round(v_drop, 3),

            "internal_resistance": round(
                BATTERY_INTERNAL_RESISTANCE,
                4
            ),

            "heat_w": round(heat_w, 2),

            "cooling_w": round(cooling_w, 2),

            "temp_rise": round(temp_rise, 2),

            "energy_kwh": round(energy_kwh, 3),

            "throughput_ah": round(
                total_charge_throughput_ah,
                3
            ),

            "efficiency": round(
                efficiency,
                2
            ),

            "c_rate": round(
                c_rate,
                3
            ),

            "fault_active": fault_active,

            "fault_type": error_count,

            "max_cell_v": round(
                max_cell_v,
                3
            ),

            "min_cell_v": round(
                min_cell_v,
                3
            ),

            "cell_delta_mv": round(
                cell_delta_mv,
                1
            ),

            "tick": tick
        }

        # =================================================
        # WRITE CSV
        # =================================================

        with open(CSV_FILE, mode='a', newline='') as file:

            writer = csv.writer(file)

            writer.writerow(payload.values())

        # =================================================
        # MQTT PUBLISH EVERY 2 SECONDS
        # =================================================

        current_time = time.time()

        if current_time - last_mqtt_publish >= 2:

            payload_json = json.dumps(
                payload,
                separators=(',', ':')
            )

            client.publish(
                TOPIC,
                payload_json
            )

            print("\nPublished to MQTT:")
            print(payload_json)

            last_mqtt_publish = current_time

        # =================================================
        # LOOP CONTROL
        # =================================================

        tick += 1

        time.sleep(0.05)

    except Exception as e:

        print(f"Runtime Error: {e}")

        time.sleep(1)