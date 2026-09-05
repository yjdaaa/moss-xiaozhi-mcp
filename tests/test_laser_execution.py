import hashlib
import os
import shutil
import tempfile
import unittest
import uuid
from unittest.mock import patch

from core import laser_execution
from core import _laser_execution_backend as backend
from core.laser_runtime.models import file_sha256


class LaserExecutionTests(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.mkdtemp(prefix="laser-exec-")
        self.addCleanup(shutil.rmtree, self._tmpdir, ignore_errors=True)

    def _prepared(self, converted=False, name="job.gcode", content="G21\nG1 X1 Y1\n", include_hash=True):
        path = os.path.join(self._tmpdir, name)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(content)
        prepared = {
            "source_file": path,
            "gcode_file": path,
            "converted": converted,
        }
        if include_hash:
            prepared["expected_gcode_sha256"] = file_sha256(path)
        return prepared

    def test_send_file_requires_connection_mode(self):
        with self.assertRaises(TypeError):
            laser_execution.send_file(self._prepared())  # type: ignore[call-arg]

    def test_job_status_requires_connection_mode(self):
        with self.assertRaises(TypeError):
            laser_execution.job_status("abc")  # type: ignore[call-arg]

    def test_cancel_job_requires_connection_mode(self):
        with self.assertRaises(TypeError):
            laser_execution.cancel_job("abc")  # type: ignore[call-arg]

    def test_job_status_rejects_empty_connection_mode(self):
        result = laser_execution.job_status("missing", "")
        self.assertFalse(result["success"])
        self.assertIn("connection_mode", result["result"])

    def test_cancel_job_rejects_empty_connection_mode(self):
        result = laser_execution.cancel_job("missing", "")
        self.assertFalse(result["success"])
        self.assertIn("connection_mode", result["result"])

    def test_unconfirmed_send_file_does_not_touch_device_or_popen(self):
        prepared = self._prepared(converted=True)
        with (
            patch.object(backend, "execute_serial_send_file") as execute_mock,
            patch.object(backend, "start_serial_send_file_job") as start_mock,
            patch.object(backend.subprocess, "Popen") as popen_mock,
        ):
            result = laser_execution.send_file(
                prepared,
                "serial",
                confirmed=False,
                port="COM3",
            )
        self.assertTrue(result["success"], result)
        self.assertTrue(result["result"]["confirmation_required"])
        self.assertEqual(result["result"]["prepared"], prepared)
        execute_mock.assert_not_called()
        start_mock.assert_not_called()
        popen_mock.assert_not_called()

    def test_r2_send_file_string_false_variants_zero_backend_serial_and_network(self):
        """Direct send_file entry must reject bool("false")-style truthiness."""
        prepared = self._prepared()
        false_values = (False, None, "", "false", "FALSE", "0", "off", "no")
        for value in false_values:
            with self.subTest(confirmed=value, mode="serial"):
                with (
                    patch.object(backend, "execute_serial_send_file") as execute_mock,
                    patch.object(backend, "start_serial_send_file_job") as start_mock,
                    patch.object(backend.subprocess, "Popen") as popen_mock,
                ):
                    result = laser_execution.send_file(
                        prepared,
                        "serial",
                        confirmed=value,
                        port="COM3",
                    )
                self.assertTrue(result["success"], result)
                self.assertTrue(result["result"]["confirmation_required"])
                execute_mock.assert_not_called()
                start_mock.assert_not_called()
                popen_mock.assert_not_called()
            with self.subTest(confirmed=value, mode="network"):
                with (
                    patch.object(backend, "execute_network_send_file") as execute_mock,
                    patch.object(backend, "start_network_send_file_job") as start_mock,
                    patch.object(backend.subprocess, "Popen") as popen_mock,
                ):
                    result = laser_execution.send_file(
                        prepared,
                        "network",
                        confirmed=value,
                        host="laser.local",
                    )
                self.assertTrue(result["success"], result)
                self.assertTrue(result["result"]["confirmation_required"])
                execute_mock.assert_not_called()
                start_mock.assert_not_called()
                popen_mock.assert_not_called()

    def test_r2_send_file_explicit_true_still_routes_serial_backend(self):
        prepared = self._prepared()
        with patch.object(
            backend,
            "start_serial_send_file_job",
            return_value={"success": True, "job_id": "s1", "status": "pending"},
        ) as start_mock:
            result = laser_execution.send_file(
                prepared,
                "serial",
                confirmed=True,
                run_in_background=True,
                port="COM3",
            )
        self.assertTrue(result["success"], result)
        start_mock.assert_called_once()

    def test_r2_send_file_string_true_still_routes_network_backend(self):
        prepared = self._prepared()
        with patch.object(
            backend,
            "start_network_send_file_job",
            return_value={"success": True, "job_id": "n1", "status": "pending"},
        ) as start_mock:
            result = laser_execution.send_file(
                prepared,
                "network",
                confirmed="true",
                run_in_background=True,
                host="laser.local",
            )
        self.assertTrue(result["success"], result)
        start_mock.assert_called_once()

    def test_send_file_routes_serial_converted_to_zero_origin(self):
        prepared = self._prepared(converted=True)
        captured = {}

        def fake_execute(prepared_result, port, baudrate, wait_for_response):
            captured["prepared"] = prepared_result
            captured["send_zero_origin"] = bool(prepared_result.get("converted"))
            return {
                "success": True,
                "result": "ok",
                "detail": {
                    **prepared_result,
                    "send_zero_origin": captured["send_zero_origin"],
                    "port": port,
                },
            }

        with patch.object(backend, "execute_serial_send_file", side_effect=fake_execute):
            result = laser_execution.send_file(
                prepared,
                "serial",
                confirmed=True,
                run_in_background=False,
                port="COM9",
                baudrate=115200,
            )
        self.assertTrue(result["success"], result)
        self.assertTrue(captured["send_zero_origin"])
        self.assertTrue(result["detail"]["send_zero_origin"])

    def test_send_file_routes_serial_raw_without_zero_origin(self):
        prepared = self._prepared(converted=False)
        captured = {}

        def fake_execute(prepared_result, port, baudrate, wait_for_response):
            captured["send_zero_origin"] = bool(prepared_result.get("converted"))
            return {
                "success": True,
                "result": "ok",
                "detail": {
                    **prepared_result,
                    "send_zero_origin": captured["send_zero_origin"],
                },
            }

        with patch.object(backend, "execute_serial_send_file", side_effect=fake_execute):
            result = laser_execution.send_file(
                prepared,
                "serial",
                confirmed=True,
                run_in_background=False,
            )
        self.assertTrue(result["success"], result)
        self.assertFalse(captured["send_zero_origin"])

    def test_send_file_routes_network_and_forces_telnet_background(self):
        prepared = self._prepared(converted=False)

        def fake_start(prepared_result, host, transport, http_port, telnet_port, timeout, wait_for_response):
            self.assertEqual(transport, "telnet")
            return {
                "success": True,
                "job_id": "net1",
                "status": "pending",
                "result": "started",
                "detail": prepared_result,
            }

        with patch.object(backend, "start_network_send_file_job", side_effect=fake_start):
            result = laser_execution.send_file(
                prepared,
                "network",
                confirmed=True,
                run_in_background=True,
                host="laser.local",
                transport="http",
            )
        self.assertTrue(result["success"], result)
        self.assertEqual(result["job_id"], "net1")

    def test_background_serial_popen_targets_core_backend_worker(self):
        prepared = self._prepared(converted=False)
        job_dir = self._tmpdir

        class FakeProcess:
            pid = 4242

        class FakeSerial:
            def close(self):
                return None

        with (
            patch.object(backend, "SERIAL_JOBS_DIR", job_dir),
            patch.object(
                backend,
                "preflight_serial_send_file",
                return_value=(None, FakeSerial(), "COM3", {"probe_command": "?"}),
            ),
            patch.object(backend.subprocess, "Popen", return_value=FakeProcess()) as popen_mock,
        ):
            result = laser_execution.send_file(
                prepared,
                "serial",
                confirmed=True,
                run_in_background=True,
                port="COM3",
            )

        self.assertTrue(result["success"], result)
        argv = popen_mock.call_args.args[0]
        self.assertEqual(argv[1:5], ["-m", "core._laser_execution_backend", "--send-file-worker", "serial"])
        self.assertTrue(argv[5].endswith(".json"))
        joined = " ".join(argv)
        self.assertNotIn("tools.laser_grbl_tool", joined)
        self.assertNotIn("tools.laser_network_grbl_tool", joined)

    def test_background_network_popen_targets_core_backend_worker(self):
        prepared = self._prepared(converted=False)
        job_dir = self._tmpdir

        class FakeProcess:
            pid = 5252

        with (
            patch.object(backend, "NETWORK_JOBS_DIR", job_dir),
            patch.object(
                backend,
                "preflight_network_send_file",
                return_value=(None, {"probe_command": "?"}),
            ),
            patch.object(backend.subprocess, "Popen", return_value=FakeProcess()) as popen_mock,
        ):
            result = laser_execution.send_file(
                prepared,
                "network",
                confirmed=True,
                run_in_background=True,
                host="laser.local",
            )

        self.assertTrue(result["success"], result)
        argv = popen_mock.call_args.args[0]
        self.assertEqual(argv[1:5], ["-m", "core._laser_execution_backend", "--send-file-worker", "network"])
        joined = " ".join(argv)
        self.assertNotIn("tools.laser_grbl_tool", joined)
        self.assertNotIn("tools.laser_network_grbl_tool", joined)

    def test_worker_cli_requires_mode_and_job_file(self):
        self.assertEqual(backend.main(["--send-file-worker"]), 2)
        self.assertEqual(backend.main(["--send-file-worker", "serial"]), 2)

    def test_worker_cli_serial_only_reads_given_job_file(self):
        job_id = uuid.uuid4().hex
        job_file = os.path.join(self._tmpdir, f"{job_id}.json")
        prepared = self._prepared(converted=False)
        backend.write_job_file(
            job_file,
            {
                "job_id": job_id,
                "status": "pending",
                "prepared": prepared,
                "port": "COM3",
                "baudrate": 115200,
                "wait_for_response": True,
                "expected_gcode_sha256": prepared["expected_gcode_sha256"],
                "result": None,
            },
        )

        with patch.object(
            backend,
            "execute_serial_send_file",
            return_value={"success": True, "result": "sent"},
        ) as execute_mock, patch.object(
            backend,
            "execute_network_send_file",
        ) as network_mock:
            code = backend.main(["--send-file-worker", "serial", job_file])

        self.assertEqual(code, 0)
        execute_mock.assert_called_once()
        kwargs = execute_mock.call_args.kwargs
        self.assertFalse(kwargs.get("skip_online_probe", False))
        network_mock.assert_not_called()
        status = backend.read_job_file(job_file)
        self.assertEqual(status["status"], "completed")

    def test_background_serial_start_does_not_probe_worker_probes_once_after_hash(self):
        """Start must not touch device; worker verifies hash then probes once before stream."""
        prepared = self._prepared(converted=False)
        job_dir = self._tmpdir
        probe_calls = {"preflight": 0}
        order = []

        class FakeSerial:
            def close(self):
                return None

        class FakeProcess:
            pid = 9001

        def fake_preflight(port, baudrate):
            probe_calls["preflight"] += 1
            order.append("probe")
            return None, FakeSerial(), "COM3", {"probe_command": "?", "port": "COM3"}

        real_open_verified = backend.open_verified_gcode_text

        def tracking_open(gcode_file, expected_gcode_sha256):
            order.append("open_verify")
            return real_open_verified(gcode_file, expected_gcode_sha256)

        def fake_send(ser, gcode_file=None, wait_for_response=False, send_zero_origin=False, gcode_handle=None):
            order.append("stream")
            # Production send_gcode_file closes verified handles; mocks must match.
            if gcode_handle is not None and not getattr(gcode_handle, "closed", False):
                gcode_handle.close()
            return 2, 0, None

        with (
            patch.object(backend, "SERIAL_JOBS_DIR", job_dir),
            patch.object(backend, "preflight_serial_send_file", side_effect=fake_preflight),
            patch.object(backend, "open_verified_gcode_text", side_effect=tracking_open),
            patch.object(backend, "send_gcode_file", side_effect=fake_send),
            patch.object(backend.subprocess, "Popen", return_value=FakeProcess()) as popen_mock,
        ):
            start = laser_execution.send_file(
                prepared,
                "serial",
                confirmed=True,
                run_in_background=True,
                port="COM3",
            )
            self.assertTrue(start["success"], start)
            self.assertEqual(probe_calls["preflight"], 0)
            popen_mock.assert_called_once()

            job_file = os.path.join(job_dir, f"{start['job_id']}.json")
            job = backend.read_job_file(job_file)
            self.assertEqual(job.get("expected_gcode_sha256"), prepared["expected_gcode_sha256"])
            self.assertFalse(job.get("preflight_ok", False))

            code = backend.run_serial_send_file_worker(job_file)
            self.assertEqual(code, 0)

        self.assertEqual(probe_calls["preflight"], 1)
        self.assertEqual(order, ["open_verify", "probe", "stream"])
        finished = backend.read_job_file(job_file)
        self.assertEqual(finished["status"], "completed")
        self.assertFalse(finished["result"]["detail"].get("online_probe_skipped"))

    def test_background_network_start_does_not_probe_worker_probes_once_after_hash(self):
        prepared = self._prepared(converted=False)
        job_dir = self._tmpdir
        probe_calls = {"preflight": 0}
        order = []

        class FakeProcess:
            pid = 9002

        def fake_preflight(host, transport, http_port, telnet_port, timeout):
            probe_calls["preflight"] += 1
            order.append("probe")
            return None, {
                "probe_command": "?",
                "host": host,
                "transport": transport,
            }

        real_open_verified = backend.open_verified_gcode_text

        def tracking_open(gcode_file, expected_gcode_sha256):
            order.append("open_verify")
            return real_open_verified(gcode_file, expected_gcode_sha256)

        def fake_stream(host, gcode_file=None, telnet_port=None, timeout=None, wait_for_response=True, send_zero_origin=False, socket_factory=None, gcode_handle=None):
            order.append("stream")
            if gcode_handle is not None and not getattr(gcode_handle, "closed", False):
                gcode_handle.close()
            return 2, 0, None

        with (
            patch.object(backend, "NETWORK_JOBS_DIR", job_dir),
            patch.object(backend, "preflight_network_send_file", side_effect=fake_preflight),
            patch.object(backend, "open_verified_gcode_text", side_effect=tracking_open),
            patch.object(backend, "send_telnet_gcode_file", side_effect=fake_stream) as stream_mock,
            patch.object(backend.subprocess, "Popen", return_value=FakeProcess()),
        ):
            start = laser_execution.send_file(
                prepared,
                "network",
                confirmed=True,
                run_in_background=True,
                host="laser.local",
            )
            self.assertTrue(start["success"], start)
            self.assertEqual(probe_calls["preflight"], 0)

            job_file = os.path.join(job_dir, f"{start['job_id']}.json")
            job = backend.read_job_file(job_file)
            self.assertEqual(job.get("expected_gcode_sha256"), prepared["expected_gcode_sha256"])

            code = backend.run_network_send_file_worker(job_file)
            self.assertEqual(code, 0)

        self.assertEqual(probe_calls["preflight"], 1)
        stream_mock.assert_called_once()
        self.assertEqual(order, ["open_verify", "probe", "stream"])
        finished = backend.read_job_file(job_file)
        self.assertEqual(finished["status"], "completed")
        self.assertFalse(finished["result"]["detail"].get("online_probe_skipped"))

    def test_sync_serial_send_opens_verifies_then_probes_once_before_stream(self):
        prepared = self._prepared(converted=False)
        order = []
        probe_calls = {"preflight": 0}

        class FakeSerial:
            def close(self):
                return None

        def fake_preflight(port, baudrate):
            probe_calls["preflight"] += 1
            order.append("probe")
            return None, FakeSerial(), "COM3", {"probe_command": "?"}

        real_open_verified = backend.open_verified_gcode_text

        def tracking_open(gcode_file, expected_gcode_sha256):
            order.append("open_verify")
            return real_open_verified(gcode_file, expected_gcode_sha256)

        def fake_send(ser, gcode_file=None, wait_for_response=False, send_zero_origin=False, gcode_handle=None):
            order.append("stream")
            if gcode_handle is not None and not getattr(gcode_handle, "closed", False):
                gcode_handle.close()
            return 2, 0, None

        with (
            patch.object(backend, "preflight_serial_send_file", side_effect=fake_preflight),
            patch.object(backend, "open_verified_gcode_text", side_effect=tracking_open),
            patch.object(backend, "send_gcode_file", side_effect=fake_send),
        ):
            result = laser_execution.send_file(
                prepared,
                "serial",
                confirmed=True,
                run_in_background=False,
                port="COM3",
            )
        self.assertTrue(result["success"], result)
        self.assertEqual(probe_calls["preflight"], 1)
        self.assertEqual(order, ["open_verify", "probe", "stream"])
        self.assertFalse(result["detail"].get("online_probe_skipped"))

    def test_sync_network_send_opens_verifies_then_probes_once_before_stream(self):
        """Sync network: open+hash, one probe, stream from verified handle."""
        prepared = self._prepared(converted=False)
        order = []
        probe_calls = {"preflight": 0}

        def fake_preflight(host, transport, http_port, telnet_port, timeout):
            probe_calls["preflight"] += 1
            order.append("probe")
            self.assertEqual(transport, "telnet")
            return None, {
                "probe_command": "?",
                "host": host,
                "transport": transport,
            }

        real_open_verified = backend.open_verified_gcode_text

        def tracking_open(gcode_file, expected_gcode_sha256):
            order.append("open_verify")
            return real_open_verified(gcode_file, expected_gcode_sha256)

        def fake_stream(host, gcode_file=None, telnet_port=None, timeout=None, wait_for_response=True, send_zero_origin=False, socket_factory=None, gcode_handle=None):
            order.append("stream")
            if gcode_handle is not None and not getattr(gcode_handle, "closed", False):
                gcode_handle.close()
            return 2, 0, None

        with (
            patch.object(backend, "preflight_network_send_file", side_effect=fake_preflight),
            patch.object(backend, "open_verified_gcode_text", side_effect=tracking_open),
            patch.object(backend, "send_telnet_gcode_file", side_effect=fake_stream) as stream_mock,
        ):
            result = laser_execution.send_file(
                prepared,
                "network",
                confirmed=True,
                run_in_background=False,
                host="laser.local",
                transport="http",  # forced to telnet for full file
            )
        self.assertTrue(result["success"], result)
        self.assertEqual(probe_calls["preflight"], 1)
        stream_mock.assert_called_once()
        self.assertEqual(order, ["open_verify", "probe", "stream"])
        self.assertFalse(result["detail"].get("online_probe_skipped"))
        self.assertEqual(result["detail"].get("transport"), "telnet")

    def test_confirmed_send_without_expected_hash_fails_closed_before_device(self):
        prepared = self._prepared(include_hash=False)
        with (
            patch.object(backend, "preflight_serial_send_file") as preflight,
            patch.object(backend, "start_serial_send_file_job") as start_job,
            patch.object(backend, "execute_serial_send_file") as execute,
            patch.object(backend.subprocess, "Popen") as popen,
        ):
            result = laser_execution.send_file(
                prepared,
                "serial",
                confirmed=True,
                run_in_background=False,
                port="COM3",
            )
        self.assertFalse(result["success"])
        self.assertIn("expected_gcode_sha256", result["result"])
        preflight.assert_not_called()
        start_job.assert_not_called()
        execute.assert_not_called()
        popen.assert_not_called()

    def test_sync_serial_hash_mismatch_zero_device_access(self):
        prepared = self._prepared()
        prepared["expected_gcode_sha256"] = "0" * 64
        with (
            patch.object(backend, "preflight_serial_send_file") as preflight,
            patch.object(backend, "send_gcode_file") as stream,
            patch.object(backend, "connect_grbl_serial") as connect,
        ):
            result = laser_execution.send_file(
                prepared,
                "serial",
                confirmed=True,
                run_in_background=False,
                port="COM3",
            )
        self.assertFalse(result["success"])
        self.assertIn("preview_content_mismatch", result.get("error_code", "") + result["result"])
        preflight.assert_not_called()
        stream.assert_not_called()
        connect.assert_not_called()

    def test_sync_network_hash_mismatch_zero_device_access(self):
        prepared = self._prepared()
        prepared["expected_gcode_sha256"] = "0" * 64
        with (
            patch.object(backend, "preflight_network_send_file") as preflight,
            patch.object(backend, "send_telnet_gcode_file") as stream,
            patch.object(backend, "query_telnet_command") as query,
        ):
            result = laser_execution.send_file(
                prepared,
                "network",
                confirmed=True,
                run_in_background=False,
                host="laser.local",
            )
        self.assertFalse(result["success"])
        self.assertIn("preview_content_mismatch", result.get("error_code", "") + result["result"])
        preflight.assert_not_called()
        stream.assert_not_called()
        query.assert_not_called()

    def test_background_serial_worker_hash_fail_before_probe_or_connect(self):
        prepared = self._prepared()
        job_dir = self._tmpdir

        class FakeProcess:
            pid = 4242

        with (
            patch.object(backend, "SERIAL_JOBS_DIR", job_dir),
            patch.object(backend.subprocess, "Popen", return_value=FakeProcess()),
            patch.object(backend, "preflight_serial_send_file") as preflight,
            patch.object(backend, "connect_serial_for_stream") as connect_only,
            patch.object(backend, "send_gcode_file") as stream,
        ):
            start = laser_execution.send_file(
                prepared,
                "serial",
                confirmed=True,
                run_in_background=True,
                port="COM3",
            )
            self.assertTrue(start["success"], start)
            preflight.assert_not_called()

            # Replace file after start, before worker open
            with open(prepared["gcode_file"], "w", encoding="utf-8") as handle:
                handle.write("G0 X99\nG1 X100\n")

            job_file = os.path.join(job_dir, f"{start['job_id']}.json")
            code = backend.run_serial_send_file_worker(job_file)
            self.assertEqual(code, 1)
            preflight.assert_not_called()
            connect_only.assert_not_called()
            stream.assert_not_called()

        finished = backend.read_job_file(job_file)
        self.assertEqual(finished["status"], "failed")
        self.assertIn("preview_content_mismatch", finished["result"].get("error_code", "") + finished["result"]["result"])

    def test_background_network_worker_hash_fail_before_probe_or_stream(self):
        prepared = self._prepared()
        job_dir = self._tmpdir

        class FakeProcess:
            pid = 5252

        with (
            patch.object(backend, "NETWORK_JOBS_DIR", job_dir),
            patch.object(backend.subprocess, "Popen", return_value=FakeProcess()),
            patch.object(backend, "preflight_network_send_file") as preflight,
            patch.object(backend, "send_telnet_gcode_file") as stream,
            patch.object(backend, "query_telnet_command") as query,
        ):
            start = laser_execution.send_file(
                prepared,
                "network",
                confirmed=True,
                run_in_background=True,
                host="laser.local",
            )
            self.assertTrue(start["success"], start)
            preflight.assert_not_called()

            with open(prepared["gcode_file"], "w", encoding="utf-8") as handle:
                handle.write("G0 X99\n")

            job_file = os.path.join(job_dir, f"{start['job_id']}.json")
            code = backend.run_network_send_file_worker(job_file)
            self.assertEqual(code, 1)
            preflight.assert_not_called()
            stream.assert_not_called()
            query.assert_not_called()

        finished = backend.read_job_file(job_file)
        self.assertEqual(finished["status"], "failed")
        self.assertIn("preview_content_mismatch", finished["result"].get("error_code", "") + finished["result"]["result"])

    def test_send_gcode_file_prefers_verified_handle_and_does_not_reopen_path(self):
        path = os.path.join(self._tmpdir, "handle.gcode")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write("G21\nG1 X1\n")
        expected = file_sha256(path)
        verified = backend.open_verified_gcode_text(path, expected)
        self.assertTrue(verified["success"], verified)
        gcode_handle = verified["handle"]

        class FakeSerial:
            def __init__(self):
                self.writes = []

            def write(self, data):
                self.writes.append(data)

            def readline(self):
                return b"ok\n"

        real_open = open
        open_calls = {"count": 0}

        def counting_open(*args, **kwargs):
            open_calls["count"] += 1
            return real_open(*args, **kwargs)

        ser = FakeSerial()
        with patch("builtins.open", side_effect=counting_open):
            total, errors, send_error = backend.send_gcode_file(
                ser,
                path,
                wait_for_response=True,
                gcode_handle=gcode_handle,
            )
        self.assertIsNone(send_error)
        self.assertEqual(errors, 0)
        self.assertGreaterEqual(total, 1)
        self.assertEqual(open_calls["count"], 0)
        self.assertTrue(gcode_handle.closed)

    def test_file_sha256_helper_matches_hashlib(self):
        path = os.path.join(self._tmpdir, "hash-me.gcode")
        payload = b"G21\nG1 X1 Y1\n"
        with open(path, "wb") as handle:
            handle.write(payload)
        self.assertEqual(file_sha256(path), hashlib.sha256(payload).hexdigest())

    def test_open_verified_opens_target_path_only_once(self):
        path = os.path.join(self._tmpdir, "once.gcode")
        raw = b"G21\r\nG1 X1\r\n"
        with open(path, "wb") as handle:
            handle.write(raw)
        expected = hashlib.sha256(raw).hexdigest()
        real_open = open
        open_calls = []

        def counting_open(target, *args, **kwargs):
            if os.path.abspath(str(target)) == os.path.abspath(path):
                open_calls.append((args, kwargs))
            return real_open(target, *args, **kwargs)

        with patch("builtins.open", side_effect=counting_open):
            verified = backend.open_verified_gcode_text(path, expected)
        self.assertTrue(verified["success"], verified)
        self.assertEqual(len(open_calls), 1)
        args, kwargs = open_calls[0]
        mode = args[0] if args else kwargs.get("mode")
        self.assertEqual(mode, "rb")
        verified["handle"].close()

    def test_raw_byte_hash_includes_crlf_and_non_utf8_bytes(self):
        path = os.path.join(self._tmpdir, "binary.gcode")
        raw = b"G21\r\n;comment \xff\xfe\nG1 X1\n"
        with open(path, "wb") as handle:
            handle.write(raw)
        expected = hashlib.sha256(raw).hexdigest()
        self.assertEqual(file_sha256(path), expected)
        verified = backend.open_verified_gcode_text(path, expected)
        self.assertTrue(verified["success"], verified)
        self.assertEqual(verified["actual_gcode_sha256"], expected)
        verified["handle"].close()

    def test_verified_handle_stream_does_not_reopen_path(self):
        path = os.path.join(self._tmpdir, "stream-once.gcode")
        raw = b"G21\nG1 X1\n"
        with open(path, "wb") as handle:
            handle.write(raw)
        expected = hashlib.sha256(raw).hexdigest()
        verified = backend.open_verified_gcode_text(path, expected)
        handle = verified["handle"]

        class FakeSerial:
            def write(self, data):
                return None

            def readline(self):
                return b"ok\n"

        real_open = open
        open_calls = {"count": 0}

        def counting_open(target, *args, **kwargs):
            if os.path.abspath(str(target)) == os.path.abspath(path):
                open_calls["count"] += 1
            return real_open(target, *args, **kwargs)

        with patch("builtins.open", side_effect=counting_open):
            total, errors, send_error = backend.send_gcode_file(
                FakeSerial(),
                path,
                wait_for_response=True,
                gcode_handle=handle,
            )
        self.assertIsNone(send_error)
        self.assertEqual(errors, 0)
        self.assertGreaterEqual(total, 1)
        self.assertEqual(open_calls["count"], 0)
        self.assertTrue(handle.closed)

    def test_send_zero_origin_failure_closes_verified_handle(self):
        path = os.path.join(self._tmpdir, "zero-fail.gcode")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write("G21\nG1 X1\n")
        expected = file_sha256(path)
        verified = backend.open_verified_gcode_text(path, expected)
        gcode_handle = verified["handle"]

        class FailZeroSerial:
            def write(self, data):
                return None

            def readline(self):
                return b"error:zero\n"

        total, errors, send_error = backend.send_gcode_file(
            FailZeroSerial(),
            path,
            wait_for_response=True,
            send_zero_origin=True,
            gcode_handle=gcode_handle,
        )
        self.assertEqual(errors, 1)
        self.assertIn("设置零点失败", send_error)
        self.assertTrue(gcode_handle.closed)

    def test_background_network_empty_host_no_job_no_popen_no_probe(self):
        prepared = self._prepared()
        job_dir = self._tmpdir
        with (
            patch.object(backend, "NETWORK_JOBS_DIR", job_dir),
            patch.object(backend, "DEFAULT_NETWORK_HOST", ""),
            patch.object(backend, "preflight_network_send_file") as preflight,
            patch.object(backend.subprocess, "Popen") as popen_mock,
        ):
            result = laser_execution.send_file(
                prepared,
                "network",
                confirmed=True,
                run_in_background=True,
                host="",
            )
        self.assertFalse(result["success"], result)
        self.assertIn("host", result["result"].lower())
        preflight.assert_not_called()
        popen_mock.assert_not_called()
        self.assertEqual([name for name in os.listdir(job_dir) if name.endswith(".json")], [])

    def test_open_verified_read_failure_result_has_no_absolute_path(self):
        path = os.path.join(self._tmpdir, "open-fail.gcode")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write("G21\n")
        expected = file_sha256(path)
        leak = f"Permission denied: '{path}'"
        real_open = open

        def failing_open(target, *args, **kwargs):
            if os.path.abspath(str(target)) == os.path.abspath(path):
                raise OSError(leak)
            return real_open(target, *args, **kwargs)

        self.assertTrue(os.path.isfile(path))
        with patch("builtins.open", side_effect=failing_open):
            verified = backend.open_verified_gcode_text(path, expected)
        self.assertFalse(verified["success"])
        self.assertNotIn(path, verified["result"])
        self.assertNotIn(self._tmpdir, verified["result"])
        self.assertNotIn(leak, verified["result"])
        if verified.get("detail") is not None:
            detail_text = str(verified["detail"])
            self.assertNotIn(path, detail_text)
            self.assertNotIn(leak, detail_text)
        self.assertEqual(verified["error_code"], "preview_content_mismatch")

    def test_job_status_does_not_scan_other_transport_directory(self):
        serial_dir = os.path.join(self._tmpdir, "serial")
        network_dir = os.path.join(self._tmpdir, "network")
        os.makedirs(serial_dir)
        os.makedirs(network_dir)
        job_id = uuid.uuid4().hex
        # Only network has the job
        backend.write_job_file(
            os.path.join(network_dir, f"{job_id}.json"),
            {"job_id": job_id, "status": "pending"},
        )
        with (
            patch.object(backend, "SERIAL_JOBS_DIR", serial_dir),
            patch.object(backend, "NETWORK_JOBS_DIR", network_dir),
        ):
            missing = laser_execution.job_status(job_id, "serial")
            found = laser_execution.job_status(job_id, "network")
        self.assertFalse(missing["success"])
        self.assertTrue(found["success"])

    def test_core_modules_do_not_import_tools(self):
        root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
        for rel in ("core/laser_execution.py", "core/_laser_execution_backend.py"):
            path = os.path.join(root, rel)
            with open(path, "r", encoding="utf-8") as handle:
                source = handle.read()
            # Only real import statements; docstrings may mention "import tools".
            self.assertNotRegex(source, r"(?m)^\s*from tools(?:\.|\s|$)")
            self.assertNotRegex(source, r"(?m)^\s*import tools(?:\.|\s|$)")

    def test_only_two_execution_core_files(self):
        root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "core"))
        self.assertTrue(os.path.isfile(os.path.join(root, "laser_execution.py")))
        self.assertTrue(os.path.isfile(os.path.join(root, "_laser_execution_backend.py")))
        unexpected = [
            name
            for name in os.listdir(root)
            if name.startswith("laser_execution") and name not in {
                "laser_execution.py",
                "_laser_execution_backend.py",
            }
        ]
        self.assertEqual(unexpected, [])

    def test_backend_send_gcode_file_can_set_zero_origin_before_file_lines(self):
        sent_commands = []

        def record_send(_ser, line, timeout=5):
            sent_commands.append(line)
            return True, None

        gcode_file = os.path.join(self._tmpdir, "job.gcode")
        with open(gcode_file, "w", encoding="utf-8") as handle:
            handle.write("; comment\n")
            handle.write("G21\n")
            handle.write("\n")
            handle.write("G1 X1 Y1 S100\n")

        with patch.object(backend, "send_gcode_line", side_effect=record_send):
            total, errors, send_error = backend.send_gcode_file(
                object(), gcode_file, wait_for_response=True, send_zero_origin=True
            )

        self.assertIsNone(send_error)
        self.assertEqual(errors, 0)
        self.assertEqual(total, 3)
        self.assertEqual(sent_commands, ["G92 X0 Y0 Z0", "G21", "G1 X1 Y1 S100"])

    def test_backend_send_gcode_file_default_mode_writes_without_waiting(self):
        class FakeSerial:
            def __init__(self):
                self.writes = []

            def write(self, data):
                self.writes.append(data)

            def readline(self):
                raise AssertionError("default send_file mode must not wait for GRBL response")

        gcode_file = os.path.join(self._tmpdir, "job.nc")
        with open(gcode_file, "w", encoding="utf-8") as handle:
            handle.write("; comment\n")
            handle.write("G21\n")
            handle.write("\n")
            handle.write("G1 X1 Y1 S100\n")

        serial = FakeSerial()
        total, errors, send_error = backend.send_gcode_file(serial, gcode_file)

        self.assertIsNone(send_error)
        self.assertEqual(errors, 0)
        self.assertEqual(total, 2)
        self.assertEqual(serial.writes, [b"G21\n", b"G1 X1 Y1 S100\n"])

    def test_cancel_serial_job_terminates_worker_and_marks_cancelled(self):
        job_dir = self._tmpdir
        job_id = uuid.uuid4().hex
        job_file = os.path.join(job_dir, f"{job_id}.json")
        backend.write_job_file(
            job_file,
            {
                "job_id": job_id,
                "status": "running",
                "process_id": 12345,
                "result": None,
            },
        )

        with (
            patch.object(backend, "SERIAL_JOBS_DIR", job_dir),
            patch.object(backend.os, "kill") as kill_mock,
        ):
            result = laser_execution.cancel_job(job_id, "serial")

        self.assertTrue(result["success"], result)
        kill_mock.assert_called_once_with(12345, backend.signal.SIGTERM)
        status = backend.read_job_file(job_file)
        self.assertEqual(status["status"], "cancelled")
        self.assertEqual(status["result"]["job_id"], job_id)

    def test_cancel_network_job_refuses_terminal_jobs(self):
        job_dir = self._tmpdir

        for stored_status in ("completed", "failed", "cancelled", "canceled"):
            with self.subTest(stored_status=stored_status):
                job_id = uuid.uuid4().hex
                job_file = os.path.join(job_dir, f"{job_id}.json")
                backend.write_job_file(
                    job_file,
                    {
                        "job_id": job_id,
                        "status": stored_status,
                        "process_id": 12345,
                        "result": {"success": True},
                    },
                )
                with (
                    patch.object(backend, "NETWORK_JOBS_DIR", job_dir),
                    patch.object(backend.os, "kill") as kill_mock,
                ):
                    result = laser_execution.cancel_job(job_id, "network")

                self.assertFalse(result["success"], result)
                self.assertIn("任务已结束", result["result"])
                kill_mock.assert_not_called()

    def test_serial_and_network_tools_do_not_import_private_backend(self):
        root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
        for rel in (
            "tools/laser_grbl_tool.py",
            "tools/laser_network_grbl_tool.py",
        ):
            path = os.path.join(root, rel)
            with open(path, "r", encoding="utf-8") as handle:
                source = handle.read()
            self.assertNotRegex(source, r"(?m)^\s*from core import _laser_execution_backend")
            self.assertNotRegex(source, r"(?m)^\s*import core\._laser_execution_backend")
            self.assertNotIn("--send-file-worker", source)

    def test_production_popen_helper_only_targets_core_backend(self):
        source_path = os.path.abspath(backend.__file__)
        with open(source_path, "r", encoding="utf-8") as handle:
            source = handle.read()
        self.assertIn('"-m"', source)
        self.assertIn('"core._laser_execution_backend"', source)
        self.assertIn('"--send-file-worker"', source)
        self.assertNotIn("tools.laser_grbl_tool", source)
        self.assertNotIn("tools.laser_network_grbl_tool", source)

    def test_backend_has_no_http_full_file_sender(self):
        source_path = os.path.abspath(backend.__file__)
        with open(source_path, "r", encoding="utf-8") as handle:
            source = handle.read()
        self.assertNotIn("def send_http_gcode_file", source)
        self.assertNotIn("def query_web_command", source)
        self.assertNotIn("def build_http_command_url", source)
        self.assertIn("def send_telnet_gcode_file", source)
        # execute path must only stream via Telnet helper
        self.assertIn("send_telnet_gcode_file(", source)

    def test_serial_background_start_skips_probe_worker_preflight_fail_does_not_stream(self):
        """Start no longer probes; worker fails probe after hash with zero stream."""
        prepared = self._prepared(converted=False)
        job_dir = self._tmpdir

        class FakeProcess:
            pid = 7001

        with (
            patch.object(backend, "SERIAL_JOBS_DIR", job_dir),
            patch.object(
                backend,
                "preflight_serial_send_file",
                return_value=(
                    {"success": False, "result": "设备在线检查失败"},
                    None,
                    None,
                    None,
                ),
            ) as preflight,
            patch.object(backend, "send_gcode_file") as stream,
            patch.object(backend.subprocess, "Popen", return_value=FakeProcess()) as popen_mock,
        ):
            result = laser_execution.send_file(
                prepared,
                "serial",
                confirmed=True,
                run_in_background=True,
                port="COM3",
            )
            self.assertTrue(result["success"], result)
            preflight.assert_not_called()
            popen_mock.assert_called_once()
            job_file = os.path.join(job_dir, f"{result['job_id']}.json")
            code = backend.run_serial_send_file_worker(job_file)
            self.assertEqual(code, 1)
            preflight.assert_called_once()
            stream.assert_not_called()

        finished = backend.read_job_file(job_file)
        self.assertEqual(finished["status"], "failed")
        self.assertIn("设备在线检查失败", finished["result"]["result"])

    def test_network_background_start_skips_probe_worker_preflight_fail_does_not_stream(self):
        prepared = self._prepared(converted=False)
        job_dir = self._tmpdir

        class FakeProcess:
            pid = 7002

        with (
            patch.object(backend, "NETWORK_JOBS_DIR", job_dir),
            patch.object(
                backend,
                "preflight_network_send_file",
                return_value=(
                    {"success": False, "result": "设备在线检查失败"},
                    None,
                ),
            ) as preflight,
            patch.object(backend, "send_telnet_gcode_file") as stream,
            patch.object(backend.subprocess, "Popen", return_value=FakeProcess()) as popen_mock,
        ):
            result = laser_execution.send_file(
                prepared,
                "network",
                confirmed=True,
                run_in_background=True,
                host="laser.local",
            )
            self.assertTrue(result["success"], result)
            preflight.assert_not_called()
            popen_mock.assert_called_once()
            job_file = os.path.join(job_dir, f"{result['job_id']}.json")
            code = backend.run_network_send_file_worker(job_file)
            self.assertEqual(code, 1)
            preflight.assert_called_once()
            stream.assert_not_called()

        finished = backend.read_job_file(job_file)
        self.assertEqual(finished["status"], "failed")
        self.assertIn("设备在线检查失败", finished["result"]["result"])

    def test_serial_sync_preflight_failure_does_not_stream(self):
        prepared = self._prepared(converted=False)

        with (
            patch.object(
                backend,
                "preflight_serial_send_file",
                return_value=(
                    {"success": False, "result": "设备在线检查失败"},
                    None,
                    None,
                    None,
                ),
            ),
            patch.object(backend, "send_gcode_file") as stream_mock,
            patch.object(backend, "connect_serial_for_stream") as connect_mock,
        ):
            result = laser_execution.send_file(
                prepared,
                "serial",
                confirmed=True,
                run_in_background=False,
                port="COM3",
            )

        self.assertFalse(result["success"], result)
        self.assertIn("设备在线检查失败", result["result"])
        stream_mock.assert_not_called()
        connect_mock.assert_not_called()

    def test_network_sync_preflight_failure_does_not_stream(self):
        prepared = self._prepared(converted=False)

        with (
            patch.object(
                backend,
                "preflight_network_send_file",
                return_value=(
                    {"success": False, "result": "设备在线检查失败"},
                    None,
                ),
            ),
            patch.object(backend, "send_telnet_gcode_file") as stream_mock,
        ):
            result = laser_execution.send_file(
                prepared,
                "network",
                confirmed=True,
                run_in_background=False,
                host="laser.local",
            )

        self.assertFalse(result["success"], result)
        self.assertIn("设备在线检查失败", result["result"])
        stream_mock.assert_not_called()

    def test_cancel_serial_job_refuses_all_terminal_statuses(self):
        job_dir = self._tmpdir

        for stored_status in ("completed", "failed", "cancelled", "canceled"):
            with self.subTest(stored_status=stored_status):
                job_id = uuid.uuid4().hex
                job_file = os.path.join(job_dir, f"{job_id}.json")
                backend.write_job_file(
                    job_file,
                    {
                        "job_id": job_id,
                        "status": stored_status,
                        "process_id": 12345,
                        "result": {"success": True},
                    },
                )
                with (
                    patch.object(backend, "SERIAL_JOBS_DIR", job_dir),
                    patch.object(backend.os, "kill") as kill_mock,
                ):
                    result = laser_execution.cancel_job(job_id, "serial")

                self.assertFalse(result["success"], result)
                self.assertIn("任务已结束", result["result"])
                kill_mock.assert_not_called()

    def test_cancel_network_running_job_kills_and_marks_cancelled(self):
        job_dir = self._tmpdir
        job_id = uuid.uuid4().hex
        job_file = os.path.join(job_dir, f"{job_id}.json")
        backend.write_job_file(
            job_file,
            {
                "job_id": job_id,
                "status": "running",
                "process_id": 54321,
                "result": None,
            },
        )

        with (
            patch.object(backend, "NETWORK_JOBS_DIR", job_dir),
            patch.object(backend.os, "kill") as kill_mock,
        ):
            result = laser_execution.cancel_job(job_id, "network")

        self.assertTrue(result["success"], result)
        kill_mock.assert_called_once_with(54321, backend.signal.SIGTERM)
        status = backend.read_job_file(job_file)
        self.assertEqual(status["status"], "cancelled")
        self.assertEqual(status["result"]["job_id"], job_id)

    def test_serial_send_error_returns_failure_via_public_entry(self):
        prepared = self._prepared(converted=False)

        class FakeSerial:
            def close(self):
                return None

        with (
            patch.object(
                backend,
                "preflight_serial_send_file",
                return_value=(None, FakeSerial(), "COM3", {"probe_command": "?"}),
            ),
            patch.object(
                backend,
                "send_gcode_file",
                return_value=(3, 1, "发送第 2 行失败: G1 X1 Y1，原因: error:2"),
            ),
        ):
            result = laser_execution.send_file(
                prepared,
                "serial",
                confirmed=True,
                run_in_background=False,
                port="COM3",
            )

        self.assertFalse(result["success"], result)
        self.assertIn("发送第 2 行失败", result["result"])
        self.assertEqual(result["detail"]["errors"], 1)
        self.assertEqual(result["detail"]["send_error"], result["result"])

    def test_probe_serial_grbl_success_closes_serial(self):
        class FakeSerial:
            def __init__(self):
                self.closed = False

            def close(self):
                self.closed = True

        ser = FakeSerial()
        with patch.object(
            backend,
            "preflight_serial_send_file",
            return_value=(
                None,
                ser,
                "COM3",
                {"probe_command": "?", "probe_response": "<Idle>"},
            ),
        ) as preflight_mock:
            result = laser_execution.probe_serial_grbl(port="COM3", baudrate=115200)

        preflight_mock.assert_called_once_with("COM3", 115200)
        self.assertTrue(ser.closed)
        self.assertTrue(result["success"], result)
        self.assertEqual(result["result"]["port"], "COM3")
        self.assertEqual(result["result"]["baudrate"], 115200)
        self.assertEqual(result["result"]["probe_command"], "?")
        self.assertEqual(result["result"]["probe_response"], "<Idle>")

    def test_probe_serial_grbl_preflight_failure_still_closes_serial(self):
        class FakeSerial:
            def __init__(self):
                self.closed = False

            def close(self):
                self.closed = True

        ser = FakeSerial()
        failure = {"success": False, "result": "设备在线检查失败", "detail": {"port": "COM3"}}
        with patch.object(
            backend,
            "preflight_serial_send_file",
            return_value=(failure, ser, None, None),
        ):
            result = laser_execution.probe_serial_grbl(port="COM3")

        self.assertTrue(ser.closed)
        self.assertFalse(result["success"], result)
        self.assertEqual(result["result"], "设备在线检查失败")

    def test_probe_serial_grbl_preflight_and_close_failure_preserves_both_errors(self):
        class FakeSerial:
            def close(self):
                raise OSError("close boom")

        failure = {
            "success": False,
            "result": "设备在线检查失败",
            "detail": {"port": "COM3"},
        }
        with patch.object(
            backend,
            "preflight_serial_send_file",
            return_value=(failure, FakeSerial(), None, None),
        ):
            result = laser_execution.probe_serial_grbl(port="COM3")

        self.assertFalse(result["success"], result)
        self.assertEqual(result["result"], "设备在线检查失败")
        self.assertEqual(result["detail"]["port"], "COM3")
        self.assertIn("close boom", result["detail"]["close_error"])

    def test_probe_serial_grbl_close_failure_returns_structured_error(self):
        class FakeSerial:
            def close(self):
                raise OSError("close boom")

        with patch.object(
            backend,
            "preflight_serial_send_file",
            return_value=(
                None,
                FakeSerial(),
                "COM5",
                {"probe_command": "?", "probe_response": "<Idle>"},
            ),
        ):
            result = laser_execution.probe_serial_grbl(port="COM5", baudrate=9600)

        self.assertFalse(result["success"], result)
        self.assertIn("关闭串口失败", result["result"])
        self.assertEqual(result["detail"]["probe"]["port"], "COM5")
        self.assertEqual(result["detail"]["probe"]["probe_response"], "<Idle>")
        self.assertIn("close boom", result["detail"]["close_error"])

    def test_resolve_laser_network_host_delegates_and_rejects_scheme(self):
        with patch.object(backend, "DEFAULT_NETWORK_HOST", ""):
            host, error = laser_execution.resolve_laser_network_host("")
            self.assertIsNone(host)
            self.assertIn("host", error)

            host, error = laser_execution.resolve_laser_network_host("http://laser.local")
            self.assertIsNone(host)
            self.assertIn("http://", error)

            host, error = laser_execution.resolve_laser_network_host("laser.local")
            self.assertEqual(host, "laser.local")
            self.assertIsNone(error)

    def test_resolve_laser_connection_mode_defaults_aliases_and_errors(self):
        with patch.object(laser_execution, "DEFAULT_CONNECTION_MODE", "network"):
            mode, error = laser_execution.resolve_laser_connection_mode("")
            self.assertEqual(mode, "network")
            self.assertIsNone(error)

        with patch.object(laser_execution, "DEFAULT_CONNECTION_MODE", "serial"):
            mode, error = laser_execution.resolve_laser_connection_mode("")
            self.assertEqual(mode, "serial")
            self.assertIsNone(error)

        aliases = {
            "serial": "serial",
            "usb": "serial",
            "com": "serial",
            "串口": "serial",
            "network": "network",
            "net": "network",
            "wifi": "network",
            "http": "network",
            "telnet": "network",
            "网络": "network",
        }
        for raw, expected in aliases.items():
            mode, error = laser_execution.resolve_laser_connection_mode(raw)
            self.assertEqual(mode, expected, raw)
            self.assertIsNone(error, raw)

        mode, error = laser_execution.resolve_laser_connection_mode(
            "", default_mode="wifi"
        )
        self.assertEqual(mode, "network")
        self.assertIsNone(error)

        with patch.object(laser_execution, "DEFAULT_CONNECTION_MODE", "netwrok"):
            mode, error = laser_execution.resolve_laser_connection_mode("")
            self.assertIsNone(mode)
            self.assertIn("LASER_DEFAULT_CONNECTION_MODE", error)

        mode, error = laser_execution.resolve_laser_connection_mode("bluetooth")
        self.assertIsNone(mode)
        self.assertIn("connection_mode", error)
        self.assertIn("serial / network", error)

    def test_send_status_cancel_still_reject_empty_mode_after_facade_expansion(self):
        result = laser_execution.send_file(self._prepared(), "", confirmed=False)
        self.assertFalse(result["success"])
        self.assertIn("connection_mode", result["result"])
        result = laser_execution.job_status("x", "")
        self.assertFalse(result["success"])
        result = laser_execution.cancel_job("x", "")
        self.assertFalse(result["success"])


if __name__ == "__main__":
    unittest.main()
