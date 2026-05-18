import can
import time

def request_and_listen(bus, can_id):
    """Sends a request frame and listens for ANY response"""
    msg = can.Message(
        arbitration_id=can_id,
        data=[0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00],
        is_extended_id=True
    )
    
    try:
        print(f"-> Sending Request to {hex(can_id)}...")
        bus.send(msg)
        
        # Listen for up to 2 seconds for any frame
        start_time = time.time()
        frames_heard = 0
        
        while time.time() - start_time < 2.0:
            response = bus.recv(0.5) 
            if response:
                frames_heard += 1
                # If we get the exact response we asked for
                if response.arbitration_id == can_id:
                    print(f"<- SUCCESS! Received Daly Default Reply: {[hex(d) for d in response.data]}")
                # If we hear Pylontech/Inverter broadcast frames instead
                elif response.arbitration_id in [0x351, 0x355, 0x356, 0x359]:
                    print(f"<- Heard Inverter Broadcast ID {hex(response.arbitration_id)}: {[hex(d) for d in response.data]}")
                    print("   (Your BMS is in Inverter/Pylontech mode, not Daly Default mode!)")
                else:
                    print(f"<- Heard Unknown ID: {hex(response.arbitration_id)}")
                    
        if frames_heard == 0:
            print("<- Bus is silent. No response received.")

    except can.CanError as e:
        print(f"CAN Bus error: {e}")

def main():
    print("Initializing CANable gs_usb interface...")
    
    try:
        # channel=0 grabs the first connected gs_usb device
        bus = can.interface.Bus(
            interface='gs_usb', 
            channel=0, 
            bitrate=500000 # Try 500000 if 250000 is completely silent
        )
        
        print("Connected! Testing communication...")
        request_and_listen(bus, 0x18900140)

    except Exception as e:
        print(f"\n[Error] {e}")
        print("If you get a 'No Backend' or USB error, you likely need to use Zadig to install the WinUSB driver.")
    finally:
        if 'bus' in locals():
            bus.shutdown()
            print("\nBus connection closed.")

if __name__ == "__main__":
    main()