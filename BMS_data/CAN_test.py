import can
import time

def listen_to_bus(com_port):
    print(f"Listening on {com_port} for any broadcasted CAN frames...")
    
    try:
        bus = can.interface.Bus(
            interface='slcan', 
            channel=com_port, 
            ttyBaudrate=115200, 
            bitrate=500000 # Inverter protocols often default to 500kbps, not 250kbps!
        )
        
        start_time = time.time()
        frames_found = 0
        
        # Listen for 5 seconds
        while time.time() - start_time < 5.0:
            msg = bus.recv(1.0) # wait up to 1 second for a message
            if msg:
                frames_found += 1
                print(f"Heard ID: {hex(msg.arbitration_id)} | Data: {[hex(d) for d in msg.data]}")
                
        if frames_found == 0:
            print("\nBus is completely silent. Check 120-ohm resistor or BMS wake status.")
            
    except Exception as e:
        print(f"Error: {e}")
    finally:
        if 'bus' in locals():
            bus.shutdown()

if __name__ == "__main__":
    # Change to your COM port (e.g., COM7 based on your previous output)
    listen_to_bus('COM7')