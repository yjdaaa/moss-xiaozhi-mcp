import subprocess
import unittest
from unittest.mock import patch

from tools import command_execution_tool


class FakeMcp:
    def __init__(self):
        self.tools = {}

    def tool(self):
        def decorator(func):
            self.tools[func.__name__] = func
            return func

        return decorator


class CommandExecutionToolTests(unittest.TestCase):
    def test_command_execution_uses_mocked_subprocess_on_windows(self):
        fake = FakeMcp()
        command_execution_tool.register_tool(fake)
        completed = subprocess.CompletedProcess(
            args="echo hello",
            returncode=0,
            stdout="hello\n",
            stderr="",
        )

        with (
            patch.object(command_execution_tool.platform, "system", return_value="Windows"),
            patch.object(command_execution_tool.subprocess, "run", return_value=completed) as run_mock,
        ):
            result = fake.tools["command_execution_tool"](" echo hello ")

        self.assertTrue(result["success"], result)
        self.assertEqual(result["result"], "hello\n")
        run_mock.assert_called_once_with(
            "echo hello",
            shell=True,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

    def test_command_execution_uses_mocked_subprocess_without_shell_off_windows(self):
        fake = FakeMcp()
        command_execution_tool.register_tool(fake)
        completed = subprocess.CompletedProcess(
            args=["echo", "hello"],
            returncode=0,
            stdout="hello\n",
            stderr="",
        )

        with (
            patch.object(command_execution_tool.platform, "system", return_value="Linux"),
            patch.object(command_execution_tool.subprocess, "run", return_value=completed) as run_mock,
        ):
            result = fake.tools["command_execution_tool"](" echo hello ")

        self.assertTrue(result["success"], result)
        self.assertEqual(result["result"], "hello\n")
        run_mock.assert_called_once_with(
            ["echo", "hello"],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

    def test_command_execution_failed_subprocess_returns_structured_error(self):
        fake = FakeMcp()
        command_execution_tool.register_tool(fake)
        error = subprocess.CalledProcessError(
            returncode=2,
            cmd="bad-command",
            output="partial",
            stderr="failed",
        )

        with (
            patch.object(command_execution_tool.platform, "system", return_value="Windows"),
            patch.object(command_execution_tool.subprocess, "run", side_effect=error) as run_mock,
        ):
            result = fake.tools["command_execution_tool"]("bad-command")

        self.assertFalse(result["success"], result)
        self.assertEqual(result["result"], "partial")
        self.assertEqual(result["error"], "failed")
        self.assertEqual(result["returncode"], 2)
        run_mock.assert_called_once()


if __name__ == "__main__":
    unittest.main()
