import importlib.util
import threading
import urllib.request
from pathlib import Path


def _load_splash_server_module():
    repo_root = Path(__file__).resolve().parents[1]
    module_path = repo_root / "scripts/startup_splash_server.py"
    spec = importlib.util.spec_from_file_location("startup_splash_server", module_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_splash_server_serves_assets_and_records_dismissal(tmp_path):
    module = _load_splash_server_module()
    splash_root = tmp_path / "splash"
    splash_root.mkdir()
    (splash_root / "startup-splash.html").write_text("OpenFlight", encoding="utf-8")
    dismiss_file = tmp_path / "dismissed"
    server = module.create_server(splash_root, dismiss_file, host="127.0.0.1", port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    try:
        port = server.server_address[1]
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/startup-splash.html") as response:
            assert response.read() == b"OpenFlight"

        request = urllib.request.Request(
            f"http://127.0.0.1:{port}/dismiss", data=b"", method="POST"
        )
        with urllib.request.urlopen(request) as response:
            assert response.status == 204
        assert dismiss_file.read_text(encoding="utf-8") == "dismissed\n"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
