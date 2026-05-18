import can
import time

def decode_basic_data(data):
    """Decodes CAN frame 0x18900140 (Voltage, Current, SOC)"""
    # Byte 0-1: Total Voltage (0.1V per bit)
    voltage = ((data[0] << 8) | data[1]) / 10.0
    
    # Byte 4-5: Current (0.1A per bit, 30000 offset)
    # 30000 means 0A. > 30000 is charging, < 30000 is discharging
    current_raw = (data[4] << 8) | data[5]
    current = (current_raw - 30000) / 10.0
    
    # Byte 6-7: SOC (0.1% per bit)
    soc = ((data[6] << 8) | data[7]) / 10.0
    
    print("\n--- Basic Data ---")
    print(f"Total Voltage : {voltage} V")
    print(f"Current       : {current} A")
    print(f"SOC           : {soc} %")

def decode_temperature_data(data):
    """Decodes CAN frame 0x18920140 (Min/Max Temperatures)"""
    # Temperatures have a 40°C offset (40 means 0°C)
    max_temp = data[0] - 40
    max_sensor_num = data[1]
    min_temp = data[2] - 40
    min_sensor_num = data[3]
    
    print("\n--- Temperature Data ---")
    print(f"Max Temp: {max_temp} °C (Sensor {max_sensor_num})")
    print(f"Min Temp: {min_temp} °C (Sensor {min_sensor_num})")

def request_can_data(bus, can_id):
    """Sends a request frame and waits for the response"""
    # Create the request message (8 bytes of 0x00)
    msg = can.Message(
        arbitration_id=can_id,
        data=[0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00],
        is_extended_id=True
    )
    
    try:
        bus.send(msg)
        # Wait up to 1 second for a response
        response = bus.recv(1.0) 
        
        if response and response.arbitration_id == can_id:
            return response.data
        else:
            print(f"No valid response received for ID: {hex(can_id)}")
            return None
            
    except can.CanError as e:
        print(f"CAN Bus error: {e}")
        return None

def main():
    # ---------------------------------------------------------
    # WAVESHARE SETUP:
    # Depending on your exact Waveshare model, the interface differs.
    # If it acts as a COM port (SLCAN compatible), use:
    # interface='slcan', channel='COM3', bitrate=250000
    #
    # If you are on Linux and it shows up as a native CAN interface:
    # interface='socketcan', channel='can0', bitrate=250000
    # ---------------------------------------------------------
    
    print("Initializing CAN interface...")
    try:
        # Adjust 'channel' and 'interface' for your specific OS/adapter setup
        bus = can.interface.Bus(bustype='slcan', channel='COM7', bitrate=250000)
        
        # 1. Request Basic Data (ID: 0x18900140)
        print("Requesting Basic Data...")
        basic_payload = request_can_data(bus, 0x18900140)
        if basic_payload:
            decode_basic_data(basic_payload)
            
        time.sleep(0.1) # Small delay between requests
        
        # 2. Request Temp Data (ID: 0x18920140)
        print("\nRequesting Temperature Data...")
        temp_payload = request_can_data(bus, 0x18920140)
        if temp_payload:
            decode_temperature_data(temp_payload)

    except Exception as e:
        print(f"Failed to connect to CAN bus: {e}")
    finally:
        if 'bus' in locals():
            bus.shutdown()

if __name__ == "__main__":
    main()