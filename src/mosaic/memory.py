"""This process's memory use, without a dependency."""
from __future__ import annotations

import sys


def rss_bytes() -> int | None:
    """Resident memory of this process now, or None where it cannot be read."""
    try:
        import psutil  # not required; used when it is installed
        return int(psutil.Process().memory_info().rss)
    except Exception:
        pass
    try:
        if sys.platform.startswith("linux"):
            with open("/proc/self/statm", encoding="ascii") as f:
                resident_pages = int(f.read().split()[1])
            import os
            return resident_pages * os.sysconf("SC_PAGE_SIZE")
        if sys.platform == "win32":
            return _rss_windows()
        if sys.platform == "darwin":
            import resource
            # ru_maxrss is the peak, not the current size; it is the closest
            # figure the standard library has on macOS.
            return int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
    except Exception:
        pass
    return None


def _rss_windows() -> int | None:
    import ctypes
    from ctypes import wintypes

    class Counters(ctypes.Structure):
        _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
                    ("PeakWorkingSetSize", ctypes.c_size_t),
                    ("WorkingSetSize", ctypes.c_size_t),
                    ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                    ("PagefileUsage", ctypes.c_size_t),
                    ("PeakPagefileUsage", ctypes.c_size_t)]

    counters = Counters()
    counters.cb = ctypes.sizeof(Counters)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    psapi = ctypes.WinDLL("psapi", use_last_error=True)
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    ok = psapi.GetProcessMemoryInfo(
        kernel.GetCurrentProcess(), ctypes.byref(counters), counters.cb)
    return int(counters.WorkingSetSize) if ok else None


def format_bytes(n: int | None) -> str:
    if n is None:
        return "not available"
    if n >= 1 << 30:
        return f"{n / (1 << 30):.2f} GB"
    return f"{n / (1 << 20):.0f} MB"
