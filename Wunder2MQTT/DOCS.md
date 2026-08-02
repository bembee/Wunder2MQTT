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

---

## Weather Station Console Setup

On your weather station's configuration app (e.g., *WS View Plus*, *Ecowitt*, or local web interface), configure a **Custom / Weather Underground** server destination:

* **Server / Host / IP:** The local IP address of your Home Assistant instance (or point your router's / Dnsmasq DNS for `weatherstation.wunderground.com` to your Home Assistant IP).
* **Port:** `80` (or the custom host port you mapped in the app settings).
* **Path:** `/weatherstation/updateweatherstation.php` (or simply `/`).
* **Station ID / Password:** Match the credentials if configured in the app options.

> **Note:** Weather stations like the **Raddy WF-100C** (and similar hardcoded consoles) only ask for Wi-Fi credentials during setup and do not allow entering a custom server IP or URL. For these models, using the **DNS Redirection / Hijack** method described above is mandatory so that the station's built-in calls to `weatherstation.wunderground.com` are transparently routed to Home Assistant. (Station ID and password remain optional in the app settings).
