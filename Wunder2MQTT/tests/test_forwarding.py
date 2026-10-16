import importlib.util
import json
from pathlib import Path
import ssl
import threading
import unittest
from datetime import datetime, timezone
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlsplit
from unittest.mock import MagicMock, patch

import yaml


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("wunder_main", ROOT / "main.py")
main = importlib.util.module_from_spec(spec)
# Importing request handlers must not access Supervisor or connect to MQTT.
with patch("urllib.request.urlopen") as supervisor, patch("paho.mqtt.client.Client") as mqtt:
    spec.loader.exec_module(main)
    assert not supervisor.called
    assert not mqtt.called

RECEIVED = datetime(2026, 10, 16, 12, 34, 56, tzinfo=timezone.utc)
OBSERVATION = {"ID": "TESTSTATION", "PASSWORD": "secret&+?=key", "tempf": "72.5"}


class ForwardingTests(unittest.TestCase):
    def setUp(self):
        self.uploader = main.WundergroundForwarder()
        self.uploader.opener = MagicMock()
        response = self.uploader.opener.open.return_value.__enter__.return_value
        response.status = 200
        response.read.return_value = b"success\n"
        self.patches = [
            patch.object(main, "client", MagicMock()),
            patch.object(main, "forwarder", self.uploader),
            patch.object(main, "WUNDERGROUND_FORWARDING", True),
            patch.object(main, "STATION_ID", ""),
            patch.object(main, "STATION_PASSWORD", ""),
            patch.object(main, "LOG_RAW_DATA", False),
            patch.object(main, "SENSORS", [
                {"incoming_key": "tempf", "id": "temperature"},
                {"incoming_key": "dateutc", "id": "last_update"},
            ]),
        ]
        for item in self.patches:
            item.start()
            self.addCleanup(item.stop)
        self.http = main.app.test_client()

    def send(self, data=None, path="/weatherstation/updateweatherstation.php"):
        return self.http.get(path, query_string=OBSERVATION if data is None else data)

    def test_disabled_does_not_enqueue_or_upload(self):
        with patch.object(main, "WUNDERGROUND_FORWARDING", False):
            self.assertEqual(self.send().data, b"success")
        self.assertTrue(self.uploader.pending.empty())
        self.uploader.opener.open.assert_not_called()
        main.client.publish.assert_called_once()

    def test_original_fields_credentials_encoding_and_controls(self):
        data = dict(OBSERVATION, dateutc="2026-10-16 12:00:00", soilmoisture="41",
                    softwaretype="Console + model", action="other", realtime="0", rtfreq="5")
        self.assertEqual(self.send(data).status_code, 200)
        queued = self.uploader.pending.get_nowait()
        self.uploader.upload(queued)
        args, kwargs = self.uploader.opener.open.call_args
        parts = urlsplit(args[0])
        self.assertEqual(parts.scheme, "https")
        self.assertEqual(parts.netloc, "rtupdate.wunderground.com")
        expected = dict(data, action="updateraw", realtime="1")
        self.assertEqual(parse_qs(parts.query), {k: [v] for k, v in expected.items()})
        self.assertEqual(kwargs, {"timeout": 5})
        payload = json.loads(main.client.publish.call_args.args[1])
        self.assertEqual(payload, {"temperature": 72.5, "last_update": "2026-10-16T12:00:00Z"})

    def test_no_sensor_mappings_still_forwards(self):
        with patch.object(main, "SENSORS", []):
            self.assertEqual(self.send().data, b"success")
        self.assertEqual(self.uploader.pending.qsize(), 1)
        main.client.publish.assert_not_called()

    def test_receipt_time_and_default_frequency(self):
        for timestamp in (None, "now", ""):
            with self.subTest(timestamp=timestamp):
                data = OBSERVATION.copy()
                if timestamp is not None:
                    data["dateutc"] = timestamp
                self.uploader.enqueue(data, RECEIVED)
                queued = self.uploader.pending.get_nowait()
                self.assertEqual(queued["dateutc"], "2026-10-16 12:34:56")
                self.assertEqual(queued["rtfreq"], "2.5")
                self.assertEqual(data.get("dateutc"), timestamp)

    def test_mqtt_now_uses_same_receipt_time_as_upload(self):
        with patch.object(main, "datetime") as clock:
            clock.now.return_value = RECEIVED
            self.assertEqual(self.send(dict(OBSERVATION, dateutc="now")).data, b"success")
        payload = json.loads(main.client.publish.call_args.args[1])
        self.assertEqual(payload["last_update"], "2026-10-16T12:34:56Z")
        self.assertEqual(self.uploader.pending.get_nowait()["dateutc"], "2026-10-16 12:34:56")

    def test_rejected_requests_and_health_never_enqueue(self):
        for name, value in (("STATION_ID", "expected"), ("STATION_PASSWORD", "expected")):
            with patch.object(main, name, value):
                self.assertEqual(self.send().status_code, 401)
        self.assertEqual(self.send(path="/health").data, b"OK")
        self.assertTrue(self.uploader.pending.empty())
        main.client.publish.assert_not_called()

    def test_missing_credentials_skip_upload_but_publish(self):
        for key in ("ID", "PASSWORD"):
            for value in (None, "", "   "):
                data = OBSERVATION.copy()
                if value is None:
                    data.pop(key)
                else:
                    data[key] = value
                with self.assertLogs(main.logger, level="WARNING"):
                    self.assertEqual(self.send(data).data, b"success")
        self.assertTrue(self.uploader.pending.empty())
        self.assertEqual(main.client.publish.call_count, 6)

    def test_queue_overflow_drops_oldest_and_acknowledges(self):
        for index in range(100):
            self.uploader.enqueue(dict(OBSERVATION, tempf=str(index)), RECEIVED)
        with self.assertLogs(main.logger, level="WARNING") as logs:
            self.assertEqual(self.send(dict(OBSERVATION, tempf="100")).data, b"success")
        self.assertIn("oldest observation discarded", " ".join(logs.output))
        self.assertEqual(self.uploader.pending.qsize(), 100)
        values = [self.uploader.pending.get_nowait()["tempf"] for _ in range(100)]
        self.assertEqual(values, [str(i) for i in range(1, 101)])
        main.client.publish.assert_called_once()

    def test_upload_failures_are_sanitized_and_not_retried(self):
        url = self.uploader.URL + "?ID=TESTSTATION&PASSWORD=secret&+?=key"
        failures = [TimeoutError(url), ssl.SSLError(url), URLError(url),
                    HTTPError(url, 403, url, {}, None)]
        for failure in failures:
            with self.subTest(failure=type(failure).__name__):
                self.uploader.opener.open.reset_mock()
                self.uploader.opener.open.side_effect = failure
                with self.assertLogs(main.logger, level="WARNING") as logs:
                    self.uploader.upload(OBSERVATION)
                    self.assertEqual(self.send().data, b"success")
                self.assertEqual(self.uploader.opener.open.call_count, 1)
                self.assert_logs_safe(logs.output)

    def test_ingestion_requires_success_body_and_http_status(self):
        response = self.uploader.opener.open.return_value.__enter__.return_value
        for status, body in ((200, b"INVALIDPASSWORDID secret&+?=key"),
                             (503, b"success"), (200, b"success with extra text")):
            response.status, response.read.return_value = status, body
            with self.assertLogs(main.logger, level="WARNING") as logs:
                self.uploader.upload(OBSERVATION)
            self.assert_logs_safe(logs.output)
        response.status, response.read.return_value = 200, b"success\n"
        with patch.object(main.logger, "warning") as warning:
            self.uploader.upload(OBSERVATION)
        warning.assert_not_called()

    def test_raw_and_authentication_logs_redact_credentials(self):
        with patch.object(main, "LOG_RAW_DATA", True):
            with self.assertLogs(main.logger, level="INFO") as logs:
                self.send(dict(OBSERVATION, extra="23"))
            self.assert_logs_safe(logs.output)
            with patch.object(main, "STATION_ID", "expected"):
                with self.assertLogs(main.logger, level="INFO") as logs:
                    self.send()
                self.assert_logs_safe(logs.output)

    def assert_logs_safe(self, output):
        text = " ".join(output)
        for sensitive in (OBSERVATION["ID"], OBSERVATION["PASSWORD"], self.uploader.URL):
            self.assertNotIn(sensitive, text)

    def test_one_worker_continues_after_failed_upload(self):
        done = threading.Event()
        calls = []

        def open_upload(url, timeout):
            calls.append(url)
            if len(calls) == 1:
                raise TimeoutError("must not appear in logs")
            done.set()
            response = MagicMock()
            response.__enter__.return_value.status = 200
            response.__enter__.return_value.read.return_value = b"success"
            return response

        self.uploader.opener.open.side_effect = open_upload
        self.uploader.enqueue(OBSERVATION, RECEIVED)
        self.uploader.enqueue(OBSERVATION, RECEIVED)
        with self.assertLogs(main.logger, level="WARNING") as logs:
            self.uploader.start()
            try:
                self.assertTrue(done.wait(2))
            finally:
                self.uploader.stop()
        self.assertEqual(len(logs.output), 1)
        self.assertFalse(self.uploader.worker.is_alive())
        self.assertEqual(len(calls), 2)
        self.assertEqual(self.uploader.pending.unfinished_tasks, 0)

    def test_stalled_upload_does_not_delay_requests(self):
        entered, release, acknowledged = threading.Event(), threading.Event(), threading.Event()

        def blocked_upload(observation):
            entered.set()
            release.wait(3)

        def incoming_request():
            with main.app.test_client() as http:
                result = http.get("/", query_string=OBSERVATION)
                if result.status_code == 200 and result.data == b"success":
                    acknowledged.set()

        self.uploader.upload = blocked_upload
        self.uploader.enqueue(OBSERVATION, RECEIVED)
        self.uploader.start()
        incoming = threading.Thread(target=incoming_request, daemon=True)
        try:
            self.assertTrue(entered.wait(1))
            incoming.start()
            self.assertTrue(acknowledged.wait(1), "Incoming request blocked on the upload")
            main.client.publish.assert_called_once()
        finally:
            release.set()
            self.uploader.stop()
            if incoming.ident is not None:
                incoming.join(timeout=2)


