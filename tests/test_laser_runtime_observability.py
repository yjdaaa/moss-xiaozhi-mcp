import contextlib
import io
import json
import unittest

from core.laser_runtime.observability import configure_laser_logger, log_event


class LaserRuntimeObservabilityTests(unittest.TestCase):
    def test_structured_log_uses_stderr_and_redacts_sensitive_fields(self):
        stdout = io.StringIO()
        stderr = io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            logger = configure_laser_logger(force=True)
            log_event(
                "send_failed",
                logger=logger,
                workflow_id="wf_123",
                job_id="job_123",
                transport="network",
                error_code="device_offline",
                token="secret-token",
                password="secret-password",
                network_host="laser.local",
            )

        self.assertEqual(stdout.getvalue(), "")
        payload = json.loads(stderr.getvalue())
        self.assertEqual(payload["event"], "send_failed")
        self.assertEqual(payload["workflow_id"], "wf_123")
        self.assertEqual(payload["error_code"], "device_offline")
        self.assertEqual(payload["token"], "<redacted>")
        self.assertEqual(payload["password"], "<redacted>")
        self.assertEqual(payload["network_host"], "<redacted>")
        self.assertNotIn("secret-token", stderr.getvalue())
        self.assertNotIn("laser.local", stderr.getvalue())

    def test_unapproved_detail_does_not_leak_url_credentials_or_commands(self):
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            logger = configure_laser_logger(force=True)
            log_event(
                "send_failed",
                logger=logger,
                job_id="job_123",
                detail="POST https://operator:secret@laser.local/run command=M3 S1000",
            )

        output = stderr.getvalue()
        payload = json.loads(output)
        self.assertEqual(payload["detail"], "<redacted>")
        self.assertNotIn("operator", output)
        self.assertNotIn("secret", output)
        self.assertNotIn("laser.local", output)
        self.assertNotIn("M3 S1000", output)


if __name__ == "__main__":
    unittest.main()
