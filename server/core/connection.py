"""Own the CorelDRAW COM proxy and serialize operations on one Windows STA."""

import os
import queue
import re
import struct
import sys
import threading
from concurrent.futures import CancelledError, Future
from dataclasses import dataclass, field
from typing import Callable

import psutil
from loguru import logger

if sys.platform == "win32":
    import winreg

    import pythoncom
    import win32com.client
    import win32event
    import win32ts

# RPC failures indicating a dead proxy, as opposed to a busy app or a tool error.
_STALE = {0x80010108, 0x800401FD, 0x800706BA, 0x800706BE, 0x80010007, 0x80010012}
_BUSY = {0x80010001, 0x8001010A}
_NOT_RUNNING = 0x800401E3
_SESSION_MUTEX = r"Local\CorelDRAW-MCP-COM"


def _hresult(exc):
    return getattr(exc, "hresult", 0) & 0xFFFFFFFF


def _registered_server_is_running(prog_id):
    """Check the configured Corel executable, same Windows user and logon session."""
    clsid = pythoncom.MakeIID(prog_id)
    with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, rf"CLSID\{clsid}\LocalServer32") as key:
        command, _ = winreg.QueryValueEx(key, "")
    match = re.match(r'^\s*"([^"]+\.exe)"|^\s*(.+?\.exe)(?:\s|$)', command, re.IGNORECASE)
    if not match:
        return False
    expected = os.path.normcase(os.path.expandvars(match.group(1) or match.group(2)))
    pid = os.getpid()
    user = psutil.Process(pid).username().casefold()
    session = win32ts.ProcessIdToSessionId(pid)
    # Fresh Process objects avoid cached process metadata across reconnects.
    for candidate in psutil.pids():
        try:
            process = psutil.Process(candidate)
            if (
                process.name().casefold() == os.path.basename(expected).casefold()
                and os.path.normcase(process.exe()) == expected
                and process.username().casefold() == user
                and win32ts.ProcessIdToSessionId(candidate) == session
                and process.is_running()
            ):
                return True
        except (psutil.Error, OSError, pythoncom.error):
            continue
    return False


@dataclass
class ConnectionConfig:
    app_name: str = field(
        default_factory=lambda: os.environ.get("COREL_CORELDRAW_APP_NAME", "CorelDRAW.Application")
    )


@dataclass(frozen=True)
class ConnectionStatus:
    connected: bool = False
    app_running: bool = False
    version: str | None = None
    last_error: str | None = None


class _BridgeError(RuntimeError):
    def __init__(self, state, message):
        super().__init__(message)
        self.state = state


@dataclass
class _Task:
    func: Callable
    future: Future = field(default_factory=Future)
    started: threading.Event = field(default_factory=threading.Event)


class _COMThread(threading.Thread):
    def __init__(self, cleanup):
        super().__init__(daemon=True, name="CorelDRAW-COM-STA")
        self._queue = queue.Queue()
        # A bounded number of callers may wait (including the active call).
        self._slots = threading.BoundedSemaphore(4)
        self._lock = threading.Lock()
        self._closed = False
        self._init_error = None
        self._cleanup = cleanup

    def run(self):
        initialized = False
        try:
            if sys.platform == "win32":
                pythoncom.CoInitializeEx(pythoncom.COINIT_APARTMENTTHREADED)
                initialized = True
        except Exception as exc:
            self._init_error = str(exc)
            logger.error("Could not initialize the CorelDRAW STA: {}", exc)

        try:
            while True:
                try:
                    task = self._queue.get(timeout=0.05)
                except queue.Empty:
                    if initialized:
                        pythoncom.PumpWaitingMessages()
                    continue
                if task is None:
                    return
                try:
                    if task.future.set_running_or_notify_cancel():
                        task.started.set()
                        try:
                            if self._init_error:
                                raise _BridgeError("unavailable", self._init_error)
                            task.future.set_result(task.func())
                        except Exception as exc:
                            task.future.set_exception(exc.with_traceback(None))
                finally:
                    self._slots.release()
                if initialized:
                    pythoncom.PumpWaitingMessages()
        finally:
            self._cleanup()
            if initialized:
                pythoncom.CoUninitialize()

    def call(self, func, *, timeout=60.0, queue_timeout=2.0):
        with self._lock:
            if self._closed or not self.is_alive():
                raise _BridgeError("unavailable", "CorelDRAW worker is not running")
            if not self._slots.acquire(blocking=False):
                raise _BridgeError("busy", "CorelDRAW worker is busy; request was not started")
            task = _Task(func)
            self._queue.put(task)

        if not task.started.wait(queue_timeout) and task.future.cancel():
            raise _BridgeError("busy", "CorelDRAW worker is busy; queued request was cancelled before it started")
        try:
            return task.future.result(timeout)
        except CancelledError:
            raise _BridgeError("unavailable", "CorelDRAW worker stopped; request was not started") from None
        except TimeoutError:
            if task.future.done():
                return task.future.result()
            raise _BridgeError(
                "timeout", f"CorelDRAW did not finish within {timeout:g}s. "
                "The operation may still be running; verify its outcome before retrying."
            ) from None

    def shutdown(self):
        with self._lock:
            self._closed = True
            while True:
                try:
                    task = self._queue.get_nowait()
                except queue.Empty:
                    break
                if task is not None:
                    task.future.cancel()
                    task.started.set()
                    self._slots.release()
            self._queue.put(None)


