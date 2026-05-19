import json
import time
import ssl
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
# TIMING
# =========================================================

CSV_LOG_INTERVAL = 0.05
MQTT_PUBLISH_INTERVAL = 2.0

# =========================================================
# STATE VARIABLES
# =========================================================

battery_temperature = BATTERY_AMBIENT_TEMP

total_charge_throughput_ah = 0.0

battery_soh = 100.0

tick = 0

last_mqtt_publish = 0

# =========================================================
# CSV FILE
# =========================================================

CSV_FILE = "ev_battery_complete_log.csv"

CSV_HEADERS = [

    "timestamp",
    "soc",
    "soh",
    "voltage",
    "current",
    "temperature",
    "temperatures",
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
    "battery_state",
    "fault_active",
    "fault_count",
    "faults",
    "charge_mosfet",
    "discharge_mosfet",
    "max_cell_v",
    "min_cell_v",
    "cell_delta_mv",
    "cell_voltages",
    "tick",
    "alive",
    "source",
    "interface",
    "protocol",
    "firmware"
]

# =========================================================
# MQTT CALLBACKS
# =========================================================

def on_connect(client, userdata, flags, rc):

    if rc == 0:

        print("Connected to HiveMQ Cloud")

    else:

        print(f"MQTT Connection Failed: {rc}")


def on_disconnect(client, userdata, rc):

    print(f"Disconnected from MQTT Broker: {rc}")

# =========================================================
# MQTT CLIENT SETUP
# =========================================================

client = mqtt.Client()

client.username_pw_set(USERNAME, PASSWORD)

client.tls_set(
    cert_reqs=ssl.CERT_REQUIRED,
    tls_version=ssl.PROTOCOL_TLSv1_2
)

client.reconnect_delay_set(
    min_delay=1,
    max_delay=120
)

client.on_connect = on_connect
client.on_disconnect = on_disconnect

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

new_file = not os.path.exists(CSV_FILE)

csv_file = open(CSV_FILE, mode='a', newline='')

writer = csv.DictWriter(
    csv_file,
    fieldnames=CSV_HEADERS
)

if new_file:

    writer.writeheader()

# =========================================================
# MAIN LOOP
# =========================================================

