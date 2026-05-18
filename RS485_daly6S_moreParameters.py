from dalybms import DalyBMS
import time
import json

def read_all_daly_data(port):
    bms = DalyBMS(request_retries=3)
    print(f"Attempting to connect to BMS on port: {port}...")
    
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
            print(f"  Highest: {voltage_range.get('highest_voltage')}V (Cell {voltage_range.get('highest_cell')})")
            print(f"  Lowest:  {voltage_range.get('lowest_voltage')}V (Cell {voltage_range.get('lowest_cell')})")
        time.sleep(0.5)

        # 2. Temperature Ranges (Min/Max)
        temp_range = bms.get_temperature_range()
        if temp_range:
            print("\nTemperature Range:")
            print(f"  Highest: {temp_range.get('highest_temperature')}°C (Sensor {temp_range.get('highest_sensor')})")
            print(f"  Lowest:  {temp_range.get('lowest_temperature')}°C (Sensor {temp_range.get('lowest_sensor')})")
        time.sleep(0.5)

        # 3. Active Balancing Status
        balancing = bms.get_balancing_status()
        if balancing:
            print("\nCell Balancing Status:")
            # 'balancing' usually returns a dict like {1: False, 2: True, ...}
            actively_balancing = [cell for cell, is_balancing in balancing.items() if is_balancing]
            if actively_balancing:
                print(f"  Actively balancing cells: {actively_balancing}")
            else:
                print("  No cells are currently balancing.")
        time.sleep(0.5)

        # 4. Specific Errors
        errors = bms.get_errors()
        if errors:
            print("\nActive Errors:")
            # Returns a list of active error strings
            if len(errors) == 0:
                print("  No active errors. System is healthy.")
            else:
                for error in errors:
                    print(f"  - {error}")
        time.sleep(0.5)

        # ==========================================
        # THE "GET ALL" METHOD
        # ==========================================
        print("\n--- Complete BMS Data Dump ---")
        print("Fetching all parameters at once...\n")
        
        # get_all() returns a dictionary containing soc, voltages, temps, ranges, mosfet, etc.
        all_data = bms.get_all()
        
        if all_data:
            # Print it out nicely formatted as JSON
            print(json.dumps(all_data, indent=4))
        else:
            print("Failed to fetch complete data dump.")

    except Exception as e:
        print(f"\nAn error occurred: {e}")

if __name__ == "__main__":
    # CHANGE THIS to your actual port (e.g., 'COM4' or '/dev/ttyUSB0')
    RS485_PORT = 'COM6' 
    read_all_daly_data(RS485_PORT)