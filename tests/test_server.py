import json
import threading
import urllib.error
import urllib.request
from collections.abc import Iterator

import pytest

from laser_cutterd import __version__
from laser_cutterd.server import make_server


@pytest.fixture
def base_url() -> Iterator[str]:
    server = make_server("127.0.0.1", 0, lambda: {"online": True, "state": "idle"})
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()
    server.server_close()
    thread.join()


def get(url: str) -> tuple[int, dict[str, object]]:
    try:
        with urllib.request.urlopen(url, timeout=5) as response:  # noqa: S310
            return response.status, json.load(response)
    except urllib.error.HTTPError as e:
        return e.code, json.load(e)


def test_healthz_reports_version(base_url: str) -> None:
    assert get(f"{base_url}/healthz") == (200, {"status": "ok", "version": __version__})


def test_status_returns_snapshot(base_url: str) -> None:
    assert get(f"{base_url}/status") == (200, {"online": True, "state": "idle"})


def test_index_lists_endpoints(base_url: str) -> None:
    status, body = get(f"{base_url}/")
    assert status == 200
    assert body["version"] == __version__
    assert "/status" in body


def test_unknown_path_is_404(base_url: str) -> None:
    assert get(f"{base_url}/nope") == (404, {"error": "not found"})
