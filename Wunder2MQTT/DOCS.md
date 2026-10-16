# Wunder2MQTT Documentation

**Wunder2MQTT** acts as a local HTTP server that receives weather data updates sent by weather stations using the Weather Underground (Wunderground) protocol, processes them, and seamlessly publishes them to Home Assistant via MQTT.

---

## Prerequisites

Before installing and using this app, make sure you have:

* **Working MQTT Broker:** A running MQTT integration/broker in Home Assistant (such as the official **Mosquitto broker** app). The app uses MQTT to automatically discover entities and update sensor states in real time.

### Network & DNS Redirection Setup

If your weather station console does **not** allow configuring a custom server IP/URL directly and hardcodes `weatherstation.wunderground.com` (or similar endpoints), you must redirect the station's DNS traffic to your Home Assistant instance.

### Tested Setup Guide

1. **Dedicated Access Point:** Connect a compatible inexpensive USB Wi-Fi adapter to your Home Assistant hardware and use the [Hass.io Access Point](https://github.com/ex-ml/hassio-access-point) app to create a dedicated wireless network for the weather station.
2. **DNS Hijacking / Redirection:** Use the **Dnsmasq** app to override `weatherstation.wunderground.com` (and the `/weatherstation/updateweatherstation.php` endpoint) so that all traffic points directly to your Home Assistant IP address.

---

## How It Works (Technical Overview)

1. **Local HTTP Server (Flask & Waitress):**
   * The app runs a Python-based **Flask** server served by **Waitress** on internal port `80`.
   * Weather stations send periodic HTTP `GET` requests containing real-time weather metrics formatted as URL parameters (e.g., `tempf`, `humidity`, `baromin`, `dateutc`).
   * The server listens to all incoming paths, meaning it seamlessly captures requests sent to default Wunderground endpoints such as `/weatherstation/updateweatherstation.php` or the root `/`.

2. **Data Parsing & Formatting:**
   * When data arrives, the engine verifies optional authentication credentials (`ID` and `PASSWORD`).
   * Time values (`dateutc`) are dynamically formatted into standard ISO 8601 UTC strings (`YYYY-MM-DDTHH:MM:SSZ`).
   * Native weather metrics (Fahrenheit, mph, inHg, inches) are passed directly to Home Assistant. Home Assistant automatically performs unit conversions based on your user profile settings (e.g., °F to °C, mph to km/h).

3. **MQTT Discovery & Publishing:**
   * Upon startup or reconnection, the app automatically publishes MQTT Auto-Discovery configuration payloads to Home Assistant (`homeassistant/sensor/...`).
   * Parsed data is published to a single MQTT state topic (`<device_identifier>/state`) in JSON format.
   * Home Assistant automatically updates all matching entities in real time without requiring manual configuration in Home Assistant itself.

---

## Configuration Options

1. **Device Name / Manufacturer / Model:** Define how your weather station and its entities appear in the Home Assistant Device Registry.
2. **Station ID & Password:** *(Optional)* If your weather station or console requires authentication, specify the exact credentials configured on your device.
3. **Log Raw Data:** Enable this option to log incoming raw HTTP requests and unhandled sensor keys in the app logs (ideal for debugging or finding extra sensors like soil moisture or solar radiation).
4. **Sensors Configuration:**
   * Custom mapping for individual sensors (id, name, incoming key, unit).
   * **Device Class & State Class:** Custom dropdown selection for Home Assistant entity mapping.
5. **Forward to Weather Underground:** `wunderground_forwarding` (default `false`) enables optional cloud uploads alongside MQTT.

## Optional Weather Underground Forwarding

1. Register a Personal Weather Station in your Weather Underground account and obtain its station ID and station key. Use the station key, not your account password.
2. Configure the weather console to include that ID as `ID` and key as `PASSWORD` in its requests to this app.
3. Enable **Forward to Weather Underground** in the app configuration and restart the app.

The existing `station_id` and `station_password` options remain optional incoming authentication filters. They do not supply or replace cloud credentials. Requests that fail local authentication are never uploaded. If either incoming credential is empty, the upload is skipped with a warning and local processing continues.

Each accepted observation is queued after MQTT publishing, even if none of its fields are mapped to Home Assistant sensors. Original fields and native units are preserved and sent using URL-encoded HTTPS GET requests to `https://rtupdate.wunderground.com/weatherstation/updateweatherstation.php`, with `action=updateraw` and `realtime=1`. Incoming `rtfreq` is preserved, defaulting to `2.5`. Configure your station to send no faster than once every 2.5 seconds, as described in the [official protocol](https://support.weather.com/s/article/PWS-Upload-Protocol).

Explicit `dateutc` timestamps are preserved for cloud uploads. Missing timestamps and `dateutc=now` use the UTC receipt time so queue delays do not change the observation time. MQTT also converts `dateutc=now` to a valid UTC timestamp.

One background worker handles uploads with a five-second network timeout and a queue of up to 100 waiting observations. If the queue fills, the oldest waiting observation is discarded with a warning. Failed uploads are logged without credentials or upstream response bodies; there are no retries or disk storage, and queued observations are lost on restart. Only a successful HTTP response containing `success` counts as ingestion. Cloud failures do not change the local `success` acknowledgment or block MQTT publishing. TLS certificates are verified and redirects are rejected.

**DNS routing:** Restrict DNS redirection to the weather station's network. The add-on must resolve `rtupdate.wunderground.com` to Weather Underground, rather than Home Assistant. Permit outbound HTTPS on port 443. No custom DNS bypass is provided.

Enabling this option shares weather observations with Weather Underground. Leave it disabled for local-only operation. `/health` requests never upload data.

### Development validation

Install `Wunder2MQTT/requirements-dev.txt` in a Python virtual environment, then run `python -m unittest discover -s Wunder2MQTT/tests -v` from the repository root. Tests mock MQTT and HTTP uploads; no observations are sent to Weather Underground.

---

## Weather Station Console Setup

On your weather station's configuration app (e.g., *WS View Plus*, *Ecowitt*, or local web interface), configure a **Custom / Weather Underground** server destination:

* **Server / Host / IP:** The local IP address of your Home Assistant instance (or point your router's / Dnsmasq DNS for `weatherstation.wunderground.com` to your Home Assistant IP).
* **Port:** `80` (or the custom host port you mapped in the app settings).
* **Path:** `/weatherstation/updateweatherstation.php` (or simply `/`).
* **Station ID / Password:** Match the credentials if configured in the app options.

> **Note:** Weather stations like the **Raddy WF-100C** (and similar hardcoded consoles) only ask for Wi-Fi credentials during setup and do not allow entering a custom server IP or URL. For these models, using the **DNS Redirection / Hijack** method described above is mandatory so that the station's built-in calls to `weatherstation.wunderground.com` are transparently routed to Home Assistant. (Station ID and password remain optional in the app settings).
