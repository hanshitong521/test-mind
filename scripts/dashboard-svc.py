"""Real SCM service host for the TestMind dashboard (stdlib only, no pywin32).

Why this exists
---------------
`python.exe` is not an SCM-aware executable: it never calls
`StartServiceCtrlDispatcherW`, so a service pointing at it reports 1053
("did not respond in a timely fashion") and stays stuck in Stopped while the
child process actually runs. That mismatch made `Get-Service` useless as a
health signal.

This module is a proper Win32 service host:
  * `StartServiceCtrlDispatcherW` wires us into the SCM
  * a `SERVICE_STATUS` block is published and kept accurate (START_PENDING ->
    RUNNING -> STOP_PENDING -> STOPPED)
  * a control handler accepts STOP and SHUTDOWN
  * the dashboard runs in a child process; the handler tree-kills it on stop
    so no orphan keeps port 8901 bound

The dashboard has no native extension dependency, so the child is started with
the same interpreter running this file (sys.executable), keeping service and
CLI launches consistent.

Run modes
---------
    python scripts\\dashboard-svc.py install     # install + set auto + start
    python scripts\\dashboard-svc.py remove      # delete the service entry
    python scripts\\dashboard-svc.py debug       # run in foreground (no SCM)
    (no args)                                    # SCM entry point
"""
import ctypes
import ctypes.wintypes as wintypes
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

SERVICE_NAME = "TestMindDashboard"
SERVICE_DISPLAY = "TestMind Dashboard (8901)"
REPO = Path(__file__).resolve().parent.parent
PORT = 8901

# ---------------------------------------------------------------- Win32 consts
SERVICE_WIN32_OWN_PROCESS = 0x00000010
SERVICE_ACCEPT_STOP = 0x00000001
SERVICE_ACCEPT_SHUTDOWN = 0x00000004

SERVICE_CONTROL_STOP = 0x00000001
SERVICE_CONTROL_SHUTDOWN = 0x00000005

SERVICE_STOPPED = 0x00000001
SERVICE_START_PENDING = 0x00000002
SERVICE_STOP_PENDING = 0x00000003
SERVICE_RUNNING = 0x00000004

SERVICE_AUTO_START = 0x00000002
ERROR_SERVICE_ALREADY_RUNNING = 1056
NO_ERROR = 0
ERROR_EXCEPTION_IN_SERVICE = 1066

OPEN_SC_MANAGER_ALL = 0xF003F
SERVICE_ALL_ACCESS = 0xF01FF

advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)

# --- prototypes -------------------------------------------------------------
# Declaring these matters on 64-bit: without restype=HANDLE ctypes truncates the
# returned pointer to a 32-bit int and every later call fails with a bogus handle.
advapi32.OpenSCManagerW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD]
advapi32.OpenSCManagerW.restype = wintypes.HANDLE

advapi32.OpenServiceW.argtypes = [wintypes.HANDLE, wintypes.LPCWSTR, wintypes.DWORD]
advapi32.OpenServiceW.restype = wintypes.HANDLE

advapi32.CreateServiceW.restype = wintypes.HANDLE
advapi32.CreateServiceW.argtypes = [
    wintypes.HANDLE, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
    wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, wintypes.LPCWSTR,
    wintypes.LPCWSTR, ctypes.c_void_p, wintypes.LPCWSTR, wintypes.LPCWSTR,
    wintypes.LPCWSTR,
]

advapi32.DeleteService.argtypes = [wintypes.HANDLE]
advapi32.DeleteService.restype = wintypes.BOOL

advapi32.CloseServiceHandle.argtypes = [wintypes.HANDLE]
advapi32.CloseServiceHandle.restype = wintypes.BOOL

advapi32.StartServiceW.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.c_void_p]
advapi32.StartServiceW.restype = wintypes.BOOL

advapi32.RegisterServiceCtrlHandlerExW.argtypes = [
    wintypes.LPCWSTR, ctypes.c_void_p, ctypes.c_void_p
]
advapi32.RegisterServiceCtrlHandlerExW.restype = wintypes.HANDLE

