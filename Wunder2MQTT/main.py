import os
import json
import logging
import time
import queue
import ssl
import threading
import urllib.parse
import urllib.request
from datetime import datetime, timezone
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


class RejectRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class WundergroundForwarder:
    URL = "https://rtupdate.wunderground.com/weatherstation/updateweatherstation.php"

    def __init__(self):
        self.pending = queue.Queue(maxsize=100)
        self.enqueue_lock = threading.Lock()
        self.stopped = threading.Event()
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPSHandler(context=ssl.create_default_context()),
            RejectRedirects(),
        )
        self.worker = threading.Thread(target=self.run, name="wunderground-upload", daemon=True)

    def start(self):
        self.worker.start()

    def stop(self):
        self.stopped.set()
        self.worker.join(timeout=6)

    def enqueue(self, data, received_at):
        if not data.get("ID", "").strip() or not data.get("PASSWORD", "").strip():
            logger.warning("Weather Underground upload skipped: missing station credentials.")
            return
        observation = data.copy()
        observation.update(action="updateraw", realtime="1")
        observation.setdefault("rtfreq", "2.5")
        if not observation.get("dateutc") or observation["dateutc"].lower() == "now":
            observation["dateutc"] = received_at.strftime("%Y-%m-%d %H:%M:%S")
        # Serialize producers so concurrent requests consistently drop the oldest item.
        with self.enqueue_lock:
            if self.pending.full():
                try:
                    self.pending.get_nowait()
                    self.pending.task_done()
                    logger.warning("Weather Underground upload queue full: oldest observation discarded.")
                except queue.Empty:
                    pass
            self.pending.put_nowait(observation)

    def upload(self, observation):
        try:
            url = self.URL + "?" + urllib.parse.urlencode(observation)
            with self.opener.open(url, timeout=5) as response:
                if 200 <= response.status < 300 and response.read(1024).strip() == b"success":
                    return
            logger.warning("Weather Underground rejected an observation.")
        except Exception:
            # Exceptions and upstream bodies can contain the credential-bearing URL.
            logger.warning("Weather Underground upload failed; observation discarded.")

    def run(self):
        while not self.stopped.is_set():
            try:
                observation = self.pending.get(timeout=0.5)
            except queue.Empty:
                continue
            try:
                self.upload(observation)
            finally:
                self.pending.task_done()

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
WUNDERGROUND_FORWARDING = options.get("wunderground_forwarding", False)
SENSORS = options.get("sensors", [])
client = None
forwarder = None

DEVICE_INFO = {
    "identifiers": [DEVICE_IDENTIFIER],
    "name": DEVICE_NAME,
    "manufacturer": MANUFACTURER,
    "model": DEVICE_MODEL
}

STATE_TOPIC = f"{DEVICE_IDENTIFIER}/state"

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

@app.route("/health", methods=["GET"])
def health_check():
    return "OK", 200

@app.route("/", defaults={"path": ""}, methods=["GET"])
@app.route("/<path:path>", methods=["GET"])
def catch_all(path):
    try:
        data = request.args.to_dict()
        received_at = datetime.now(timezone.utc)
        
        if LOG_RAW_DATA:
            log_data = data.copy()
            for key in ("ID", "PASSWORD"):
                if key in log_data:
                    log_data[key] = "***HIDDEN***"
            logger.info(f"Raw data received: {json.dumps(log_data)}")
        
        if STATION_ID and data.get("ID") != STATION_ID:
            logger.warning("Unauthorized access attempt (Invalid Station ID)")
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
                    if raw_val.lower() == "now":
                        raw_val = received_at.strftime("%Y-%m-%d %H:%M:%S")
                    processed_data[out_id] = str(raw_val).replace(' ', 'T') + 'Z'
                    continue

                processed_data[out_id] = parse_value(raw_val)

        if LOG_RAW_DATA:
            known_keys = {s.get("incoming_key") for s in SENSORS}
            system_fields = {"ID", "PASSWORD", "action", "realtime"}
            unhandled_data = {k: v for k, v in log_data.items() if k not in known_keys and k not in system_fields}
            
            if unhandled_data:
                logger.info(f"Unhandled parameters: {json.dumps(unhandled_data)}")

        if processed_data:
            client.publish(STATE_TOPIC, json.dumps(processed_data))

        if WUNDERGROUND_FORWARDING and forwarder is not None:
            forwarder.enqueue(data, received_at)
            
        return "success", 200

    except Exception:
        logger.error("Error processing incoming request.")
        return "Internal Error", 500

def main():
    global client, forwarder
    mqtt_config = get_supervisor_mqtt() or {}
    mqtt_host = mqtt_config.get("host", "core-mosquitto")
    mqtt_port = int(mqtt_config.get("port", 1883))
    mqtt_user = mqtt_config.get("username", "")
    mqtt_password = mqtt_config.get("password", "")
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    if mqtt_user and mqtt_password:
        client.username_pw_set(mqtt_user, mqtt_password)
    client.on_connect = on_connect
    while True:
        try:
            client.connect(mqtt_host, mqtt_port, 60)
            break
        except Exception:
            logger.warning("Waiting for MQTT broker (%s:%s). Retrying in 5 seconds...", mqtt_host, mqtt_port)
            time.sleep(5)
    client.loop_start()
    try:
        if WUNDERGROUND_FORWARDING:
            forwarder = WundergroundForwarder()
            forwarder.start()
        logger.info(f"[{DEVICE_NAME}] Add-on started successfully.")
        logger.info("Listening for weather station traffic on internal port 80...")
        serve(app, host='0.0.0.0', port=80)
    finally:
        if forwarder is not None:
            forwarder.stop()
        client.disconnect()
        client.loop_stop()


if __name__ == '__main__':
    main()
