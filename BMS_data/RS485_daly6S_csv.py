from dalybms import DalyBMS
import time
import json
import csv
import os
from datetime import datetime

CSV_FILE = "daly_bms_log.csv"


def create_csv_file():
    """
    Create CSV file with headers if it doesn't exist
    """
    if not os.path.exists(CSV_FILE):
        with open(CSV_FILE, mode='w', newline='') as file:
            writer = csv.writer(file)

            headers = [
                "Timestamp",
                "Pack Voltage(V)",
                "Pack Current(A)",
                "SOC(%)",
                "MOSFET Temp(C)",
                "Highest Cell Voltage(V)",
                "Highest Cell Number",
                "Lowest Cell Voltage(V)",
                "Lowest Cell Number",
                "Highest Temperature(C)",
                "Highest Temp Sensor",
                "Lowest Temperature(C)",
                "Lowest Temp Sensor",
                "Balancing Cells",
                "Errors"
            ]

            writer.writerow(headers)


def save_to_csv(all_data,
                voltage_range,
                temp_range,
                balancing,
                errors):
    """
    Save BMS data to CSV
    """

    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # Default values
    pack_voltage = ""
    pack_current = ""
    soc = ""
    mosfet_temp = ""

    # Extract main values safely
    if all_data:
        pack_voltage = all_data.get("soc", {}).get("total_voltage", "")
        pack_current = all_data.get("soc", {}).get("current", "")
        soc = all_data.get("soc", {}).get("soc_percent", "")
        mosfet_temp = all_data.get("mosfet_status", {}).get("temperature", "")

    # Voltage range values
    highest_voltage = ""
    highest_cell = ""
    lowest_voltage = ""
    lowest_cell = ""

    if voltage_range:
        highest_voltage = voltage_range.get("highest_voltage", "")
        highest_cell = voltage_range.get("highest_cell", "")
        lowest_voltage = voltage_range.get("lowest_voltage", "")
        lowest_cell = voltage_range.get("lowest_cell", "")

    # Temperature range values
    highest_temp = ""
    highest_sensor = ""
    lowest_temp = ""
    lowest_sensor = ""

    if temp_range:
        highest_temp = temp_range.get("highest_temperature", "")
        highest_sensor = temp_range.get("highest_sensor", "")
        lowest_temp = temp_range.get("lowest_temperature", "")
        lowest_sensor = temp_range.get("lowest_sensor", "")

    # Balancing cells
    balancing_cells = ""

    if balancing:
        active_cells = [
            str(cell)
            for cell, status in balancing.items()
            if status
        ]
        balancing_cells = ",".join(active_cells)

    # Errors
    error_string = ""

    if errors:
        error_string = " | ".join(errors)

    # Write row
    with open(CSV_FILE, mode='a', newline='') as file:
        writer = csv.writer(file)

        writer.writerow([
            timestamp,
            pack_voltage,
            pack_current,
            soc,
            mosfet_temp,
            highest_voltage,
            highest_cell,
            lowest_voltage,
            lowest_cell,
            highest_temp,
            highest_sensor,
            lowest_temp,
            lowest_sensor,
            balancing_cells,
            error_string
        ])


def read_all_daly_data(port):

    bms = DalyBMS(request_retries=3)

    print(f"\nAttempting to connect to BMS on port: {port}...")

    try:
        bms.connect(port)

        print("Connected successfully!\n")

        # ==========================================
        # NEW SPECIFIC ADVANCED DATA
        # ==========================================

        print("--- Min/Max & Balancing Data ---")

        # 1. Cell Voltage Ranges (Min/Max)
        voltage_range = bms.get_cell_voltage_range()

        if voltage_range:
            print("Voltage Range:")
            print(
                f"  Highest: {voltage_range.get('highest_voltage')}V "
                f"(Cell {voltage_range.get('highest_cell')})"
            )
            print(
                f"  Lowest:  {voltage_range.get('lowest_voltage')}V "
                f"(Cell {voltage_range.get('lowest_cell')})"
            )

        time.sleep(0.5)

        # 2. Temperature Ranges (Min/Max)
        temp_range = bms.get_temperature_range()

        if temp_range:
            print("\nTemperature Range:")
            print(
                f"  Highest: {temp_range.get('highest_temperature')}°C "
                f"(Sensor {temp_range.get('highest_sensor')})"
            )
            print(
                f"  Lowest:  {temp_range.get('lowest_temperature')}°C "
                f"(Sensor {temp_range.get('lowest_sensor')})"
            )

        time.sleep(0.5)

        # 3. Active Balancing Status
        balancing = bms.get_balancing_status()

        if balancing:
            print("\nCell Balancing Status:")

            actively_balancing = [
                cell
                for cell, is_balancing in balancing.items()
                if is_balancing
            ]

            if actively_balancing:
                print(f"  Actively balancing cells: {actively_balancing}")
            else:
                print("  No cells are currently balancing.")

        time.sleep(0.5)

        # 4. Specific Errors
        errors = bms.get_errors()

        if errors is not None:
            print("\nActive Errors:")

            if len(errors) == 0:
                print("  No active errors. System is healthy.")
            else:
                for error in errors:
                    print(f"  - {error}")

        time.sleep(0.5)

        # ==========================================
        # COMPLETE DATA DUMP
        # ==========================================

        print("\n--- Complete BMS Data Dump ---")
        print("Fetching all parameters at once...\n")

        all_data = bms.get_all()

        if all_data:
            print(json.dumps(all_data, indent=4))
        else:
            print("Failed to fetch complete data dump.")

        # ==========================================
        # SAVE DATA TO CSV
        # ==========================================

        save_to_csv(
            all_data,
            voltage_range,
            temp_range,
            balancing,
            errors
        )

        print(f"\nData saved to: {CSV_FILE}")

    except Exception as e:
        print(f"\nAn error occurred: {e}")


if __name__ == "__main__":

    # CHANGE THIS to your actual port
    # Example: 'COM4' or '/dev/ttyUSB0'
    RS485_PORT = 'COM8'

    # Create CSV with headers
    create_csv_file()

    while True:

        read_all_daly_data(RS485_PORT)

        # Logging interval
        time.sleep(0.2)
