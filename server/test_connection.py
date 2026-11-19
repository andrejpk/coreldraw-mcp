"""Regression checks for the bridge with no running CorelDRAW dependency."""

import sys
import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch
from uuid import uuid4

from core import connection


class RpcError(Exception):
    hresult = -2147023174  # RPC_S_SERVER_UNAVAILABLE


class BridgeTests(unittest.TestCase):
    def setUp(self):
        isolated_mutex = patch.object(connection, "_SESSION_MUTEX", rf"Local\CorelDRAW-MCP-Test-{uuid4()}")
        isolated_mutex.start()
        self.addCleanup(isolated_mutex.stop)

    def test_expired_queued_work_never_runs(self):
        worker = connection._COMThread(lambda: None)
        worker.start()
        release = threading.Event()
        active = Mock(side_effect=lambda: release.wait(3))
        queued = Mock()
        try:
            with self.assertRaisesRegex(connection._BridgeError, "may still be running"):
                worker.call(active, timeout=0.01)
            with self.assertRaisesRegex(connection._BridgeError, "cancelled before it started"):
                worker.call(queued, queue_timeout=0.01)
            release.set()
            self.assertEqual(worker.call(lambda: "usable"), "usable")
            active.assert_called_once()
            queued.assert_not_called()
        finally:
            release.set()
            worker.shutdown()
            worker.join(3)

    def test_disconnected_callback_is_not_replayed_and_next_request_reattaches(self):
        bridge = connection.CorelDrawConnection()
        self.addCleanup(bridge.disconnect)
        app = SimpleNamespace(Version="24")
        callback = Mock(side_effect=RpcError())
        with patch.object(bridge, "_attach", wraps=lambda: self._attach_fake(bridge, app)) as attach:
            self.assertEqual(bridge.safe_call(callback)["state"], "disconnected")
            callback.assert_called_once()
            self.assertFalse(bridge.status.connected)
            self.assertTrue(bridge.safe_call(lambda: bridge.app.Version)["success"])
            self.assertEqual(attach.call_count, 2)

    def test_stale_proxy_is_replaced_before_starting_the_callback(self):
        bridge = connection.CorelDrawConnection()
        self.addCleanup(bridge.disconnect)

        def get_version(_self):
            raise RpcError()

        dead_app = type("DeadApp", (), {"Version": property(get_version)})
        bridge._com_thread.call(lambda: setattr(bridge, "_app", dead_app()))
        replacement = SimpleNamespace(Version="24")
        with patch.object(bridge, "_attach", side_effect=lambda: self._attach_fake(bridge, replacement)):
            result = bridge.safe_call(lambda: bridge.app.Version)
        self.assertEqual(result, {"success": True, "result": "24"})

    @unittest.skipUnless(sys.platform == "win32", "Windows COM attachment")
    def test_no_activation_when_registered_executable_is_absent(self):
        bridge = connection.CorelDrawConnection()
        self.addCleanup(bridge.disconnect)
        missing = connection.pythoncom.com_error(-2147221021, "Operation unavailable", None, None)
        client = connection.win32com.client
        with (
            patch.object(client, "GetActiveObject", side_effect=missing),
            patch.object(connection, "_registered_server_is_running", return_value=False),
            patch.object(client, "Dispatch") as dispatch,
        ):
            self.assertEqual(bridge.probe()["state"], "not_running")
            dispatch.assert_not_called()

    @unittest.skipUnless(sys.platform == "win32", "Windows named mutex")
    def test_busy_session_does_not_start_a_callback(self):
        events = connection.win32event
        mutex = events.CreateMutex(None, True, connection._SESSION_MUTEX)
        bridge = connection.CorelDrawConnection()
        self.addCleanup(bridge.disconnect)
        callback = Mock()
        try:
            self.assertEqual(bridge.safe_call(callback)["state"], "busy")
            callback.assert_not_called()
        finally:
            events.ReleaseMutex(mutex)
            mutex.Close()

    def test_overrunning_read_is_interrupted_and_frees_the_worker(self):
        worker = connection._COMThread(lambda: None)
        worker.start()
        steps = []

        def long_read():
            # Stands in for a loop of COM calls: the interrupt lands between calls.
            for _ in range(500):
                steps.append(1)
                time.sleep(0.01)
            return "finished"

        try:
            with self.assertRaisesRegex(connection._BridgeError, "cancelled"):
                worker.call(long_read, timeout=0.2, read_only=True)
            self.assertLess(len(steps), 100)
            self.assertIsNone(worker.running())
            self.assertEqual(worker.call(lambda: "usable"), "usable")
        finally:
            worker.shutdown()
            worker.join(3)

    def test_generic_handlers_cannot_swallow_the_interrupt(self):
        worker = connection._COMThread(lambda: None)
        worker.start()

        def stubborn_read():
            for _ in range(500):
                try:
                    time.sleep(0.01)
                except Exception:
                    pass
            return "finished"

        try:
            with self.assertRaisesRegex(connection._BridgeError, "cancelled"):
                worker.call(stubborn_read, timeout=0.2, read_only=True)
        finally:
            worker.shutdown()
            worker.join(3)

    def test_mutating_work_is_not_interrupted_and_status_names_it(self):
        bridge = connection.CorelDrawConnection()
        self.addCleanup(bridge.disconnect)
        release = threading.Event()
        bridge._com_thread.call(lambda: setattr(bridge, "_app", SimpleNamespace(Version="24")))

        def edit_shapes():
            release.wait(3)
            return "edited"

        try:
            outcome = {}
            caller = threading.Thread(target=lambda: outcome.setdefault("r", bridge._call(edit_shapes)))
            caller.start()
            time.sleep(0.2)
            status = bridge.probe()
            self.assertEqual(status["state"], "busy")
            self.assertEqual(status["running"]["operation"], "edit_shapes")
            self.assertFalse(status["running"]["read_only"])
            self.assertIn("edit_shapes", status["error"])
            busy = bridge.read_call(lambda: "never")
            self.assertEqual(busy["state"], "busy")
            self.assertIn("still running edit_shapes", busy["error"])
            release.set()
            caller.join(3)
            self.assertEqual(outcome["r"]["result"], "edited")
        finally:
            release.set()

    def test_only_possible_edits_advance_the_generation(self):
        bridge = connection.CorelDrawConnection()
        self.addCleanup(bridge.disconnect)
        bridge._com_thread.call(lambda: setattr(bridge, "_app", SimpleNamespace(Version="24")))
        start = bridge.generation
        self.assertTrue(bridge.read_call(lambda: 1)["success"])
        self.assertEqual(bridge.generation, start)
        self.assertTrue(bridge.safe_call(lambda: 1)["success"])
        self.assertEqual(bridge.generation, start + 1)
        bridge.safe_call(Mock(side_effect=ValueError("failed edit")))
        self.assertEqual(bridge.generation, start + 2)

    @staticmethod
    def _attach_fake(bridge, app):
        bridge._app = app
        bridge._status = connection.ConnectionStatus(connected=True, app_running=True, version=app.Version)
        return app.Version
