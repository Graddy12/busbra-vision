"""Read-only integrity checks for the thesis illustration export."""
from __future__ import annotations

import hashlib
import json
import zipfile
from collections import Counter
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urlsplit

from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "elements_memoire"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class Links(HTMLParser):
    def __init__(self):
        super().__init__()
        self.targets = []

    def handle_starttag(self, tag, attrs):
        self.targets.extend(value for key, value in attrs if key in {"href", "src"} and value)


def main():
    records = json.loads((OUTPUT / "inventaire.json").read_text(encoding="utf-8"))
    control = json.loads((OUTPUT / "controle_export.json").read_text(encoding="utf-8"))
    for item in records:
        path = OUTPUT / item["fichier"]
        assert path.is_file(), path
        assert sha256(path) == item["sha256"], path
        if path.suffix == ".png":
            with Image.open(path) as image:
                assert image.size == (item["largeur"], item["hauteur"]), path
                image.verify()
        if item["categorie"] in {"notebook", "graphique_original", "resultat_original", "script", "inference_original"}:
            assert sha256(ROOT / item["source"]) == sha256(path), path
    for project, stats in control["notebooks"].items():
        notebook = json.loads((ROOT / stats["notebook"]).read_text(encoding="utf-8"))
        code = [(i, cell) for i, cell in enumerate(notebook["cells"], 1) if cell["cell_type"] == "code"]
        assert len(code) == stats["blocs_code"]
        for number, cell in code:
            selected = [item for item in records if item["projet"] == project
                        and item.get("cellule") == number and item["categorie"] == "source_cellule"]
            assert len(selected) == 1, (project, number)
            text = "".join(cell["source"]) if isinstance(cell["source"], list) else cell["source"]
            assert (OUTPUT / selected[0]["fichier"]).read_text(encoding="utf-8") == text, (project, number)
            images = [item for item in records if item["projet"] == project
                      and item.get("cellule") == number and item["categorie"] == "code_notebook"]
            assert images, (project, number)
            assert sorted(item["partie"] for item in images) == list(range(1, len(images) + 1))
        assert stats["sorties_enregistrees"] == sum(len(c.get("outputs", [])) for _, c in code) == 0
    for path in OUTPUT.rglob("*.html"):
        parser = Links()
        parser.feed(path.read_text(encoding="utf-8"))
        for target in parser.targets:
            parts = urlsplit(target)
            if not parts.scheme and parts.path:
                assert (path.parent / unquote(parts.path)).is_file(), (path, target)
    all_files = [path for path in OUTPUT.rglob("*") if path.is_file()]
    forbidden = {"instance", "logs", "uploads", ".git", ".venv"}
    assert not any(forbidden.intersection(path.relative_to(OUTPUT).parts) for path in all_files)
    assert not any(path.suffix in {".pt", ".pth", ".keras"} or path.name.startswith(".env") for path in all_files)
    with zipfile.ZipFile(OUTPUT.with_suffix(".zip")) as archive:
        assert archive.testzip() is None
        assert len(archive.namelist()) == len(all_files)
        for path in all_files:
            name = OUTPUT.name + "/" + path.relative_to(OUTPUT).as_posix()
            assert hashlib.sha256(archive.read(name)).hexdigest() == sha256(path), path
    summary = dict(
        status="OK", fichiers=len(all_files), images_png=sum(p.suffix == ".png" for p in all_files),
        blocs_code=sum(s["blocs_code"] for s in control["notebooks"].values()),
        pages_code=control["pages_code"], controle_copies_et_codes="identiques aux sources",
        liens_html="valides", archive_zip="CRC et contenu vérifiés",
        repartition_png=dict(Counter(r["projet"] for r in records if r["fichier"].endswith(".png"))),
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
