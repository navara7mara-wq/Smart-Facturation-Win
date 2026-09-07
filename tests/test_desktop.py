import os
import threading
from pathlib import Path

import desktop
import pytest


def test_runtime_marks_only_a_new_database_as_fresh(tmp_path, monkeypatch):
    data_root = tmp_path / "new-user-data"
    monkeypatch.setattr(desktop, "_desktop_data_root", lambda: data_root)
    monkeypatch.setattr(desktop, "_migrate_legacy_data", lambda root: False)
    monkeypatch.delenv("PHOENIX_FRESH_INSTALL_EMPTY", raising=False)
    monkeypatch.delenv("PHOENIX_DEFAULT_ADMIN_KEEP_PASSWORD", raising=False)
    for key in (
        "PHOENIX_DB_PATH", "PHOENIX_UPLOAD_DIR", "PHOENIX_EXPORT_DIR",
        "PHOENIX_BACKUP_DIR", "PHOENIX_LICENSE_PATH", "PHOENIX_LOG_DIR",
    ):
        monkeypatch.delenv(key, raising=False)

    assert desktop.configure_runtime_environment() == data_root
    assert os.environ["PHOENIX_FRESH_INSTALL_EMPTY"] == "1"
    assert "PHOENIX_DEFAULT_ADMIN_KEEP_PASSWORD" not in os.environ
    assert Path(os.environ["PHOENIX_DB_PATH"]) == data_root / "data" / "pos_ai.sqlite3"
    for key in (
        "PHOENIX_FRESH_INSTALL_EMPTY", "PHOENIX_DEFAULT_ADMIN_KEEP_PASSWORD",
        "PHOENIX_DB_PATH", "PHOENIX_UPLOAD_DIR", "PHOENIX_EXPORT_DIR",
        "PHOENIX_BACKUP_DIR", "PHOENIX_LICENSE_PATH", "PHOENIX_LOG_DIR",
    ):
        os.environ.pop(key, None)


def test_runtime_preserves_an_existing_database(tmp_path, monkeypatch):
    data_root = tmp_path / "existing-user-data"
    database_path = data_root / "data" / "pos_ai.sqlite3"
    database_path.parent.mkdir(parents=True)
    database_path.write_bytes(b"existing")
    monkeypatch.setattr(desktop, "_desktop_data_root", lambda: data_root)
    monkeypatch.setattr(desktop, "_migrate_legacy_data", lambda root: False)
    monkeypatch.delenv("PHOENIX_FRESH_INSTALL_EMPTY", raising=False)
    monkeypatch.delenv("PHOENIX_DEFAULT_ADMIN_KEEP_PASSWORD", raising=False)
    for key in (
        "PHOENIX_DB_PATH", "PHOENIX_UPLOAD_DIR", "PHOENIX_EXPORT_DIR",
        "PHOENIX_BACKUP_DIR", "PHOENIX_LICENSE_PATH", "PHOENIX_LOG_DIR",
    ):
        monkeypatch.delenv(key, raising=False)

    desktop.configure_runtime_environment()

    assert "PHOENIX_FRESH_INSTALL_EMPTY" not in os.environ
    assert "PHOENIX_DEFAULT_ADMIN_KEEP_PASSWORD" not in os.environ
    assert database_path.read_bytes() == b"existing"


class FakeServer:
    daemon_threads = False

    def __init__(self, address, handler):
        self.server_address = (address[0], 41821)
        self.started = threading.Event()
        self.stop_requested = threading.Event()
        self.stopped = False
        self.closed = False

    def serve_forever(self):
        self.started.set()
        self.stop_requested.wait(timeout=5)

    def shutdown(self):
        self.stopped = True
        self.stop_requested.set()

    def server_close(self):
        self.closed = True


class FakeWebview:
    def __init__(self):
        self.window = None
        self.start_options = None

    def create_window(self, title, url, **options):
        self.window = (title, url, options)

    def start(self, **options):
        self.start_options = options


def test_create_server_accepts_ephemeral_port():
    server = desktop.create_server("127.0.0.1", 0)
    try:
        assert server.server_address[0] == "127.0.0.1"
        assert server.server_address[1] > 0
        assert server.daemon_threads is True
    finally:
        server.server_close()


def test_desktop_window_uses_local_server_and_stops(monkeypatch):
    fake_server = FakeServer(("127.0.0.1", 0), None)
    monkeypatch.setattr(desktop, "create_server", lambda host, port: fake_server)
    webview = FakeWebview()

    desktop.run_desktop(webview)

    title, url, options = webview.window
    assert title.startswith("PhoEniX BPU")
    assert url == "http://127.0.0.1:41821"
    assert options["min_size"] == (1024, 700)
    assert webview.start_options == {"gui": "edgechromium", "debug": False}
    assert fake_server.stopped is True
    assert fake_server.closed is True


@pytest.mark.skipif(os.name != "nt", reason="Windows named mutex")
def test_single_instance_lock_rejects_second_instance():
    first = desktop.SingleInstanceLock("Local\\SAPTA.PhoEniX_BPU.Test")
    second = desktop.SingleInstanceLock("Local\\SAPTA.PhoEniX_BPU.Test")
    first.acquire()
    try:
        with pytest.raises(desktop.AlreadyRunningError):
            second.acquire()
    finally:
        second.release()
        first.release()


def test_second_launch_focuses_existing_window(monkeypatch):
    class BusyLock:
        def acquire(self):
            raise desktop.AlreadyRunningError("already open")

        def release(self):
            pass

    focused = []
    monkeypatch.setattr(desktop, "SingleInstanceLock", BusyLock)
    monkeypatch.setattr(desktop, "focus_existing_window", lambda: focused.append(True) or True)
    monkeypatch.setattr(desktop, "show_info", lambda message: pytest.fail(str(message)))

    assert desktop.main() == 0
    assert focused == [True]
