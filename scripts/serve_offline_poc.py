"""Serve the isolated public-demo PWA on loopback only; no Flask or patient data."""
import argparse
import functools
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "offline_poc"


class Handler(SimpleHTTPRequestHandler):
    extensions_map = {**SimpleHTTPRequestHandler.extensions_map,
                      ".wasm": "application/wasm", ".mjs": "text/javascript", ".js": "text/javascript"}

    def end_headers(self):
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self' 'wasm-unsafe-eval'; "
                         "worker-src 'self' blob:; style-src 'self'; img-src 'self' blob: data:; "
                         "connect-src 'self'; object-src 'none'; base-uri 'self'; frame-ancestors 'none'")
        super().end_headers()

    def list_directory(self, path):
        self.send_error(403, "Directory listing disabled")
        return None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=5091)
    args = parser.parse_args()
    if not (ROOT / "assets-manifest.json").is_file():
        parser.error("Exécuter d'abord scripts/export_offline_models.py.")
    handler = functools.partial(Handler, directory=str(ROOT))
    with ThreadingHTTPServer(("127.0.0.1", args.port), handler) as server:
        print(f"Prototype technique local : http://127.0.0.1:{args.port}/", flush=True)
        print("Aucun compte ni dossier patient n'est exposé. Ctrl+C pour arrêter.", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
