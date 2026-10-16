# Changelog

## 1.1.0

* Add optional HTTPS RapidFire forwarding using incoming Weather Underground station credentials, independent of MQTT sensor mappings.
* Keep uploads in a bounded background queue so upstream failures do not delay local processing.
* Support `dateutc=now` in MQTT timestamps and capture receipt time for uploads without explicit timestamps.
* Redact station credentials in logs and add English/Hungarian forwarding settings.
* Include trusted CA certificates and allow certificate access under AppArmor.

## 1.0.0

* Initial release.
