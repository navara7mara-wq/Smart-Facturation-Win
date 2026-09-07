"""Authenticated performance, header, CSRF, and resource probe for isolated RC-1."""

from __future__ import annotations

import argparse
import ctypes
import json
import re
import sqlite3
import statistics
import time
from http.cookiejar import CookieJar
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import HTTPCookieProcessor, Request, build_opener



class ProcessMemoryCounters(ctypes.Structure):
    _fields_ = [
        ("cb", ctypes.c_ulong),
        ("PageFaultCount", ctypes.c_ulong),
        ("PeakWorkingSetSize", ctypes.c_size_t),
        ("WorkingSetSize", ctypes.c_size_t),
        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
        ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
        ("PagefileUsage", ctypes.c_size_t),
        ("PeakPagefileUsage", ctypes.c_size_t),
    ]


def resource_sample(pid: int) -> dict[str, int]:
    handle = ctypes.windll.kernel32.OpenProcess(0x0400 | 0x0010, False, pid)
    if not handle:
        raise OSError(f"OpenProcess failed for {pid}")
    counters = ProcessMemoryCounters()
    counters.cb = ctypes.sizeof(counters)
    try:
        if not ctypes.windll.psapi.GetProcessMemoryInfo(handle, ctypes.byref(counters), counters.cb):
            raise OSError("GetProcessMemoryInfo failed")
        return {"rss_bytes": int(counters.WorkingSetSize)}
    finally:
        ctypes.windll.kernel32.CloseHandle(handle)


def request(opener, url: str, data: dict[str, str] | None = None, timeout: int = 120):
    body = urlencode(data).encode() if data is not None else None
    req = Request(url, data=body)
    if body is not None:
        req.add_header("Content-Type", "application/x-www-form-urlencoded")
    start = time.perf_counter()
    try:
        response = opener.open(req, timeout=timeout)
        content = response.read()
        return response.status, dict(response.headers.items()), content, (time.perf_counter() - start) * 1000
    except HTTPError as error:
        content = error.read()
        return error.code, dict(error.headers.items()), content, (time.perf_counter() - start) * 1000


def summary(values: list[float]) -> dict[str, float]:
    ordered = sorted(values)
    return {
        "iterations": len(values),
        "min_ms": round(ordered[0], 2),
        "median_ms": round(statistics.median(ordered), 2),
        "p95_ms": round(ordered[max(0, int(len(ordered) * 0.95) - 1)], 2),
        "max_ms": round(ordered[-1], 2),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8766")
    parser.add_argument("--database", required=True)
    parser.add_argument("--pid", type=int, required=True)
    args = parser.parse_args()
    if "tmp\\rc1-audit" not in args.database:
        raise RuntimeError("Non-QA database refused")

    opener = build_opener(HTTPCookieProcessor(CookieJar()))
    status, _, login, login_ms = request(opener, args.base_url + "/login")
    csrf_match = re.search(rb'name="csrf_token" value="([^"]+)"', login)
    assert status == 200 and csrf_match
    status, headers, dashboard, auth_ms = request(
        opener,
        args.base_url + "/login",
        {"csrf_token": csrf_match.group(1).decode(), "username": "admin", "password": "QA_RC1_Pass123"},
    )
    assert status == 200 and b"Tableau de bord" in dashboard

    resource_before = resource_sample(args.pid)
    timings: dict[str, dict[str, float]] = {}
    routes = {
        "dashboard": "/",
        "billing_table": "/table-facturation-new",
        "invoices": "/invoices",
        "invoice_search": "/invoices?q=QA_RC1_AUDIT_20260825_FAC_01",
        "clients": "/clients",
        "bpu": "/bpu",
        "settings": "/settings",
    }
    for name, route in routes.items():
        values: list[float] = []
        for _ in range(10):
            status, _, _, elapsed = request(opener, args.base_url + route)
            assert status == 200
            values.append(elapsed)
        timings[name] = summary(values)

    document_times: dict[str, dict[str, float]] = {}
    with sqlite3.connect(args.database) as connection:
        invoice_id = connection.execute(
            "SELECT id FROM invoices WHERE invoice_number='QA_RC1_AUDIT_20260825_FAC_01'"
        ).fetchone()[0]
    for name, route in {
        "invoice_xlsx": f"/invoices/export?id={invoice_id}",
        "invoice_pdf": f"/invoices/pdf/facture/{invoice_id}/facture.pdf",
    }.items():
        values = []
        for _ in range(1):
            status, _, content, elapsed = request(opener, args.base_url + route)
            assert status == 200 and content.startswith(b"PK" if name.endswith("xlsx") else b"%PDF")
            values.append(elapsed)
        document_times[name] = summary(values)

    marker = "QA_RC1_CSRF_NEGATIVE_20260825"
    status, _, _, csrf_negative_ms = request(
        opener,
        args.base_url + "/clients",
        {"raison_sociale": marker, "adresse": "must not persist"},
    )
    with sqlite3.connect(args.database) as connection:
        marker_count = connection.execute(
            "SELECT COUNT(*) FROM clients WHERE raison_sociale=?", (marker,)
        ).fetchone()[0]

    status_headers, response_headers, _, _ = request(opener, args.base_url + "/")
    lower_headers = {key.lower(): value for key, value in response_headers.items()}
    resource_after = resource_sample(args.pid)
    output = {
        "startup_access": {"login_get_ms": round(login_ms, 2), "login_post_ms": round(auth_ms, 2)},
        "page_timings": timings,
        "document_timings": document_times,
        "resource_before": resource_before,
        "resource_after": resource_after,
        "resource_delta": {
            "rss_bytes": resource_after["rss_bytes"] - resource_before["rss_bytes"],
        },
        "csrf_negative": {"status": status, "persisted_rows": marker_count, "elapsed_ms": round(csrf_negative_ms, 2)},
        "headers": {
            "status": status_headers,
            "content_security_policy": lower_headers.get("content-security-policy"),
            "x_content_type_options": lower_headers.get("x-content-type-options"),
            "x_frame_options": lower_headers.get("x-frame-options"),
            "referrer_policy": lower_headers.get("referrer-policy"),
            "cache_control": lower_headers.get("cache-control"),
        },
    }
    print(json.dumps(output, indent=2))


if __name__ == "__main__":
    main()