class ConfigurationTests(unittest.TestCase):
    def test_service_lifecycle(self):
        for enabled in (False, True):
            with self.subTest(enabled=enabled), \
                    patch.object(main, "WUNDERGROUND_FORWARDING", enabled), \
                    patch.object(main, "forwarder", None), \
                    patch.object(main, "client", None), \
                    patch.object(main, "get_supervisor_mqtt", return_value={}), \
                    patch.object(main.mqtt, "Client") as mqtt_client, \
                    patch.object(main, "WundergroundForwarder") as worker, \
                    patch.object(main, "serve") as serve:
                main.main()
                mqtt_client.return_value.connect.assert_called_once_with("core-mosquitto", 1883, 60)
                mqtt_client.return_value.loop_start.assert_called_once()
                mqtt_client.return_value.disconnect.assert_called_once()
                mqtt_client.return_value.loop_stop.assert_called_once()
                serve.assert_called_once_with(main.app, host="0.0.0.0", port=80)
                if enabled:
                    worker.assert_called_once_with()
                    worker.return_value.start.assert_called_once()
                    worker.return_value.stop.assert_called_once()
                else:
                    worker.assert_not_called()

    def test_translations_schema_and_defaults_match(self):
        config = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
        self.assertEqual(config["version"], "1.1.0")
        self.assertIs(config["options"]["wunderground_forwarding"], False)
        self.assertEqual(config["schema"]["wunderground_forwarding"], "bool")
        for language in ("en", "hu"):
            translation = json.loads((ROOT / "translations" / (language + ".json")).read_text(encoding="utf-8"))
            self.assertEqual(set(translation["configuration"]), set(config["schema"]))

    def test_tls_verification_and_redirect_rejection(self):
        with patch.object(main.ssl, "create_default_context", wraps=ssl.create_default_context) as create:
            uploader = main.WundergroundForwarder()
        create.assert_called_once_with()
        https = next(h for h in uploader.opener.handlers if isinstance(h, main.urllib.request.HTTPSHandler))
        self.assertEqual(https._context.verify_mode, ssl.CERT_REQUIRED)
        self.assertTrue(https._context.check_hostname)
        # Exercise urllib's redirect handling without making a network request.
        self.assertTrue(any(isinstance(h, main.RejectRedirects) for h in uploader.opener.handlers))
        for status in (301, 302, 303, 307, 308):
            with self.subTest(status=status):
                req = main.urllib.request.Request(uploader.URL)
                with self.assertRaises(HTTPError):
                    uploader.opener.error("http", req, MagicMock(), status, "redirect",
                                          {"location": "https://example.com/"})


if __name__ == "__main__":
    unittest.main()