class CorelDrawConnection:
    def __init__(self, config=None):
        self.config = config or ConnectionConfig()
        self._app = None
        self._status = ConnectionStatus()
        self._com_thread = _COMThread(self._clear_proxy)
        self._com_thread.start()

    @property
    def app(self):
        if threading.current_thread() is not self._com_thread:
            raise RuntimeError("Access to CorelDRAW must run on the COM worker")
        return self._app

    @property
    def status(self):
        """Last successful attachment or known disconnect; use probe for live state."""
        return self._status

    def _clear_proxy(self, error=None):
        self._app = None
        self._status = ConnectionStatus(last_error=error)

    def _attach(self):
        if sys.platform != "win32" or struct.calcsize("P") != 8:
            raise _BridgeError("unavailable", "CorelDRAW automation requires 64-bit Python on Windows")
        try:
            app = win32com.client.GetActiveObject(self.config.app_name)
        except Exception as exc:
            if _hresult(exc) != _NOT_RUNNING:
                raise
            # CorelDRAW 2022 can run without publishing an application object in
            # the ROT. Only use its class factory when the registered program is
            # already running for our interactive user in this session.
            if not _registered_server_is_running(self.config.app_name):
                raise _BridgeError(
                    "not_running", "CorelDRAW is not available in this Windows session. "
                    "Open it in your desktop session and dismiss any startup dialog."
                ) from None
            app = win32com.client.Dispatch(self.config.app_name)
        version = str(app.Version)
        self._app = app
        self._status = ConnectionStatus(connected=True, app_running=True, version=version)
        return version

    def _ensure_attached(self):
        if self._app is not None:
            try:
                return str(self._app.Version)
            except Exception as exc:
                if _hresult(exc) not in _STALE:
                    raise
                self._clear_proxy(str(exc))
        return self._attach()

    def _execute(self, func, args, kwargs, *, probe=False):
        if sys.platform != "win32":
            return self._execute_exclusive(func, args, kwargs, probe=probe)
        # Separate Codex chats can launch separate stdio bridges. Never run
        # simultaneous operations against the same interactive CorelDRAW session.
        mutex = win32event.CreateMutex(None, False, _SESSION_MUTEX)
        try:
            state = win32event.WaitForSingleObject(mutex, 0)
            if state == win32event.WAIT_TIMEOUT:
                raise _BridgeError("busy", "Another CorelDRAW MCP client is working; request was not started")
            try:
                return self._execute_exclusive(func, args, kwargs, probe=probe)
            finally:
                win32event.ReleaseMutex(mutex)
        finally:
            mutex.Close()

    def _execute_exclusive(self, func, args, kwargs, *, probe=False):
        # Catch and convert here, so COM exceptions/references never leave the STA.
        action_started = False
        try:
            version = self._ensure_attached()
            if probe:
                return {"success": True, "state": "ready", "version": version}
            action_started = True
            return {"success": True, "result": func(*args, **kwargs)}
        except Exception as exc:
            code = _hresult(exc)
            if code in _STALE:
                self._clear_proxy(str(exc))
                state = "disconnected"
                error = "CorelDRAW disconnected. The next call will attach to the running instance."
                if action_started:
                    error += " Verify the document or output before retrying; the operation was not replayed."
            elif code in _BUSY:
                state = "busy"
                error = "CorelDRAW rejected a COM request while busy. Check for an open dialog and verify any partial changes."
            elif isinstance(exc, _BridgeError):
                state, error = exc.state, str(exc)
                self._clear_proxy(error)
            else:
                state, error = ("tool_error" if action_started else "unavailable"), str(exc) or type(exc).__name__
                if not action_started:
                    self._clear_proxy(error)
            return {"success": False, "state": state, "result": None, "error": error}

    def _call(self, func, args=(), kwargs=None, *, probe=False):
        try:
            result = self._com_thread.call(
                lambda: self._execute(func, args, kwargs or {}, probe=probe),
                timeout=2.0 if probe else 60.0,
                queue_timeout=0.1 if probe else 2.0,
            )
        except _BridgeError as exc:
            result = {"success": False, "state": exc.state, "result": None, "error": str(exc)}
        except Exception as exc:
            result = {"success": False, "state": "unavailable", "result": None, "error": str(exc)}
        if result.get("state") in {"timeout", "disconnected", "unavailable"}:
            logger.warning("CorelDRAW {}: {}", result["state"], result["error"])
        return result

    def probe(self):
        """Check the COM apartment and running application without opening a document."""
        return self._call(None, probe=True)

    def connect(self):
        return self.probe()["success"]

    def disconnect(self):
        # Only the worker releases its COM proxy. Never close the user's CorelDRAW.
        self._com_thread.shutdown()

    def is_alive(self):
        return self.probe()["success"]

    def safe_call(self, func, *args, **kwargs):
        """Run one operation once. Never replay a callback after any error."""
        return self._call(func, args, kwargs)


_connection = None
_connection_lock = threading.Lock()


def get_connection():
    global _connection
    with _connection_lock:
        if _connection is None:
            _connection = CorelDrawConnection()
        return _connection


def init_connection(config=None):
    global _connection
    with _connection_lock:
        if _connection is None:
            _connection = CorelDrawConnection(config)
        conn = _connection
    return conn.connect()


def close_connection():
    global _connection
    with _connection_lock:
        if _connection is not None:
            _connection.disconnect()
            _connection = None
