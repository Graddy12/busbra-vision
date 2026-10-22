import argparse
import os
import socket
import sys
import threading
import webbrowser

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
    parser.add_argument("--open-browser", action="store_true")
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("Le port doit etre compris entre 1 et 65535.")
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

    print(f"Espace d'analyse (serveur ou local) : {url}workspace/", flush=True)
    app.run(host=args.host, port=args.port, debug=False, use_reloader=False)
