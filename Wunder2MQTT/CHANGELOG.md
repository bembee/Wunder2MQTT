# Changelog

## 1.1.0

* **New Feature:** Added optional Weather Underground forwarding. You can now proxy data to official WU servers while intercepting it locally via MQTT.
* **Security Improvement:** The local HTTP server now strictly listens only to the official `/weatherstation/updateweatherstation.php` endpoint. All other paths are safely ignored (returns 404).
* **Enhancement:** Added `rtupdate.wunderground.com` (Rapid-Fire endpoint) support to the documentation and forwarding engine to prevent rate-limiting.
* **Enhancement:** Added optional logging for outbound WU forwarding payloads (linked to the "Log Raw Data" setting), with automatic password obfuscation for security.
* **Bugfix:** Corrected `state_class` for `wind_direction` to `measurement_angle`.

## 1.0.0

* Initial release.