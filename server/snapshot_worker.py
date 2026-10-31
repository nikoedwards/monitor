"""Disposable Chromium worker for production web snapshots."""
from __future__ import annotations

import json
import ctypes
import os
import sys
from pathlib import Path

from .snapshot import _playwright_capture


_JOB_HANDLE = None


def _attach_windows_job() -> None:
    """Make every Chromium child die with this disposable worker on Windows."""
    global _JOB_HANDLE
    if os.name != "nt":
        return
    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateJobObjectW.restype = ctypes.c_void_p
        kernel32.CreateJobObjectW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p]
        kernel32.GetCurrentProcess.restype = ctypes.c_void_p
        kernel32.SetInformationJobObject.restype = ctypes.c_int
        kernel32.SetInformationJobObject.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, ctypes.c_uint32]
        kernel32.AssignProcessToJobObject.restype = ctypes.c_int
        kernel32.AssignProcessToJobObject.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        kernel32.CloseHandle.restype = ctypes.c_int
        kernel32.CloseHandle.argtypes = [ctypes.c_void_p]

        class BasicLimitInformation(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", ctypes.c_longlong),
                ("PerJobUserTimeLimit", ctypes.c_longlong),
                ("LimitFlags", ctypes.c_uint32),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", ctypes.c_uint32),
                ("Affinity", ctypes.c_size_t),
                ("PriorityClass", ctypes.c_uint32),
                ("SchedulingClass", ctypes.c_uint32),
            ]

        class IoCounters(ctypes.Structure):
            _fields_ = [("ReadOperationCount", ctypes.c_uint64), ("WriteOperationCount", ctypes.c_uint64), ("OtherOperationCount", ctypes.c_uint64), ("ReadTransferCount", ctypes.c_uint64), ("WriteTransferCount", ctypes.c_uint64), ("OtherTransferCount", ctypes.c_uint64)]

        class ExtendedLimitInformation(ctypes.Structure):
            _fields_ = [("BasicLimitInformation", BasicLimitInformation), ("IoInfo", IoCounters), ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t), ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]

        job = kernel32.CreateJobObjectW(None, None)
        if not job:
            return
        info = ExtendedLimitInformation()
        # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        info.BasicLimitInformation.LimitFlags = 0x2000
        if not kernel32.SetInformationJobObject(job, 9, ctypes.byref(info), ctypes.sizeof(info)):
            kernel32.CloseHandle(job)
            return
        if not kernel32.AssignProcessToJobObject(job, kernel32.GetCurrentProcess()):
            kernel32.CloseHandle(job)
            return
        _JOB_HANDLE = job
    except Exception:
        # Restricted Windows environments may already place us in a job.
        pass


def _prefer_worker_oom_kill() -> None:
    """Make Linux cgroup OOM handling prefer this disposable worker."""
    try:
        Path("/proc/self/oom_score_adj").write_text("500", encoding="ascii")
    except (OSError, ValueError):
        # Windows and restricted containers do not expose this control.
        pass


def main() -> int:
    _attach_windows_job()
    _prefer_worker_oom_kill()
    request = json.loads(sys.stdin.read())
    try:
        result = _playwright_capture(
            request["url"],
            Path(request["png_path"]),
            Path(request["html_path"]),
            request.get("source_html") or "",
            safe_mode=bool(request.get("safe_mode")),
        )
    except Exception as exc:
        print(json.dumps({"error_type": "snapshot_capture", "error": str(exc)}, ensure_ascii=False))
        return 0
    print(json.dumps(result or {"error": "Playwright 未返回结果"}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
