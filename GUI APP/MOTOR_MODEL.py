import tkinter as tk
from tkinter import ttk, messagebox
import paho.mqtt.client as mqtt
import json
import ssl

class MotorConfigApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Motor Model Parameter Publisher")
        self.root.geometry("450x650")
        
        # --- MQTT Settings Variables (Updated with your credentials) ---
        self.mqtt_broker = tk.StringVar(value="4927161c6b0c474a9aa19d86178cf2b1.s1.eu.hivemq.cloud")
        self.mqtt_port = tk.IntVar(value=8883)
        self.mqtt_user = tk.StringVar(value="bms_data")
        self.mqtt_pass = tk.StringVar(value="Praveen@81433")
        self.mqtt_topic = tk.StringVar(value="ev\motor")
        
        # --- Motor & Physics Parameters Variables ---
        # Constants from Motor_model.h
        self.aero_cd = tk.DoubleVar(value=0.23)
        self.aero_a = tk.DoubleVar(value=2.2)
        self.air_rho = tk.DoubleVar(value=1.225)
        self.c_rr = tk.DoubleVar(value=0.01)
        self.gravity = tk.DoubleVar(value=9.81)
        
        # Inferred from config.h
        self.veh_mass = tk.DoubleVar(value=1500.0)
        self.wheel_rad = tk.DoubleVar(value=0.3)
        self.gear_ratio = tk.DoubleVar(value=8.0)
        self.motor_eff = tk.DoubleVar(value=0.9)
        self.max_torque = tk.DoubleVar(value=250.0)
        self.max_rpm = tk.DoubleVar(value=12000.0)
        self.bat_v = tk.DoubleVar(value=400.0)

        self.create_widgets()

    def create_widgets(self):
        notebook = ttk.Notebook(self.root)
        notebook.pack(pady=10, expand=True, fill="both")

        # --- Tab 1: Parameters ---
        param_frame = ttk.Frame(notebook)
        notebook.add(param_frame, text="Model Parameters")
        
        params = [
            ("AERO_CD (Drag Coeff)", self.aero_cd),
            ("AERO_A (Frontal Area m²)", self.aero_a),
            ("AIR_RHO (Air Density kg/m³)", self.air_rho),
            ("C_RR (Rolling Resistance)", self.c_rr),
            ("GRAVITY (m/s²)", self.gravity),
            ("VEHICLE_MASS_KG", self.veh_mass),
            ("WHEEL_RADIUS_M", self.wheel_rad),
            ("GEAR_RATIO", self.gear_ratio),
            ("MOTOR_EFFICIENCY", self.motor_eff),
            ("MOTOR_MAX_TORQUE_NM", self.max_torque),
            ("MOTOR_MAX_RPM", self.max_rpm),
            ("BAT_NOMINAL_V", self.bat_v)
        ]

        for i, (label_text, var) in enumerate(params):
            ttk.Label(param_frame, text=label_text).grid(row=i, column=0, padx=10, pady=5, sticky="w")
            ttk.Entry(param_frame, textvariable=var).grid(row=i, column=1, padx=10, pady=5, sticky="ew")

        # --- Tab 2: MQTT Setup ---
        mqtt_frame = ttk.Frame(notebook)
        notebook.add(mqtt_frame, text="MQTT Settings")

        mqtt_settings = [
            ("Broker Address", self.mqtt_broker),
            ("Port", self.mqtt_port),
            ("Username", self.mqtt_user),
            ("Password", self.mqtt_pass, "*"),
            ("Publish Topic", self.mqtt_topic)
        ]

        for i, item in enumerate(mqtt_settings):
            ttk.Label(mqtt_frame, text=item[0]).grid(row=i, column=0, padx=10, pady=10, sticky="w")
            if len(item) == 3: # Password field
                ttk.Entry(mqtt_frame, textvariable=item[1], show=item[2]).grid(row=i, column=1, padx=10, pady=10, sticky="ew")
            else:
                ttk.Entry(mqtt_frame, textvariable=item[1]).grid(row=i, column=1, padx=10, pady=10, sticky="ew")

        # --- Publish Button ---
        btn_frame = ttk.Frame(self.root)
        btn_frame.pack(pady=10)
        
        publish_btn = ttk.Button(btn_frame, text="Publish Parameters to MQTT", command=self.publish_params)
        publish_btn.pack()

    def publish_params(self):
        # 1. Gather Data
        payload = {
            "AERO_CD": self.aero_cd.get(),
            "AERO_A": self.aero_a.get(),
            "AIR_RHO": self.air_rho.get(),
            "C_RR": self.c_rr.get(),
            "GRAVITY": self.gravity.get(),
            "VEHICLE_MASS_KG": self.veh_mass.get(),
            "WHEEL_RADIUS_M": self.wheel_rad.get(),
            "GEAR_RATIO": self.gear_ratio.get(),
            "MOTOR_EFFICIENCY": self.motor_eff.get(),
            "MOTOR_MAX_TORQUE_NM": self.max_torque.get(),
            "MOTOR_MAX_RPM": self.max_rpm.get(),
            "BAT_NOMINAL_V": self.bat_v.get()
        }
        
        json_payload = json.dumps(payload)

        # 2. Setup MQTT Client
        client = mqtt.Client(client_id="MotorConfigGUI")
        
        # Apply credentials if provided
        user = self.mqtt_user.get()
        password = self.mqtt_pass.get()
        if user and password:
            client.username_pw_set(user, password)
            
        port = self.mqtt_port.get()
        
        # Apply TLS connection for port 8883 (Required for HiveMQ Cloud)
        if port == 8883:
            client.tls_set(tls_version=ssl.PROTOCOL_TLS)

        # 3. Connect and Publish
        try:
            broker = self.mqtt_broker.get()
            topic = self.mqtt_topic.get()
            
            client.connect(broker, port, 60)
            client.loop_start()
            
            result = client.publish(topic, json_payload, qos=1)
            result.wait_for_publish()
            
            client.loop_stop()
            client.disconnect()
            
            messagebox.showinfo("Success", f"Parameters successfully published to:\n{topic}")
            
        except Exception as e:
            messagebox.showerror("MQTT Error", f"Failed to publish data.\nError: {str(e)}")


if __name__ == "__main__":
    root = tk.Tk()
    app = MotorConfigApp(root)
    root.mainloop()