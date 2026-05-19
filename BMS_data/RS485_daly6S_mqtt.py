import json
import time
import ssl
import paho.mqtt.client as mqtt
from dalybms import DalyBMS

# ==========================================
# MQTT CONFIGURATION
# ==========================================

BROKER = "4927161c6b0c474a9aa19d86178cf2b1.s1.eu.hivemq.cloud"
PORT = 8883

USERNAME = "bms_data"
PASSWORD = "Praveen@81433"

TOPIC = "bms/rt"

# ==========================================
# DALY BMS CONFIGURATION
# ==========================================

RS485_PORT = "COM8"

# ==========================================
# MQTT CALLBACKS
# ==========================================

def on_connect(client, userdata, flags, rc):
    if rc == 0:
        print("MQTT Connected")
    else:
        print(f"MQTT Connection Failed: {rc}")

# ==========================================
# MQTT CLIENT SETUP
# ==========================================

client = mqtt.Client()

client.username_pw_set(USERNAME, PASSWORD)

client.tls_set(
    cert_reqs=ssl.CERT_REQUIRED,
    tls_version=ssl.PROTOCOL_TLSv1_2
)

client.on_connect = on_connect

print("Connecting MQTT...")
client.connect(BROKER, PORT, 60)

client.loop_start()

# ==========================================
# DALY BMS SETUP
# ==========================================

bms = DalyBMS(request_retries=3)

print(f"Connecting to Daly BMS on {RS485_PORT}...")

try:
    bms.connect(RS485_PORT)
    print("BMS Connected Successfully")

except Exception as e:
    print(f"BMS Connection Failed: {e}")
    exit()

# ==========================================
# MAIN LOOP
# ==========================================

while True:

    try:

        # -------------------------------
        # BASIC DATA
        # -------------------------------

        soc = bms.get_soc()
        cell_voltages = bms.get_cell_voltages()
        temps = bms.get_temperatures()

        # -------------------------------
        # SAFETY CHECKS
        # -------------------------------

        if not soc:
            print("Failed to read SOC")
            time.sleep(1)
            continue

        # -------------------------------
        # OPTIMIZED PAYLOAD
        # -------------------------------

        payload = {

            # Total Voltage *100
            "v": int(soc.get("total_voltage", 0) * 100),

            # Current *10
            "i": int(soc.get("current", 0) * 10),

            # SOC *10
            "s": int(soc.get("soc_percent", 0) * 10),

            # Cell Voltages in mV
            "cv": [
                int(v * 1000)
                for v in cell_voltages.values()
            ] if cell_voltages else [],

            # Temperatures
            "t": list(temps.values()) if temps else []
        }

        # -------------------------------
        # MINIFIED JSON
        # -------------------------------

        payload_json = json.dumps(
            payload,
            separators=(',', ':')
        )

        # -------------------------------
        # MQTT PUBLISH
        # -------------------------------

        client.publish(TOPIC, payload_json)

        print(payload_json)

        # -------------------------------
        # SEND RATE
        # -------------------------------

        time.sleep(1)

    except Exception as e:

        print(f"Runtime Error: {e}")

        time.sleep(2)