try:

    while True:

        loop_start_time = time.time()

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

            # =================================================
            # VALIDATION
            # =================================================

            if voltage <= 0:

                print("Invalid voltage")

                continue

            if soc < 0 or soc > 100:

                print("Invalid SOC")

                continue

            # =================================================
            # MOSFET DATA
            # =================================================

            capacity_ah = BATTERY_CAPACITY_AH

            charge_mosfet = False

            discharge_mosfet = False

            if mosfet:

                capacity_ah = mosfet.get(
                    "capacity_ah",
                    BATTERY_CAPACITY_AH
                )

                charge_mosfet = mosfet.get(
                    "charging_mosfet",
                    False
                )

                discharge_mosfet = mosfet.get(
                    "discharging_mosfet",
                    False
                )

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
            # SENSOR NOISE
            # =================================================

            voltage += random.gauss(0, 0.02)

            current += random.gauss(0, 0.05)

            avg_temp += random.gauss(0, 0.2)

            # =================================================
            # POWER
            # =================================================

            power_kw = (
                voltage * current
            ) / 1000.0

            # =================================================
            # INTERNAL VOLTAGE DROP
            # =================================================

            v_drop = abs(
                current *
                BATTERY_INTERNAL_RESISTANCE
            )

            # =================================================
            # OCV ESTIMATION
            # =================================================

            ocv = voltage + v_drop

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

            dt = CSV_LOG_INTERVAL

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
            # THROUGHPUT
            # =================================================

            total_charge_throughput_ah += abs(
                current * dt / 3600.0
            )

            # =================================================
            # SOH MODEL
            # =================================================

            battery_soh = max(
                80.0,
                100.0 - (
                    (
                        total_charge_throughput_ah /
                        (
                            BATTERY_CAPACITY_AH *
                            1000
                        )
                    ) * 20
                )
            )

            # =================================================
            # ENERGY REMAINING
            # =================================================

            energy_kwh = (
                (
                    soc / 100.0
                ) *
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
                (
                    voltage / ocv
                ) * 100
            ) if ocv > 0 else 0

            # =================================================
            # C-RATE
            # =================================================

            c_rate = (
                current / capacity_ah
            ) if capacity_ah > 0 else 0

            # =================================================
            # BATTERY STATE
            # =================================================

            if current > 0:

                battery_state = "charging"

            elif current < 0:

                battery_state = "discharging"

            else:

                battery_state = "idle"

            # =================================================
            # CUSTOM FAULT DETECTION
            # =================================================

            custom_faults = []

            if max_cell_v > 4.2:

                custom_faults.append(
                    "CELL_OVERVOLT"
                )

            if min_cell_v < 2.5:

                custom_faults.append(
                    "CELL_UNDERVOLT"
                )

            if avg_temp > 60:

                custom_faults.append(
                    "OVER_TEMP"
                )

            if cell_delta_mv > 50:

                custom_faults.append(
                    "CELL_IMBALANCE"
                )

            # =================================================
            # DALY FAULTS
            # =================================================

            bms_faults = []

            if errors:

                if isinstance(errors, dict):

                    for key, value in errors.items():

                        if value:

                            bms_faults.append(key)

                else:

                    bms_faults = list(errors)

            # =================================================
            # FINAL FAULT LIST
            # =================================================

            all_faults = (
                custom_faults +
                bms_faults
            )

            fault_active = len(all_faults) > 0

            # =================================================
            # FINAL PAYLOAD
            # =================================================

            payload = {

                "timestamp":
                    datetime.utcnow().isoformat(),

                "soc":
                    round(soc, 2),

                "soh":
                    round(battery_soh, 2),

                "voltage":
                    round(voltage, 3),

                "current":
                    round(current, 3),

                "temperature":
                    round(avg_temp, 2),

                "temperatures":
                    t,

                "power_kw":
                    round(power_kw, 3),

                "capacity_ah":
                    round(capacity_ah, 2),

                "range_km":
                    round(range_km, 1),

                "ocv":
                    round(ocv, 3),

                "v_drop":
                    round(v_drop, 3),

                "internal_resistance":
                    round(
                        BATTERY_INTERNAL_RESISTANCE,
                        4
                    ),

                "heat_w":
                    round(heat_w, 2),

                "cooling_w":
                    round(cooling_w, 2),

                "temp_rise":
                    round(temp_rise, 2),

                "energy_kwh":
                    round(energy_kwh, 3),

                "throughput_ah":
                    round(
                        total_charge_throughput_ah,
                        3
                    ),

                "efficiency":
                    round(
                        efficiency,
                        2
                    ),

                "c_rate":
                    round(
                        c_rate,
                        3
                    ),

                "battery_state":
                    battery_state,

                "fault_active":
                    fault_active,

                "fault_count":
                    len(all_faults),

                "faults":
                    all_faults,

                "charge_mosfet":
                    charge_mosfet,

                "discharge_mosfet":
                    discharge_mosfet,

                "max_cell_v":
                    round(
                        max_cell_v,
                        3
                    ),

                "min_cell_v":
                    round(
                        min_cell_v,
                        3
                    ),

                "cell_delta_mv":
                    round(
                        cell_delta_mv,
                        1
                    ),

                "cell_voltages":
                    cv,

                "tick":
                    tick,

                "alive":
                    True,

                "source":
                    "DalyBMS",

                "interface":
                    "RS485",

                "protocol":
                    "MQTT",

                "firmware":
                    "1.0"
            }

            # =================================================
            # WRITE CSV
            # =================================================

            writer.writerow(payload)

            csv_file.flush()

            # =================================================
            # MQTT PUBLISH
            # =================================================

            current_time = time.time()

            if (
                current_time -
                last_mqtt_publish
            ) >= MQTT_PUBLISH_INTERVAL:

                payload_json = json.dumps(
                    payload,
                    separators=(',', ':')
                )

                result = client.publish(
                    TOPIC,
                    payload_json,
                    qos=1,
                    retain=True
                )

                if result.rc == mqtt.MQTT_ERR_SUCCESS:

                    print("\nPublished to MQTT:")
                    print(payload_json)

                else:

                    print(
                        f"MQTT Publish Failed: "
                        f"{result.rc}"
                    )

                last_mqtt_publish = current_time

            # =================================================
            # LOOP CONTROL
            # =================================================

            tick += 1

            elapsed = (
                time.time() -
                loop_start_time
            )

            remaining = (
                CSV_LOG_INTERVAL -
                elapsed
            )

            if remaining > 0:

                time.sleep(remaining)

        except Exception as e:

            print(f"Runtime Error: {e}")

            time.sleep(1)

except KeyboardInterrupt:

    print("\nStopping System...")

finally:

    try:

        client.loop_stop()

        client.disconnect()

    except:

        pass

    try:

        csv_file.close()

    except:

        pass

    print("System Shutdown Complete")