advapi32.SetServiceStatus.argtypes = [wintypes.HANDLE, ctypes.c_void_p]
advapi32.SetServiceStatus.restype = wintypes.BOOL

advapi32.StartServiceCtrlDispatcherW.argtypes = [ctypes.c_void_p]
advapi32.StartServiceCtrlDispatcherW.restype = wintypes.BOOL


class SERVICE_STATUS(ctypes.Structure):
    _fields_ = [
        ("dwServiceType", wintypes.DWORD),
        ("dwCurrentState", wintypes.DWORD),
        ("dwControlsAccepted", wintypes.DWORD),
        ("dwWin32ExitCode", wintypes.DWORD),
        ("dwServiceSpecificExitCode", wintypes.DWORD),
        ("dwCheckPoint", wintypes.DWORD),
        ("dwWaitHint", wintypes.DWORD),
    ]


class SERVICE_TABLE_ENTRY(ctypes.Structure):
    _fields_ = [
        ("lpServiceName", wintypes.LPWSTR),
        ("lpServiceProc", ctypes.c_void_p),
    ]


HANDLER_FUNC = ctypes.WINFUNCTYPE(
    None, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p, ctypes.c_void_p
)
SERVICE_MAIN_FUNC = ctypes.WINFUNCTYPE(
    None, wintypes.DWORD, ctypes.POINTER(wintypes.LPWSTR)
)

# Module level so the GC never collects the child handle or the status block.
_child = None
_status = SERVICE_STATUS()
_status_handle = None
_stop_event = threading.Event()


def _report(state, exit_code=NO_ERROR, hint=0, checkpoint=0):
    _status.dwServiceType = SERVICE_WIN32_OWN_PROCESS
    _status.dwCurrentState = state
    _status.dwControlsAccepted = (
        SERVICE_ACCEPT_STOP | SERVICE_ACCEPT_SHUTDOWN
        if state == SERVICE_RUNNING
        else 0
    )
    _status.dwWin32ExitCode = exit_code
    _status.dwServiceSpecificExitCode = 0
    _status.dwCheckPoint = checkpoint
    _status.dwWaitHint = hint
    advapi32.SetServiceStatus(wintypes.HANDLE(_status_handle), ctypes.byref(_status))


def _log(msg):
    log = Path(os.environ.get("TEMP", r"C:\Windows\Temp")) / "testmind-dashboard-svc.log"
    try:
        with open(log, "a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')}  {msg}\n")
    except OSError:
        pass


def _kill_child():
    global _child
    if _child is None or _child.poll() is not None:
        return
    try:
        subprocess.run(
            ["taskkill", "/PID", str(_child.pid), "/T", "/F"],
            capture_output=True, timeout=20,
        )
    except Exception:
        try:
            _child.kill()
        except Exception:
            pass
    try:
        _child.wait(timeout=15)
    except Exception:
        pass


def _handler(control, event_type, event_data, context):
    """STOP / SHUTDOWN -> tear the child down and report STOPPED."""
    if control == SERVICE_CONTROL_STOP:
        _report(SERVICE_STOP_PENDING, hint=15000)
        _stop_event.set()
        _kill_child()
        _report(SERVICE_STOPPED)
    elif control == SERVICE_CONTROL_SHUTDOWN:
        _stop_event.set()
        _kill_child()
        _report(SERVICE_STOPPED)
    return 0


_handler_ref = HANDLER_FUNC(_handler)  # keep a strong ref


