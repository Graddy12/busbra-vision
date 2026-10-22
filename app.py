import argparse
import functools
import os
import socket
import sys
import threading
import webbrowser
from http.server import ThreadingHTTPServer

# Must be set before Keras is imported anywhere in the application.
os.environ["KERAS_BACKEND"] = "torch"

from cancer_app import create_app
from cancer_app.inference import ModelLoadError


app = create_app()


def check_port(host: str, port: int) -> None:
    """Refuse an already bound port, including Windows reusable sockets."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        probe.bind((host, port))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="BUSBRA Vision")
    parser.add_argument("--host", default=os.getenv("FLASK_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=os.getenv("FLASK_PORT", "5001"))
    parser.add_argument("--pwa-port", type=int, default=5091)
    parser.add_argument("--no-pwa", action="store_true", help="Ne pas démarrer le serveur local de la PWA.")
    parser.add_argument("--open-browser", action="store_true")
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("Le port doit etre compris entre 1 et 65535.")
    if not 1 <= args.pwa_port <= 65535:
        parser.error("Le port PWA doit etre compris entre 1 et 65535.")
    if not args.no_pwa and args.port == args.pwa_port:
        parser.error("Le serveur web et la PWA doivent utiliser des ports différents.")
    if not app.config["PRODUCTION"]:
        app.config["OFFLINE_PWA_URL"] = (
            None if args.no_pwa else f"http://127.0.0.1:{args.pwa_port}/"
        )
    try:
        check_port(args.host, args.port)
    except OSError as exc:
        print(
            f"Le port {args.port} est deja occupe ou indisponible ({exc}).\n"
            "Fermez l'ancien serveur avec Ctrl+C, ou choisissez un autre port :\n"
            f"  python app.py --port {args.port + 1 if args.port < 65535 else 5001}",
            file=sys.stderr,
            flush=True,
        )
        sys.exit(1)

    print(f"Python utilise : {sys.executable}", flush=True)
    print("Chargement des deux modeles, patientez...", flush=True)
    try:
        app.extensions["combined_predictor"].load()
    except ModelLoadError as exc:
        print(f"\nImpossible de demarrer : {exc}", file=sys.stderr, flush=True)
        print(
            r"Verifiez les dependances avec : .\.venv\Scripts\python.exe -m pip install -r requirements.txt",
            file=sys.stderr,
            flush=True,
        )
        sys.exit(1)

    # Another instance may have started while the models were loading.
    try:
        check_port(args.host, args.port)
    except OSError:
        print(f"Le port {args.port} vient d'etre occupe. Fermez l'autre instance.", file=sys.stderr)
        sys.exit(1)

    url = f"http://{args.host}:{args.port}/"
    print(f"Les deux modeles sont charges. Ouvrez : {url}", flush=True)
    if args.open_browser:
        open_browser = threading.Timer(1, lambda: webbrowser.open(url, new=2))
        open_browser.daemon = True
        open_browser.start()

    pwa_server = None
    if not args.no_pwa:
        try:
            from offline_poc.server import Handler as OfflinePwaHandler, ROOT as OFFLINE_PWA_ROOT

            pwa_server = ThreadingHTTPServer(
                ("127.0.0.1", args.pwa_port),
                functools.partial(OfflinePwaHandler, directory=str(OFFLINE_PWA_ROOT)),
            )
        except OSError as exc:
            print(
                f"Impossible de démarrer la PWA sur 127.0.0.1:{args.pwa_port} ({exc}). "
                "Fermez l'ancien serveur ou choisissez un autre port avec --pwa-port.",
                file=sys.stderr,
                flush=True,
            )
            sys.exit(1)
        pwa_thread = threading.Thread(target=pwa_server.serve_forever, daemon=True)
        pwa_thread.start()
        print(f"PWA hors ligne disponible : http://127.0.0.1:{args.pwa_port}/", flush=True)

    try:
        app.run(
            host=args.host,
            port=args.port,
            debug=False,
            use_reloader=False,
        )
    finally:
        if pwa_server is not None:
            pwa_server.shutdown()
            pwa_server.server_close()
