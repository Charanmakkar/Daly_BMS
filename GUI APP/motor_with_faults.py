import tkinter as tk
from tkinter import ttk, messagebox
import paho.mqtt.client as mqtt
import json
import ssl

class MotorConfigApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Vehicle Telemetry & Fault Publisher")
        self.root.geometry("450x650")
        
        # --- MQTT Settings ---
        self.broker = "4927161c6b0c474a9aa19d86178cf2b1.s1.eu.hivemq.cloud"
        self.port = 8883
        self.username = "bms_data"
        self.password = "Praveen@81433"
        
        # Topics
        self.topic_motor = "ev/motor"
        self.topic_fault = "ev/fault"

        # --- Variables: Motor Telemetry ---
        self.speed_kmh = tk.DoubleVar(value=0.0)
        self.torque_nm = tk.DoubleVar(value=0.0)
        self.rpm = tk.DoubleVar(value=0.0)
        self.mech_power_kw = tk.DoubleVar(value=0.0)
        self.is_regen = tk.BooleanVar(value=False)
        self.fault_type = tk.IntVar(value=0) # 0=None, 1=Locked, 2=Overspeed
        
        # --- Variables: System Faults ---
        self.sys_faults = {
            "Cell Over Voltage": tk.BooleanVar(value=False),
            "Cell Under Voltage": tk.BooleanVar(value=False),
            "Charge Over Current": tk.BooleanVar(value=False),
            "Discharge Over Current": tk.BooleanVar(value=False),
            "Short Circuit": tk.BooleanVar(value=False),
            "Over Temperature": tk.BooleanVar(value=False),
            "Under Temperature": tk.BooleanVar(value=False),
            "MOSFET Failure": tk.BooleanVar(value=False)
        }

        self.setup_mqtt()
        self.create_widgets()
        
        # Handle window close to gracefully disconnect MQTT
        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)
        
        # Start the continuous 1-second publishing loop
        self.publish_loop()

    def setup_mqtt(self):
        """Initialize and connect the MQTT client in the background."""
        self.client = mqtt.Client(client_id="RealTimeTelemetryGUI")
        self.client.username_pw_set(self.username, self.password)
        self.client.tls_set(tls_version=ssl.PROTOCOL_TLS)
        
        try:
            self.client.connect(self.broker, self.port, 60)
            self.client.loop_start() # Start background thread for MQTT
            print("Connected to MQTT Broker successfully.")
        except Exception as e:
            messagebox.showerror("MQTT Error", f"Failed to connect to broker on startup.\n{e}")

    def create_widgets(self):
        notebook = ttk.Notebook(self.root)
        notebook.pack(pady=10, expand=True, fill="both")

        # ==========================================
        # TAB 1: SYSTEM FAULTS (Toggle Buttons)
        # ==========================================
        sys_frame = ttk.Frame(notebook)
        notebook.add(sys_frame, text="System Faults")
        
        ttk.Label(sys_frame, text="Toggle Active System Faults:", font=("Arial", 10, "bold")).pack(pady=10, anchor="w", padx=15)
        
        for name, var in self.sys_faults.items():
            cb = ttk.Checkbutton(sys_frame, text=name, variable=var, style="Toolbutton")
            cb.pack(fill="x", padx=40, pady=4)

        # ==========================================
        # TAB 2: MOTOR TELEMETRY (Sliders)
        # ==========================================
        motor_frame = ttk.Frame(notebook)
        notebook.add(motor_frame, text="Motor Model")
        
        ttk.Label(motor_frame, text="Motor Telemetry Inputs:", font=("Arial", 10, "bold")).grid(row=0, column=0, columnspan=2, pady=10, padx=10, sticky="w")
        
        # Define the ranges for the sliders
        slider_configs = [
            ("Speed (km/h):", self.speed_kmh, 0, 200),
            ("Torque (Nm):", self.torque_nm, 0, 10000),
            ("RPM:", self.rpm, 0, 10000),
            ("Mech Power (kW):", self.mech_power_kw, 0, 10000)
        ]
        
        # Helper function to generate an isolated command for each slider's label update
        def make_slider_cmd(label_widget):
            return lambda val: label_widget.config(text=f"{float(val):.1f}")
        
        for i, (label_txt, var, min_val, max_val) in enumerate(slider_configs, start=1):
            # Label for the parameter name
            ttk.Label(motor_frame, text=label_txt).grid(row=i, column=0, padx=15, pady=10, sticky="w")
            
            # Sub-frame to hold both the slider and the live value text
            slider_frame = ttk.Frame(motor_frame)
            slider_frame.grid(row=i, column=1, padx=15, pady=10, sticky="ew")
            
            # Dynamic value label (right side)
            val_label = ttk.Label(slider_frame, text=f"{var.get():.1f}", width=6, anchor="e")
            val_label.pack(side="right", padx=5)
            
            # The Slider (Scale)
            slider = ttk.Scale(
                slider_frame, 
                from_=min_val, 
                to=max_val, 
                orient="horizontal", 
                variable=var, 
                command=make_slider_cmd(val_label)
            )
            slider.pack(side="left", fill="x", expand=True)
            
        # Regen Toggle
        ttk.Checkbutton(motor_frame, text="Toggle Regen Active", variable=self.is_regen, style="Toolbutton").grid(row=5, column=0, columnspan=2, padx=40, pady=15, sticky="ew")

        # Motor Faults (Radio Buttons)
        ttk.Label(motor_frame, text="Motor Fault Type:", font=("Arial", 10, "bold")).grid(row=6, column=0, columnspan=2, pady=5, padx=10, sticky="w")
        
        ttk.Radiobutton(motor_frame, text="MOT_FAULT_NONE (0)", variable=self.fault_type, value=0).grid(row=7, column=0, columnspan=2, padx=30, pady=2, sticky="w")
        ttk.Radiobutton(motor_frame, text="MOT_FAULT_LOCKED_ROTOR (1)", variable=self.fault_type, value=1).grid(row=8, column=0, columnspan=2, padx=30, pady=2, sticky="w")
        ttk.Radiobutton(motor_frame, text="MOT_FAULT_OVERSPEED (2)", variable=self.fault_type, value=2).grid(row=9, column=0, columnspan=2, padx=30, pady=2, sticky="w")

        # Add a status label at the bottom of the main window
        self.status_label = ttk.Label(self.root, text="Status: Starting...", foreground="orange")
        self.status_label.pack(side="bottom", pady=5)

    def publish_loop(self):
        """Constructs the JSON and pushes to the respective MQTT topics every 1 second."""
        try:
            # 1. Prepare Motor Payload
            fault_val = self.fault_type.get()
            motor_payload = {
                "speed_kmh": round(self.speed_kmh.get(), 2),
                "torque_nm": round(self.torque_nm.get(), 2),
                "rpm": round(self.rpm.get(), 2),
                "mech_power_kw": round(self.mech_power_kw.get(), 2),
                "is_regen": self.is_regen.get(),
                "fault_active": fault_val > 0, 
                "fault_type": fault_val
            }
            
            # 2. Prepare System Faults Payload
            fault_payload = {
                name: var.get() for name, var in self.sys_faults.items()
            }
            
            # 3. Publish to both topics
            self.client.publish(self.topic_motor, json.dumps(motor_payload), qos=1)
            self.client.publish(self.topic_fault, json.dumps(fault_payload), qos=1)
            
            # Toggle an asterisk to show the loop is actively running
            current_text = self.status_label.cget("text")
            if current_text.endswith("*"):
                self.status_label.config(text="Status: Auto-publishing every 1s", foreground="green")
            else:
                self.status_label.config(text="Status: Auto-publishing every 1s *", foreground="green")
            
        except Exception as e:
            self.status_label.config(text=f"Status: Error - {e}", foreground="red")
            
        # Schedule this function to run again in 1000 milliseconds (1 second)
        self.root.after(1000, self.publish_loop)

    def on_closing(self):
        """Clean up the MQTT connection on exit."""
        self.client.loop_stop()
        self.client.disconnect()
        self.root.destroy()

if __name__ == "__main__":
    root = tk.Tk()
    app = MotorConfigApp(root)
    root.mainloop()