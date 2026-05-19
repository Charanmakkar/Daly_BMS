from dalybms import DalyBMS
import time

def read_daly_bms(port):
    # Initialize the BMS. 
    # request_retries helps if the RS485 bus has slight noise
    bms = DalyBMS(request_retries=3)
    
    print(f"Attempting to connect to BMS on port: {port}...")
    
    try:
        # Connect using the RS485 serial port
        bms.connect(port)
        print("Connected successfully!\n")
        
        # ==========================================
        # BASIC DATA (SOC, Total Voltage, Current)
        # ==========================================
        print("--- Basic Data ---")
        soc_data = bms.get_soc()
        
        if soc_data:
            print(f"State of Charge (SOC): {soc_data.get('soc')} %")
            print(f"Total Voltage:         {soc_data.get('total_voltage')} V")
            print(f"Current:               {soc_data.get('current')} A")
        else:
            print("Failed to read basic SOC data.")

        time.sleep(0.5) # Short delay to prevent flooding the RS485 bus

        # ==========================================
        # ADVANCED DATA (Cells, Temps, Status)
        # ==========================================
        print("\n--- Advanced Data ---")
        
        # 1. Individual Cell Voltages
        cell_voltages = bms.get_cell_voltages()
        if cell_voltages:
            print("Cell Voltages:")
            # The library returns a dictionary of cells
            for cell_num, voltage in cell_voltages.items():
                print(f"  Cell {cell_num}: {voltage} V")
        
        time.sleep(0.5)
        
        # 2. Temperature Sensors
        temps = bms.get_temperatures()
        if temps:
            print("\nTemperatures:")
            # Usually returns a dict of sensors
            for sensor, temp in temps.items():
                print(f"  Sensor {sensor}: {temp} °C")
                
        time.sleep(0.5)
        
        # 3. MOSFET Status (Charging/Discharging relays)
        mosfet = bms.get_mosfet_status()
        if mosfet:
            print(f"\nMOSFET Status:")
            print(f"  Charge FET:    {'ON' if mosfet.get('mode') in ['charging', 'both'] else 'OFF'}")
            print(f"  Discharge FET: {'ON' if mosfet.get('mode') in ['discharging', 'both'] else 'OFF'}")
            print(f"  Capacity:      {mosfet.get('capacity_ah')} Ah")
            
        time.sleep(0.5)
        
        # 4. Alarms and General Status
        status = bms.get_status()
        if status:
            print("\nAlarms / Faults:")
            print(status) # Prints any active alarms like over-voltage, under-temp, etc.

    except Exception as e:
        print(f"\nAn error occurred: {e}")
        print("Check your wiring (A to A, B to B) and ensure the port is correct.")

if __name__ == "__main__":
    # CHANGE THIS to your actual port (e.g., 'COM4' or '/dev/ttyUSB0')
    RS485_PORT = 'COM6' 
    
    read_daly_bms(RS485_PORT)
