# Wunder2MQTT

Integrate Wunderground protocol compatible weather station into Home Assistant via HTTP and MQTT.

## Features

* **Universal Compatibility:** Works with weather station supporting Wunderground HTTP updates.
* **Strict Endpoint Security:** Safely listens only to the official `/weatherstation/updateweatherstation.php` path.
* **Optional Cloud Forwarding:** Transparently forward your data to official Weather Underground Rapid-Fire servers (`rtupdate.wunderground.com`) while intercepting it locally.
* **Fully Configurable:** Customize sensor names, units, device classes, state classes, and conversion rules directly from the Home Assistant graphical interface.
* **Automatic UTC Timestamp Conversion:** Incoming `dateutc` parameters are automatically formatted into standard ISO 8601 UTC timestamps, allowing Home Assistant to correctly display the update time in any user's local timezone and format.
* **Raw Data Debugging:** Option to log unhandled/extra incoming sensor keys and outgoing forwarding payloads for advanced users.
* **Secure Authentication:** Optional Station ID and Password verification.

[![ko-fi](https://ko-fi.com/img/githubbutton_sm.svg)](https://ko-fi.com/J2T723F0R2)