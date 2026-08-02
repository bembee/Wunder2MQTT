import os
import json
import logging
import time
from flask import Flask, request
from waitress import serve
import paho.mqtt.client as mqtt

logging.basicConfig(
    format='%(asctime)s - %(levelname)s - %(message)s',
    level=logging.INFO,
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)
logging.getLogger('werkzeug').setLevel(logging.ERROR)

app = Flask(__name__)

def parse_value(raw_val):
    try:
        return int(raw_val)
    except ValueError:
        pass
    try:
        return float(raw_val)
    except ValueError:
        pass
    return raw_val

def get_supervisor_mqtt():
    token = os.getenv("SUPERVISOR_TOKEN")
    if not token:
        return None
    try:
        import urllib.request
        req = urllib.request.Request("http://supervisor/services/mqtt")
        req.add_header("Authorization", f"Bearer {token}")
        with urllib.request.urlopen(req) as response:
            result = json.loads(response.read().decode())
            if result.get("result") == "ok":
                return result.get("data")
    except Exception as e:
        logger.error(f"Error fetching Supervisor API MQTT data: {e}")
    return None

OPTIONS_FILE = "/data/options.json"
options = {}

if os.path.exists(OPTIONS_FILE):
    try:
        with open(OPTIONS_FILE, "r") as f:
            options = json.load(f)
    except Exception as e:
        logger.error(f"Error reading configuration file: {e}")

DEVICE_NAME = options.get("device_name", "Universal Weather Station")
MANUFACTURER = options.get("manufacturer", "Generic")
DEVICE_MODEL = options.get("device_model", "Weather Station")
DEVICE_IDENTIFIER = options.get("device_identifier", "wunderground_weather_station")
STATION_ID = options.get("station_id", "")
STATION_PASSWORD = options.get("station_password", "")
LOG_RAW_DATA = options.get("log_raw_data", False)
SENSORS = options.get("sensors", [])

DEVICE_INFO = {
    "identifiers": [DEVICE_IDENTIFIER],
    "name": DEVICE_NAME,
    "manufacturer": MANUFACTURER,
    "model": DEVICE_MODEL
}

STATE_TOPIC = f"{DEVICE_IDENTIFIER}/state"

mqtt_config = get_supervisor_mqtt()
if mqtt_config:
    MQTT_HOST = mqtt_config.get("host")
    MQTT_PORT = int(mqtt_config.get("port", 1883))
    MQTT_USER = mqtt_config.get("username")
    MQTT_PASSWORD = mqtt_config.get("password")
else:
    MQTT_HOST, MQTT_PORT, MQTT_USER, MQTT_PASSWORD = "core-mosquitto", 1883, "", ""

def publish_discovery():
    for s in SENSORS:
        topic = f"homeassistant/sensor/{DEVICE_IDENTIFIER}/{s['id']}/config"
        payload = {
            "name": s["name"],
            "unique_id": f"{DEVICE_IDENTIFIER}_{s['id']}",
            "state_topic": STATE_TOPIC,
            "value_template": f"{{{{ value_json.{s['id']} }}}}",
            "device": DEVICE_INFO,
            "expire_after": 1800 
        }
        
        if s.get("unit"): 
            payload["unit_of_measurement"] = s["unit"]

        dev_class = s.get("class")
        if dev_class and dev_class != "none":
            payload["device_class"] = dev_class
            
        st_class = s.get("state_class")
        if st_class and st_class != "none":
            payload["state_class"] = st_class
        elif not st_class and dev_class not in ["none", "timestamp", "wind_direction"]:
            payload["state_class"] = "measurement"

        client.publish(topic, json.dumps(payload), retain=True)

def on_connect(client, userdata, flags, reason_code, properties):
    if not reason_code.is_failure:
        logger.info("Connected to MQTT broker. Publishing discovery configurations...")
        publish_discovery()
    else:
        logger.error(f"Failed to connect to MQTT broker, reason code: {reason_code}")

client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
if MQTT_USER and MQTT_PASSWORD:
    client.username_pw_set(MQTT_USER, MQTT_PASSWORD)
client.on_connect = on_connect

connected = False
while not connected:
    try:
        client.connect(MQTT_HOST, MQTT_PORT, 60)
        connected = True
    except Exception as e:
        logger.warning(f"Waiting for MQTT broker to become available ({MQTT_HOST}:{MQTT_PORT}). Retrying in 5 seconds...")
        time.sleep(5)

client.loop_start()

@app.route("/health", methods=["GET"])
def health_check():
    return "OK", 200

@app.route("/", defaults={"path": ""}, methods=["GET"])
@app.route("/<path:path>", methods=["GET"])
def catch_all(path):
    try:
        data = request.args.to_dict()
        
        if LOG_RAW_DATA:
            log_data = data.copy()
            if "PASSWORD" in log_data: log_data["PASSWORD"] = "***HIDDEN***"
            logger.info(f"Raw data received: {json.dumps(log_data)}")
        
        if STATION_ID and data.get("ID") != STATION_ID:
            logger.warning(f"Unauthorized access attempt (Invalid Station ID): {data.get('ID')}")
            return "Unauthorized", 401
        if STATION_PASSWORD and data.get("PASSWORD") != STATION_PASSWORD:
            logger.warning("Unauthorized access attempt (Invalid Password)")
            return "Unauthorized", 401

        processed_data = {}
        
        for s in SENSORS:
            in_key = s.get("incoming_key")
            out_id = s.get("id")
            
            if in_key and in_key in data:
                raw_val = data[in_key]
                
                if in_key == "dateutc":
                    processed_data[out_id] = str(raw_val).replace(' ', 'T') + 'Z'
                    continue

                processed_data[out_id] = parse_value(raw_val)

        if LOG_RAW_DATA:
            known_keys = {s.get("incoming_key") for s in SENSORS}
            system_fields = {"ID", "PASSWORD", "action", "realtime"}
            unhandled_data = {k: v for k, v in data.items() if k not in known_keys and k not in system_fields}
            
            if unhandled_data:
                logger.info(f"Unhandled parameters: {json.dumps(unhandled_data)}")

        if processed_data:
            client.publish(STATE_TOPIC, json.dumps(processed_data))
            
        return "success", 200

    except Exception as e:
        logger.error(f"Error processing incoming request: {e}")
        return "Internal Error", 500

if __name__ == '__main__':
    logger.info(f"[{DEVICE_NAME}] Add-on started successfully.")
    logger.info("Listening for weather station traffic on internal port 80...")
    serve(app, host='0.0.0.0', port=80)