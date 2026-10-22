"""Read-only static delivery of the public, isolated offline-inference laboratory."""
import argparse
import functools
import json
import os
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent
PUBLIC = {
    "/", "/index.html", "/styles.css", "/app.js", "/preprocess.js", "/inference-worker.js", "/sw.js",
    "/manifest.webmanifest", "/icon.svg", "/assets-manifest.json",
    "/ONNX_RUNTIME_LICENSE.txt",
    "/vendor/ort.wasm.min.js", "/vendor/ort-wasm-simd-threaded.mjs", "/vendor/ort-wasm-simd-threaded.wasm",
    "/models/classification.onnx", "/models/segmentation.onnx",
}


class Handler(SimpleHTTPRequestHandler):
    extensions_map = {**SimpleHTTPRequestHandler.extensions_map, ".wasm": "application/wasm",
                      ".mjs": "text/javascript", ".js": "text/javascript", ".onnx": "application/octet-stream",
                      ".webmanifest": "application/manifest+json"}

    def do_GET(self):
        if urlsplit(self.path).path == "/health":
            data = json.dumps({"status": "ok", "version": "offline-poc-fp32-v1", "mode": "static-delivery-only"}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        elif urlsplit(self.path).path in PUBLIC:
            super().do_GET()
        else:
            self.send_error(404)

    def do_HEAD(self):
        if urlsplit(self.path).path in PUBLIC:
            super().do_HEAD()
        else:
            self.send_error(404)

    def do_POST(self):
        self.send_error(405, "No uploads or server-side analysis in this laboratory")

    def end_headers(self):
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self' 'wasm-unsafe-eval'; "
                         "worker-src 'self' blob:; style-src 'self'; img-src 'self' blob: data:; "
                         "connect-src 'self'; object-src 'none'; base-uri 'self'; frame-ancestors 'none'")
        super().end_headers()

    def list_directory(self, path):
        self.send_error(403)
        return None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", "5091")))
    args = parser.parse_args()
    missing = [name for name in PUBLIC if name != "/" and not (ROOT / name.lstrip("/")).is_file()]
    if missing:
        parser.error("Fichiers nécessaires manquants : " + ", ".join(sorted(missing)))
    with ThreadingHTTPServer((args.host, args.port), functools.partial(Handler, directory=str(ROOT))) as server:
        print(f"Laboratoire isolé : http://{args.host}:{args.port}", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
