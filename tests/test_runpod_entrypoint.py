import unittest
from contextlib import nullcontext
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import run_services


class FakeProcess:
    def __init__(self, status=None):
        self.returncode = status

    def poll(self):
        return self.returncode

    def terminate(self):
        self.returncode = -15

    def wait(self, timeout=None):
        return self.returncode

    def kill(self):
        self.returncode = -9


class RunpodEntrypointTests(unittest.TestCase):
    def setUp(self):
        run_services.children.clear()
        run_services.shutdown_requested = False

    def tearDown(self):
        run_services.children.clear()
        run_services.shutdown_requested = False

    def test_starts_renderer_api_and_nginx_in_order(self):
        processes = [FakeProcess(), FakeProcess(), FakeProcess()]
        commands = []

        def start(command, cwd):
            commands.append((command, cwd))
            return processes[len(commands) - 1]

        def stop_after_startup(_seconds):
            run_services.shutdown_requested = True

        with (
            patch.object(run_services.subprocess, "Popen", side_effect=start),
            patch.object(run_services, "configure_basic_auth"),
            patch.object(run_services, "urlopen", return_value=nullcontext(type("Response", (), {"status": 200})())),
            patch.object(run_services.time, "sleep", side_effect=stop_after_startup),
        ):
            self.assertEqual(run_services.main(), 0)

        self.assertEqual(len(commands), 3)
        self.assertEqual(commands[0][0], ["node", "dist/server.js"])
        self.assertEqual(commands[1][0][2:5], ["uvicorn", "app:app", "--host"])
        self.assertEqual(commands[1][0][5], "127.0.0.1")
        self.assertEqual(commands[2][0], ["nginx", "-g", "daemon off;"])

    def test_renderer_exit_fails_startup_without_launching_other_services(self):
        failed_renderer = FakeProcess(status=2)

        with (
            patch.object(run_services.subprocess, "Popen", return_value=failed_renderer) as start,
            patch.object(run_services, "configure_basic_auth"),
            patch.object(run_services, "urlopen", side_effect=OSError("renderer unavailable")),
            patch.object(run_services.time, "monotonic", side_effect=[0, 1]),
            patch.object(run_services.time, "sleep"),
        ):
            self.assertEqual(run_services.main(), 1)

        self.assertEqual(start.call_count, 1)

    def test_basic_auth_requires_credentials(self):
        with (
            patch.dict(
                run_services.os.environ,
                {"OPENSHORTS_AUTH_USER": "", "OPENSHORTS_AUTH_PASSWORD": ""},
                clear=False,
            ),
            self.assertRaisesRegex(RuntimeError, "OPENSHORTS_AUTH_USER"),
        ):
            run_services.configure_basic_auth()

    def test_basic_auth_hashes_password_without_putting_it_in_arguments(self):
        with TemporaryDirectory() as temp:
            auth_file = Path(temp) / ".htpasswd"

            def create_hash(command, **kwargs):
                Path(command[-2]).write_text("user:$2y$hash\n", encoding="utf-8")
                return type("Result", (), {"returncode": 0})()

            with (
                patch.object(run_services, "AUTH_FILE", auth_file),
                patch.dict(
                    run_services.os.environ,
                    {"OPENSHORTS_AUTH_USER": "test-user", "OPENSHORTS_AUTH_PASSWORD": "never-log-me"},
                ),
                patch.object(run_services.subprocess, "run", side_effect=create_hash) as run,
            ):
                run_services.configure_basic_auth()

            command = run.call_args.args[0]
            self.assertNotIn("never-log-me", command)
            self.assertEqual(run.call_args.kwargs["input"], "never-log-me\n")
            self.assertEqual(auth_file.read_text(encoding="utf-8"), "user:$2y$hash\n")


if __name__ == "__main__":
    unittest.main()