def _service_main(argc, argv):
    global _status_handle, _child
    _status_handle = advapi32.RegisterServiceCtrlHandlerExW(
        SERVICE_NAME, ctypes.cast(_handler_ref, ctypes.c_void_p), None
    )
    if not _status_handle:
        return
    _report(SERVICE_START_PENDING, hint=15000, checkpoint=1)

    try:
        _log("service start: launching dashboard child")
        _child = subprocess.Popen(
            [sys.executable, str(REPO / "scripts" / "dashboard.py"), "--port", str(PORT)],
            cwd=str(REPO),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        _log(f"child pid={_child.pid}")
        _report(SERVICE_RUNNING)
    except Exception as e:  # pragma: no cover - service path
        _log(f"ERROR launching child: {e!r}")
        _report(SERVICE_STOPPED, exit_code=ERROR_EXCEPTION_IN_SERVICE)
        return

    # Supervise: if the child dies on its own, exit the service so the state
    # never lies about a dashboard that is no longer serving.
    while not _stop_event.is_set():
        if _child.poll() is not None:
            _log(f"child exited rc={_child.returncode}; stopping service")
            _report(SERVICE_STOPPED, exit_code=ERROR_EXCEPTION_IN_SERVICE)
            return
        time.sleep(1)

    _log("service stop: child cleaned up")
    _report(SERVICE_STOPPED)


_service_main_ref = SERVICE_MAIN_FUNC(_service_main)


# ---------------------------------------------------------------- install/remove
def cli_install():
    scm = advapi32.OpenSCManagerW(None, None, OPEN_SC_MANAGER_ALL)
    if not scm:
        print("OpenSCManager failed:", ctypes.WinError(ctypes.get_last_error()))
        return 1

    # Delete any pre-existing entry first, so we never leave a service whose
    # image path does not match the current layout.
    existing = advapi32.OpenServiceW(scm, SERVICE_NAME, SERVICE_ALL_ACCESS)
    if existing:
        advapi32.DeleteService(existing)
        advapi32.CloseServiceHandle(existing)
        print("removed pre-existing service")

    cmdline = f'"{sys.executable}" "{Path(__file__).resolve()}"'
    svc = advapi32.CreateServiceW(
        scm, SERVICE_NAME, SERVICE_DISPLAY,
        SERVICE_ALL_ACCESS, SERVICE_WIN32_OWN_PROCESS,
        SERVICE_AUTO_START, 0, cmdline, None, None, None, None, None,
    )
    if not svc:
        print("CreateService failed:", ctypes.WinError(ctypes.get_last_error()))
        advapi32.CloseServiceHandle(scm)
        return 1
    advapi32.CloseServiceHandle(svc)
    print(f"installed {SERVICE_NAME} -> {cmdline}")

    svc = advapi32.OpenServiceW(scm, SERVICE_NAME, SERVICE_ALL_ACCESS)
    ok = advapi32.StartServiceW(svc, 0, None)
    err = ctypes.get_last_error()
    if not ok and err != ERROR_SERVICE_ALREADY_RUNNING:
        print("StartService failed:", ctypes.WinError(err))
    else:
        print("service started")
    advapi32.CloseServiceHandle(svc)
    advapi32.CloseServiceHandle(scm)
    return 0


def cli_remove():
    scm = advapi32.OpenSCManagerW(None, None, OPEN_SC_MANAGER_ALL)
    svc = advapi32.OpenServiceW(scm, SERVICE_NAME, SERVICE_ALL_ACCESS)
    if svc:
        advapi32.DeleteService(svc)
        advapi32.CloseServiceHandle(svc)
        print("removed")
    else:
        print("not installed")
    advapi32.CloseServiceHandle(scm)
    return 0


def cli_debug():
    """Foreground run without the SCM (useful for local testing)."""
    _service_main(0, None)
    return 0


if __name__ == "__main__":
    arg = sys.argv[1].lower() if len(sys.argv) > 1 else ""
    if arg == "install":
        sys.exit(cli_install())
    if arg == "remove":
        sys.exit(cli_remove())
    if arg == "debug":
        sys.exit(cli_debug())
    # SCM entry point
    entries = (SERVICE_TABLE_ENTRY * 2)()
    entries[0].lpServiceName = SERVICE_NAME
    entries[0].lpServiceProc = ctypes.cast(_service_main_ref, ctypes.c_void_p)
    entries[1].lpServiceName = None
    entries[1].lpServiceProc = None
    if not advapi32.StartServiceCtrlDispatcherW(ctypes.cast(entries, ctypes.c_void_p)):
        err = ctypes.get_last_error()
        print(f"StartServiceCtrlDispatcher failed ({err}): {ctypes.WinError(err)}",
              file=sys.stderr)
        sys.exit(1)
