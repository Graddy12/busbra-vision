"""Export the supplied research material, without executing notebook cells.

Run from the repository root with the project's virtual-environment Python.
Only the explicitly supplied research notebooks/packages and public demo images
are read; application users, history, uploads, logs and secrets are never read.
"""
from __future__ import annotations

import argparse
import base64
import csv
import hashlib
import html
import importlib.util
import json
import math
import re
import shutil
import textwrap
import unicodedata
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from PIL import Image, ImageDraw, ImageFont
from pygments import highlight, lex
from pygments.formatters import HtmlFormatter
from pygments.lexers import JsonLexer, PythonLexer, TextLexer
from pygments.styles import get_style_by_name


ROOT = Path(__file__).resolve().parents[1]
FONT_ROOT = Path("C:/Windows/Fonts")
PLOT_COLORS = ["#156078", "#bb5638", "#5e7b42", "#7955a0"]
NOTEBOOKS = {
    "classification": "BUSI_Classification_EfficientNetB0_Augmentation_Colab.ipynb",
    "segmentation": "BUSBRA_segmentation_patients.ipynb",
}
ORIGINAL_FIGURES = {
    "courbes_entrainement.png": (
        "Courbes d'apprentissage originales",
        "Évolution des performances pendant les deux phases d'entraînement d'EfficientNetB0 sur BUSI.",
    ),
    "distribution_splits.png": (
        "Distribution des classes et des partitions",
        "Répartition des images BUSI entre apprentissage, validation et test, d'après l'export original.",
    ),
    "matrice_confusion_test.png": (
        "Matrice de confusion du test BUSI",
        "Comparaison des classes de référence et des classes prédites sur le jeu de test exporté.",
    ),
    "roc_test.png": (
        "Courbes ROC du test BUSI",
        "Courbes ROC multiclasse calculées lors de l'évaluation de classification exportée.",
    ),
    "exemples_predictions_test.png": (
        "Exemples de prédictions de classification",
        "Galerie d'inférences du jeu de test, fournie dans le paquet de résultats de classification.",
    ),
    "galerie_augmentations.png": (
        "Augmentations des images d'apprentissage",
        "Exemples des transformations réservées aux images d'apprentissage BUSI.",
    ),
    "gradcam_exemples.png": (
        "Explications visuelles Grad-CAM",
        "Cartes d'activation fournies par le notebook de classification ; elles ne sont pas des masques de segmentation.",
    ),
}


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def write_json(path: Path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def digest(path: Path) -> str:
    result = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            result.update(chunk)
    return result.hexdigest()


def slug(value: str, limit=65) -> str:
    value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")[:limit].strip("_") or "cellule"


def cell_text(cell: dict) -> str:
    source = cell.get("source", "")
    return "".join(source) if isinstance(source, list) else source


class Export:
    def __init__(self, output: Path):
        self.output = output
        if output.exists():
            raise SystemExit(f"Le dossier existe déjà, rien n'a été écrasé : {output}")
        output.mkdir(parents=True)
        self.records: list[dict] = []
        self.notebooks: dict[str, dict] = {}
        self.code_pages: list[dict] = []
        self.style = get_style_by_name("default")
        self.code_font = ImageFont.truetype(str(FONT_ROOT / "consola.ttf"), 28)
        self.small_font = ImageFont.truetype(str(FONT_ROOT / "segoeui.ttf"), 21)
        self.title_font = ImageFont.truetype(str(FONT_ROOT / "segoeuib.ttf"), 27)
        self.mono_small = ImageFont.truetype(str(FONT_ROOT / "consola.ttf"), 21)
        self.columns = 84
        self.max_rows = 44
        self.line_height = 40
        self.width = math.ceil(self.code_font.getlength("M") * self.columns) + 160

    def record(self, path: Path, project: str, category: str, title: str,
               caption: str = "", source: str = "", **extra):
        item = dict(
            fichier=path.relative_to(self.output).as_posix(),
            projet=project, categorie=category, titre=title,
            legende=caption, source=source,
            sha256=digest(path), octets=path.stat().st_size, **extra,
        )
        if path.suffix.lower() == ".png":
            with Image.open(path) as image:
                item.update(largeur=image.width, hauteur=image.height)
        self.records.append(item)
        return item

    def copy(self, source: Path, target: Path, project: str, category: str,
             title: str, caption=""):
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        assert digest(source) == digest(target), source
        return self.record(target, project, category, title, caption,
                           source.relative_to(ROOT).as_posix())

    def render_text(self, text: str, target_dir: Path, stem: str, project: str,
                    title: str, source: str, category: str, lexer=None,
                    cell_number=None):
        """Wrap for display only; keep every source character in the .py/.txt copy."""
        target_dir.mkdir(parents=True, exist_ok=True)
        logical_lines: list[list[tuple[str, str]]] = [[]]
        lexer = lexer or TextLexer(stripnl=False, ensurenl=False)
        for token, value in lex(text, lexer):
            color = self.style.style_for_token(token)["color"] or "202a35"
            for char in value:
                if char == "\n":
                    logical_lines.append([])
                elif char == "\t":
                    spaces = 4 - len(logical_lines[-1]) % 4
                    logical_lines[-1].extend([(" ", color)] * spaces)
                elif char != "\r":
                    logical_lines[-1].append((char, color))
        if text.endswith("\n") and logical_lines[-1] == []:
            logical_lines.pop()
        rows = []
        for number, chars in enumerate(logical_lines, 1):
            for offset in range(0, max(1, len(chars)), self.columns):
                rows.append((number, offset > 0, chars[offset:offset + self.columns]))
        pages = max(1, math.ceil(len(rows) / self.max_rows))
        paths = []
        for page in range(pages):
            portion = rows[page * self.max_rows:(page + 1) * self.max_rows]
            header_height = 172
            height = header_height + self.line_height * len(portion) + 65
            image = Image.new("RGB", (self.width, height), "white")
            draw = ImageDraw.Draw(image)
            draw.rectangle((0, 0, self.width, 9), fill="#156078")
            draw.text((30, 26), f"{project.upper()}  |  {title}",
                      font=self.title_font, fill="#17364a")
            # A long section name is not placed in this header; full captions live in the index.
            source_label = source if len(source) < 94 else "…" + source[-93:]
            draw.text((30, 76), source_label, font=self.small_font, fill="#465462")
            label = f"Partie {page + 1}/{pages} — lignes {portion[0][0] if portion else 1} à {portion[-1][0] if portion else 1}"
            draw.text((30, 115), label, font=self.small_font, fill="#465462")
            draw.rectangle((0, header_height, 101, height - 46), fill="#f2f5f7")
            for row_index, (line_number, continuation, chars) in enumerate(portion):
                y = header_height + row_index * self.line_height
                line_label = ">" if continuation else str(line_number)
                draw.text((88, y), line_label, anchor="ra", font=self.mono_small, fill="#677580")
                x = 122
                for char, color in chars:
                    draw.text((x, y), char, font=self.code_font, fill="#" + color)
                    x += self.code_font.getlength("M")
                assert x <= self.width - 25, (title, x, self.width)
            draw.text((30, height - 38),
                      "Retour visuel à la ligne : >   |   Source complète conservée dans le fichier texte/code.",
                      font=self.small_font, fill="#677580")
            path = target_dir / f"{stem}_p{page + 1:02d}.png"
            image.save(path, dpi=(300, 300))
            caption = f"{title}, partie {page + 1}/{pages}. Export du contenu source, sans exécution du notebook."
            self.record(path, project, category, title, caption, source,
                        cellule=cell_number, partie=page + 1, parties=pages)
            if category == "code_notebook":
                self.code_pages.append(dict(projet=project, cellule=cell_number,
                                            partie=page + 1, lignes=[r[0] for r in portion]))
            paths.append(path)
        return paths

    def export_notebook(self, project: str):
        source = ROOT / NOTEBOOKS[project]
        nb = read_json(source)
        folder = self.output / project
        for name in ("notebook", "codes", "graphiques", "sorties_notebook", "resultats", "scripts_inference"):
            (folder / name).mkdir(parents=True, exist_ok=True)
        self.copy(source, folder / "notebook" / source.name, project, "notebook",
                  "Notebook original", "Copie exacte du notebook fourni, non réexécuté.")
        number = 0
        output_count = 0
        rows = []
        section = "Introduction"
        assembled = [f"# Export de {source.name}\n# Cellules originales ; certaines nécessitent Jupyter/Colab.\n"]
        body = []
        try:
            import mistune
            markdown = mistune.create_markdown(escape=True)
        except ImportError:
            markdown = lambda text: "<pre>" + html.escape(text) + "</pre>"
        for cell_number, cell in enumerate(nb["cells"], 1):
            text = cell_text(cell)
            if cell["cell_type"] == "markdown":
                headings = [line.lstrip("# ").strip() for line in text.splitlines() if line.startswith("#")]
                if headings:
                    section = headings[0]
                body.append(f'<section class="markdown"><small>Cellule {cell_number}</small>{markdown(text)}</section>')
                continue
            if cell["cell_type"] != "code":
                body.append(f'<pre>{html.escape(text)}</pre>')
                continue
            number += 1
            stem = f"bloc_{number:02d}_cellule_{cell_number:02d}_{slug(section, 48)}"
            code_path = folder / "codes" / f"{stem}.py"
            code_path.write_text(text, encoding="utf-8")
            source_ref = f"{source.name} — cellule {cell_number}"
            title = f"Bloc {number:02d} · cellule {cell_number:02d}"
            self.record(code_path, project, "source_cellule", section,
                        f"Code intégral du bloc {number}, cellule {cell_number} ; non exécuté.",
                        source_ref, cellule=cell_number, bloc=number)
            images = self.render_text(text, folder / "codes", stem, project, title, source_ref,
                                      "code_notebook", PythonLexer(stripnl=False, ensurenl=False), cell_number)
            outputs = cell.get("outputs", [])
            output_count += len(outputs)
            rows.append(dict(bloc=number, cellule=cell_number, section=section,
                             lignes=len(text.splitlines()), images=len(images),
                             sorties_enregistrees=len(outputs), source=code_path.relative_to(self.output).as_posix()))
            assembled.append(f"\n# %% Bloc {number:02d} | Cellule {cell_number} | {section}\n{text}\n")
            body.append(f'<section><h2>{html.escape(title)} — {html.escape(section)}</h2>'
                        + highlight(text, PythonLexer(), HtmlFormatter())
                        + f'<p>Sorties enregistrées : {len(outputs)}. '
                          'Les graphiques disponibles séparément sont répertoriés dans le catalogue.</p></section>')
            self.export_outputs(outputs, project, cell_number)
        combined = folder / "codes" / "notebook_code_complet.py"
        combined.write_text("".join(assembled), encoding="utf-8")
        self.record(combined, project, "source_complete", "Toutes les cellules de code",
                    "Export de lecture ; dépend de l'environnement Jupyter/Colab du notebook original.", source.name)
        stats = dict(notebook=source.name, cellules=len(nb["cells"]), blocs_code=number,
                     sorties_enregistrees=output_count, cellules_code=rows)
        self.notebooks[project] = stats
        write_json(folder / "notebook" / "inventaire_cellules.json", stats)
        html_path = folder / "notebook" / "lecture_notebook.html"
        html_path.write_text(page_html(f"Notebook — {project}",
            '<p><a href="../../index.html">← Catalogue du mémoire</a></p>'
            '<p>Version de lecture hors ligne. Aucune cellule n’a été exécutée par cet export.</p>' + "".join(body)), encoding="utf-8")
        self.record(html_path, project, "lecture", "Notebook lisible hors ligne", source=source.name)
        note = (f"# Sorties du notebook — {project}\n\n"
                f"Le notebook fourni contient {number} cellules de code et {output_count} sorties enregistrées.\n\n"
                "Aucun entraînement ni aucune cellule n'a été relancé pour cet export. "
                "Les images de code sont des rendus du texte source, pas des preuves d'exécution. "
                "Les graphiques réels exportés séparément et les illustrations reconstruites à partir de données "
                "réelles se trouvent dans `../graphiques/`.\n")
        (folder / "sorties_notebook" / "LIRE_MOI.md").write_text(note, encoding="utf-8")
        print(f"{project}: {number} blocs de code exportés, {output_count} sorties sauvegardées.", flush=True)

    def export_outputs(self, outputs: list, project: str, cell: int):
        folder = self.output / project / "sorties_notebook"
        for index, output in enumerate(outputs, 1):
            stem = f"cellule_{cell:02d}_sortie_{index:02d}"
            data = output.get("data", {})
            if "image/png" in data:
                value = data["image/png"]
                path = folder / (stem + ".png")
                path.write_bytes(base64.b64decode("".join(value) if isinstance(value, list) else value))
                self.record(path, project, "sortie_originale", f"Sortie {index}, cellule {cell}",
                            "Image enregistrée dans le notebook original.", NOTEBOOKS[project], cellule=cell)
            elif "text" in output or "text/plain" in data or "traceback" in output:
                value = output.get("text", data.get("text/plain", output.get("traceback", [])))
                text = "".join(value) if isinstance(value, list) else value
                text = re.sub(r"\x1b\[[0-9;]*m", "", text)
                path = folder / (stem + ".txt")
                path.write_text(text, encoding="utf-8")
                self.record(path, project, "sortie_texte", stem, source=NOTEBOOKS[project])
                self.render_text(text, folder, stem, project, f"Sortie de la cellule {cell}",
                                 NOTEBOOKS[project], "sortie_texte_image", cell_number=cell)

    def supplied_results(self):
        for project in NOTEBOOKS:
            package = ROOT / f"{project}_package"
            for path in sorted(package.iterdir()):
                if not path.is_file() or path.suffix.lower() in {".keras", ".pt", ".pth"}:
                    continue
                if path.suffix.lower() == ".png":
                    title, caption = ORIGINAL_FIGURES.get(path.name, (path.stem, "Image originale du paquet fourni."))
                    self.copy(path, self.output / project / "graphiques" / path.name,
                              project, "graphique_original", title, caption)
                elif path.suffix.lower() == ".py":
                    target = self.output / project / "scripts_inference" / path.name
                    self.copy(path, target, project, "script", "Script d'inférence original")
                    self.render_text(path.read_text(encoding="utf-8"), target.parent, path.stem,
                                     project, "Script d'inférence", path.relative_to(ROOT).as_posix(),
                                     "code_script", PythonLexer(stripnl=False, ensurenl=False))
                else:
                    self.copy(path, self.output / project / "resultats" / path.name,
                              project, "resultat_original", path.name)
            for filename in (["metriques_test.json", "rapport_par_classe.txt", "config_modele.json"]
                             if project == "classification" else ["configuration.json", "selection.json"]):
                path = package / filename
                self.render_text(path.read_text(encoding="utf-8"), self.output / project / "resultats",
                                 path.stem, project, "Résultats / configuration", path.relative_to(ROOT).as_posix(),
                                 "resultat_image", JsonLexer(stripnl=False, ensurenl=False) if path.suffix == ".json" else None)

    def figure(self, fig, project: str, stem: str, title: str, caption: str, source: str):
        folder = self.output / project / "graphiques"
        fig.suptitle(title, fontsize=14, fontweight="bold", color="#17364a")
        for axis in fig.axes:
            if axis.axison:
                axis.spines[["top", "right"]].set_visible(False)
        for suffix in ("png", "svg"):
            path = folder / f"{stem}.{suffix}"
            fig.savefig(path, dpi=300, bbox_inches="tight", facecolor="white")
            self.record(path, project, "graphique_reconstruit" if suffix == "png" else "graphique_vectoriel",
                        title, caption, source)
        plt.close(fig)

    def classification_plots(self):
        package = ROOT / "classification_package"
        h1 = pd.read_csv(package / "history_efficientnet_phase1.csv")
        h2 = pd.read_csv(package / "history_efficientnet_phase2.csv")
        frame = pd.concat([h1, h2], ignore_index=True)
        epochs = np.arange(1, len(frame) + 1)
        source = "classification_package/history_efficientnet_phase1.csv + history_efficientnet_phase2.csv"
        for metric, val_metric, ylabel, stem, title in (
            ("accuracy", "val_accuracy", "Exactitude", "apprentissage_exactitude_300dpi", "Classification BUSI — exactitude"),
            ("loss", "val_loss", "Perte", "apprentissage_perte_300dpi", "Classification BUSI — perte"),
        ):
            fig, ax = plt.subplots(figsize=(9, 4.8), layout="constrained")
            ax.plot(epochs, frame[metric], color=PLOT_COLORS[0], marker="o", ms=3, label="Apprentissage")
            ax.plot(epochs, frame[val_metric], color=PLOT_COLORS[1], marker="o", ms=3, label="Validation")
            ax.axvline(len(h1) + .5, color="#71808b", linestyle="--", label="Début du fine-tuning")
            ax.set(xlabel="Époque cumulée (phases 1 et 2)", ylabel=ylabel, xticks=epochs)
            ax.grid(alpha=.18)
            ax.legend()
            if metric == "accuracy":
                ax.set_ylim(0, 1)
            self.figure(fig, "classification", stem, title,
                        "Courbe redessinée sans lissage à partir des journaux CSV originaux ; séparation des deux phases indiquée.", source)
        frame = pd.read_csv(package / "predictions_test.csv")
        labels = ["normal", "benign", "malignant"]
        matrix = np.array([[int(((frame.label == actual) & (frame.prediction == pred)).sum())
                            for pred in labels] for actual in labels])
        assert int(matrix.sum()) == len(frame) == 112
        metrics = read_json(package / "metriques_test.json")
        assert abs(float(np.trace(matrix) / matrix.sum()) - metrics["accuracy_test"]) < 1e-9
        fig, ax = plt.subplots(figsize=(6.5, 5.4), layout="constrained")
        ax.imshow(matrix, cmap="Blues", vmin=0)
        ax.set(xticks=range(3), yticks=range(3), xticklabels=["Normale", "Bénigne", "Maligne"],
               yticklabels=["Normale", "Bénigne", "Maligne"], xlabel="Classe prédite", ylabel="Classe de référence")
        for i in range(3):
            for j in range(3):
                ax.text(j, i, str(matrix[i, j]), ha="center", va="center", fontsize=20,
                        color="white" if matrix[i, j] > matrix.max() / 2 else "#17364a")
        self.figure(fig, "classification", "matrice_confusion_fr_300dpi", "Classification BUSI — test (112 images)",
                    "Matrice recalculée à partir des 112 prédictions exportées, avec des libellés français ; exactitude cohérente avec metriques_test.json.",
                    "classification_package/predictions_test.csv")

    def segmentation_results(self, include_inference: bool):
        print("Lecture de l'historique réel du checkpoint de segmentation…", flush=True)
        import torch

        torch.set_num_threads(4)
        # This is the user's trusted training checkpoint already used by the application.
        checkpoint_path = ROOT / "segmentation_package" / "best_model.pt"
        checkpoint = torch.load(checkpoint_path, map_location="cpu", weights_only=False)
        keep = ["format_version", "spec", "run_config", "global_epoch", "phase_index", "phase_epoch",
                "next_phase", "next_phase_epoch", "phase_best", "bad_epochs", "best_score", "selection_metric",
                "validation", "finished"]
        metadata = {key: checkpoint[key] for key in keep if key in checkpoint}
        metadata["source_checkpoint_sha256"] = digest(checkpoint_path)
        metadata["note"] = (
            "Historique disponible jusqu'au checkpoint best_model.pt uniquement, pas une preuve de fin de l'entraînement. "
            "Les scores 'validation' du checkpoint et de selection.json ne sont pas des scores de test."
        )
        results = self.output / "segmentation" / "resultats"
        path = results / "checkpoint_metadonnees.json"
        write_json(path, metadata)
        self.record(path, "segmentation", "donnees_checkpoint", "Métadonnées réelles du checkpoint",
                    metadata["note"], "segmentation_package/best_model.pt")
        history = checkpoint.get("history", [])
        write_json(results / "historique_checkpoint.json", history)
        self.record(results / "historique_checkpoint.json", "segmentation", "donnees_checkpoint",
                    "Historique réel du checkpoint", source="segmentation_package/best_model.pt['history']")
        if history:
            frame = pd.DataFrame(history)
            frame.to_csv(results / "historique_checkpoint.csv", index=False, encoding="utf-8")
            self.record(results / "historique_checkpoint.csv", "segmentation", "donnees_checkpoint",
                        "Historique réel d'apprentissage", source="segmentation_package/best_model.pt['history']")
            specs = [
                (["train_loss"], ["Perte d'apprentissage"], "Perte BCE + Dice", "apprentissage_perte_300dpi", "Segmentation BUS-BRA — perte d'apprentissage"),
                (["val_patient_dice", "val_image_dice"], ["Dice macro-patient", "Dice macro-image"], "Dice de validation", "validation_dice_300dpi", "Segmentation BUS-BRA — Dice de validation"),
                (["val_image_iou"], ["IoU macro-image"], "IoU de validation", "validation_iou_300dpi", "Segmentation BUS-BRA — IoU de validation"),
                (["learning_rate"], ["Taux d'apprentissage"], "Taux d'apprentissage (échelle log)", "taux_apprentissage_300dpi", "Segmentation BUS-BRA — taux d'apprentissage"),
            ]
            for columns, legends, ylabel, stem, title in specs:
                fig, ax = plt.subplots(figsize=(9, 4.8), layout="constrained")
                for index, (column, legend) in enumerate(zip(columns, legends)):
                    ax.plot(frame.epoch, frame[column], color=PLOT_COLORS[index], marker="o", ms=3, label=legend)
                for index in range(1, len(frame)):
                    if frame.iloc[index]["phase"] != frame.iloc[index - 1]["phase"]:
                        ax.axvline((frame.iloc[index].epoch + frame.iloc[index - 1].epoch) / 2,
                                   color="#71808b", linestyle="--", label="Changement de phase")
                ax.set(xlabel="Époque enregistrée dans le checkpoint", ylabel=ylabel)
                if columns[0] == "learning_rate":
                    ax.set_yscale("log")
                elif columns[0].startswith("val_"):
                    ax.set_ylim(0, 1)
                ax.grid(alpha=.18)
                ax.legend()
                self.figure(fig, "segmentation", stem, title,
                            f"Reconstruction sans lissage des {len(frame)} époques réellement présentes dans best_model.pt. "
                            "Historique arrêté au checkpoint sauvegardé ; aucune performance de test n'est déduite de ces courbes.",
                            "segmentation_package/best_model.pt['history']")
        selection = read_json(ROOT / "segmentation_package" / "selection.json")
        keys = ["patient_macro_dice", "image_macro_dice", "patient_macro_iou", "image_macro_iou"]
        fig, ax = plt.subplots(figsize=(9, 4.8), layout="constrained")
        values = [selection[k] for k in keys]
        bars = ax.bar(["Dice\nmacro-patient", "Dice\nmacro-image", "IoU\nmacro-patient", "IoU\nmacro-image"],
                      values, color=PLOT_COLORS)
        ax.bar_label(bars, labels=[f"{value:.4f}" for value in values], padding=5)
        ax.set(ylim=(0, 1.07), ylabel="Score de validation (0 à 1)")
        ax.grid(axis="y", alpha=.18)
        self.figure(fig, "segmentation", "selection_validation_300dpi", "Segmentation BUS-BRA — sélection sur validation",
                    "Scores de selection.json après sélection des paramètres sur validation : TTA activée, seuil 0,5, "
                    "plus grande composante conservée. Ce ne sont PAS les résultats du jeu de test.",
                    "segmentation_package/selection.json ; provenance du protocole : dernière cellule du notebook")
        self.notebooks["segmentation"]["epoques_checkpoint"] = len(history)
        self.notebooks["segmentation"]["checkpoint_finished"] = checkpoint.get("finished")
        self.render_text(json.dumps(metadata, ensure_ascii=False, indent=2), results, "checkpoint_metadonnees",
                         "segmentation", "Métadonnées du checkpoint", "segmentation_package/best_model.pt",
                         "resultat_image", JsonLexer(stripnl=False, ensurenl=False))
        if include_inference:
            self.segmentation_inferences(checkpoint, selection, torch)

    def segmentation_inferences(self, checkpoint: dict, selection: dict, torch):
        print("Production de deux illustrations d'inférence réelles (BUSI, démonstration externe)…", flush=True)
        import cv2
        import segmentation_models_pytorch as smp

        module_path = ROOT / "segmentation_package" / "inference_api.py"
        spec = importlib.util.spec_from_file_location("thesis_segmenter", module_path)
        api = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(api)
        model_spec = checkpoint["spec"]
        constructors = {"Unet": smp.Unet, "UnetPlusPlus": smp.UnetPlusPlus}
        model = constructors[model_spec["architecture"]](encoder_name=model_spec["encoder"], encoder_weights=None,
                                                       in_channels=3, classes=1, activation=None)
        model.load_state_dict(checkpoint["model_state"])
        model.eval()
        examples = [ROOT / "cancer_seins" / "benign" / "benign (92).png",
                    ROOT / "cancer_seins" / "malignant" / "malignant (95).png"]
        folder = self.output / "segmentation" / "inferences"
        folder.mkdir(exist_ok=True)
        for index, source in enumerate(examples, 1):
            image = api.read_rgb(source)
            mask, probability = api.predict_image(model, source,
                image_size=int(checkpoint["run_config"]["image_size"]), threshold=float(selection["threshold"]),
                tta=bool(selection["tta"]), device="cpu")
            if selection["largest"] and mask.any():
                count, labels, stats, _ = cv2.connectedComponentsWithStats(mask.astype(np.uint8), connectivity=8)
                if count > 1:
                    mask = labels == 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
            overlay = image.copy()
            overlay[mask] = np.clip(image[mask].astype(float) * .5 + np.array([255, 95, 65]) * .5, 0, 255)
            stem = f"exemple_{index:02d}_{source.parent.name}"
            self.copy(source, folder / f"{stem}_original.png", "segmentation", "inference_original",
                      "Échographie BUSI de démonstration", "Source BUSI, distincte du jeu d'apprentissage BUS-BRA.")
            for suffix, array in [("masque", mask.astype(np.uint8) * 255), ("superposition", overlay)]:
                path = folder / f"{stem}_{suffix}.png"
                Image.fromarray(array).save(path, dpi=(300, 300))
                self.record(path, "segmentation", "inference_detail", f"Inférence réelle — {suffix}",
                            "Exemple qualitatif externe BUSI, sans masque de référence et sans mesure Dice/IoU ; ne constitue pas une évaluation du test BUS-BRA.",
                            source.relative_to(ROOT).as_posix() + " + segmentation_package/best_model.pt")
            np.save(folder / f"{stem}_probabilites.npy", probability, allow_pickle=False)
            self.record(folder / f"{stem}_probabilites.npy", "segmentation", "inference_donnees",
                        "Carte de probabilités brute", source=source.relative_to(ROOT).as_posix())
            fig, axes = plt.subplots(1, 4, figsize=(15, 4.5), layout="constrained")
            axes[0].imshow(image); axes[0].set_title("Image source BUSI")
            axes[1].imshow(probability, cmap="magma", vmin=0, vmax=1); axes[1].set_title("Probabilité (0 à 1)")
            axes[2].imshow(mask, cmap="gray", vmin=0, vmax=1); axes[2].set_title("Masque prédit (seuil 0,5)")
            axes[3].imshow(overlay); axes[3].set_title("Superposition")
            for ax in axes:
                ax.axis("off")
            fig.text(.5, .015, "Démonstration qualitative externe BUSI — modèle entraîné sur BUS-BRA — sans masque de référence / sans score de test",
                     ha="center", fontsize=10, color="#596976")
            self.figure(fig, "segmentation", f"inference_{index:02d}_{source.parent.name}_300dpi",
                        f"Inférence réelle U-Net / ResNet34 — {source.name}",
                        "Prédiction produite localement à partir du checkpoint fourni sur un exemple BUSI bénin ou malin. "
                        "TTA et plus grande composante activées selon selection.json. Illustration qualitative externe, "
                        "sans vérité terrain ; aucun Dice, IoU ou score de test n'est calculé.",
                        source.relative_to(ROOT).as_posix() + " + segmentation_package/best_model.pt")
            write_json(folder / f"{stem}_parametres.json", dict(source=source.relative_to(ROOT).as_posix(),
                image_sha256=digest(source), selection=selection,
                checkpoint_sha256=digest(ROOT / "segmentation_package" / "best_model.pt"),
                note="Démonstration externe BUSI ; classe tirée du dossier source, pas prédite par cette illustration de segmentation."))
            print(f"  Inférence exportée : {source.name}", flush=True)
        del model

    def finish(self, script_path: Path):
        self.copy(script_path, self.output / "reproduire_export.py", "commun", "script_export",
                  "Script ayant produit le dossier", "La copie documentaire suppose le même environnement et les mêmes sources ; lancer de préférence scripts/prepare_thesis_assets.py dans le projet.")
        # Rendering audit: every non-empty source line has to occur on at least one exported page.
        for project in NOTEBOOKS:
            nb = read_json(ROOT / NOTEBOOKS[project])
            for cell_number, cell in enumerate(nb["cells"], 1):
                if cell["cell_type"] != "code":
                    continue
                expected = set(range(1, len(cell_text(cell).splitlines()) + 1))
                rendered = set(line for page in self.code_pages
                               if page["projet"] == project and page["cellule"] == cell_number
                               for line in page["lignes"])
                assert expected <= rendered, (project, cell_number, expected - rendered)
        write_json(self.output / "inventaire.json", self.records)
        columns = ["fichier", "projet", "categorie", "titre", "legende", "source", "sha256", "octets", "largeur", "hauteur", "cellule", "partie", "parties", "bloc"]
        with (self.output / "inventaire.csv").open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
            writer.writeheader(); writer.writerows(self.records)
        write_json(self.output / "controle_export.json", dict(
            cree_utc=datetime.now(timezone.utc).isoformat(), notebooks=self.notebooks,
            pages_code=len(self.code_pages), sorties_inventees=0,
            cellules_executees=0, entrainements_relances=0,
            fichiers_copies_identiques=True, couverture_lignes_code_verifiee=True,
            poids_modeles_inclus=False, donnees_application_incluses=False,
        ))
        captions = ["# Légendes proposées pour le mémoire\n",
                    "Adapter la numérotation à celle du mémoire. Les sources et les limites ci-dessous doivent être conservées.\n"]
        cards = []
        for project in NOTEBOOKS:
            captions.append(f"\n## {project.capitalize()}\n")
            figures = [item for item in self.records if item["projet"] == project and item["fichier"].endswith(".png")
                       and item["categorie"] not in {"inference_original", "inference_detail"}]
            for index, item in enumerate(figures, 1):
                if item["categorie"] not in {"code_notebook", "code_script", "resultat_image"}:
                    captions.append(f"\n### Figure {project[0].upper()}{index:02d} — {item['titre']}\n\n"
                                    f"Fichier : `{item['fichier']}`\n\n{item['legende']}\n\nSource : `{item['source']}`.\n")
            cards.append(f'<h2 id="{project}">{project.capitalize()}</h2><p>'
                         f'<a href="{project}/notebook/lecture_notebook.html">Lire le notebook</a> · '
                         f'<a href="{project}/notebook/{NOTEBOOKS[project]}">Notebook original</a></p>')
            for category, label in [("graphique_original", "Graphiques originaux"),
                                    ("graphique_reconstruit", "Courbes, résultats et inférences produits à partir des données réelles"),
                                    ("code_notebook", "Chaque bloc de code du notebook"),
                                    ("code_script", "Scripts d'inférence"), ("resultat_image", "Résultats et configurations en images")]:
                selected = [item for item in figures if item["categorie"] == category]
                if not selected:
                    continue
                cards.append(f'<h3>{label}</h3><div class="gallery">')
                for item in selected:
                    path = html.escape(item["fichier"], quote=True)
                    cards.append('<article><a target="_blank" href="' + path + '">'
                                 '<img loading="lazy" src="' + path + '" alt="' + html.escape(item["titre"], quote=True) + '"></a>'
                                 '<h4>' + html.escape(item["titre"]) + '</h4><p>' + html.escape(item["legende"]) + '</p>'
                                 '<small>Source : ' + html.escape(item["source"]) + '</small><p><a href="' + path + '">Ouvrir l’image en grand</a></p></article>')
                cards.append('</div>')
        (self.output / "LEGENDES_MEMOIRE.md").write_text("\n".join(captions), encoding="utf-8")
        nb_summary = "\n".join(f"- {p} : {s['blocs_code']} blocs de code ; {s['sorties_enregistrees']} sorties sauvegardées dans le notebook."
                               for p, s in self.notebooks.items())
        png_count = sum(item["fichier"].endswith(".png") for item in self.records)
        readme = f"""# Éléments pour le mémoire — classification / segmentation

Ouvrir **index.html** dans un navigateur pour parcourir toutes les illustrations.
Les deux sous-dossiers sont autonomes pour les fichiers de recherche qu'ils contiennent.
Ce dossier comporte {png_count} images PNG, en plus des versions SVG vectorielles, des sources et des données.

## Organisation

- `classification/notebook/`, `segmentation/notebook/` : notebooks originaux identiques et versions HTML de lecture hors ligne.
- `codes/` : chaque cellule de code en `.py` et en images PNG numérotées. Les longues cellules sont découpées en parties, sans suppression de lignes.
- `graphiques/` : figures originales et reconstructions à 300 dpi / SVG à partir de données réelles.
- `resultats/` : fichiers CSV, JSON, rapports et captures textuelles des configurations/résultats.
- `scripts_inference/` : codes d'inférence originaux et leurs images.
- `sorties_notebook/` : état des sorties effectivement présentes dans chaque notebook.
- `segmentation/inferences/` : images sources publiques de démonstration, masques prédits et probabilités brutes des deux nouvelles inférences.
- `LEGENDES_MEMOIRE.md` : titres et légendes proposés ; `inventaire.csv` / `.json` : provenance et SHA-256.

## Ce qui est réellement disponible

{nb_summary}

Il n'existe donc aucune sortie de cellule enregistrée à extraire des notebooks fournis.
Les captures de code ne sont pas des captures d'une exécution. Aucun entraînement n'a été relancé.
Les graphiques de classification originaux sont bien présents dans `classification_package/` et ont été copiés sans modification.
L'historique réel de segmentation a été extrait de `segmentation_package/best_model.pt` :
{self.notebooks['segmentation'].get('epoques_checkpoint', 0)} époques enregistrées, jusqu'au meilleur checkpoint fourni.
Le champ `finished` vaut `{self.notebooks['segmentation'].get('checkpoint_finished')}` : il ne faut pas présenter cet historique comme la totalité d'un entraînement terminé.

## Précautions scientifiques pour la rédaction

1. **Jeux de données différents** : le notebook de classification concerne BUSI ; celui de segmentation concerne BUS-BRA. Ne pas fusionner leurs protocoles ni leurs scores.
2. Classification : matrice de confusion et métriques fournies concernent le test de 112 images BUSI. Les nouvelles courbes sont redessinées depuis les CSV, sans lissage.
3. Segmentation : `selection.json` est créé par une recherche sur la VALIDATION dans le notebook. Son Dice macro-patient (environ 0,9118) n'est PAS un résultat de test. Le checkpoint a aussi ses propres scores de validation, avant la sélection TTA/post-traitement.
4. Les résultats du test BUS-BRA (`test_summary.json`, `test_per_image.csv`), ses masques de référence et son journal final complet ne sont pas fournis. Impossible de produire honnêtement une matrice de confusion pixel à pixel ou une distribution de Dice de TEST BUS-BRA sans ces données.
5. Les deux nouvelles figures d'inférence de segmentation utilisent des exemples BUSI bénin et malin de `cancer_seins/`, avec les poids fournis et les paramètres sélectionnés. Ce sont des démonstrations qualitatives externes, sans masques de référence, sans Dice/IoU calculé et sans conclusion de validation clinique. La classe dans le nom vient du dossier source.
6. Aucune perte de validation n'est inventée pour la segmentation : le checkpoint conserve la perte d'apprentissage et les métriques Dice/IoU de validation.
7. Les figures d'échographie et leurs sources doivent être citées selon les règles de licence des jeux de données. Un Grad-CAM explique une classification ; ce n'est ni une délimitation anatomique validée ni une vérité terrain.

## Insertion dans le mémoire

Utiliser les figures complètes dans le chapitre résultats, et les blocs de code détaillés en annexe.
Les variantes SVG sont recommandées si le logiciel de rédaction les accepte ; sinon utiliser les PNG 300 dpi.
Les PNG originaux gardent leur résolution d'origine : aucun détail artificiel n'a été ajouté.
Les codes sont conçus pour une largeur proche de la zone de texte A4 ; ne pas réunir une longue cellule sur une seule page au prix de la lisibilité.
Conserver les légendes et la distinction validation/test. Le catalogue permet d'ouvrir chaque image à sa résolution native.

## Reproduction et périmètre

Depuis `D:\\cancer`, exécuter ` .\\.venv\\Scripts\\python.exe .\\scripts\\prepare_thesis_assets.py --output elements_memoire_nouvel_export --inferences `.
L'outil refuse d'écraser un dossier existant. Il ne lance pas les notebooks ni les entraînements.
Les poids volumineux, comptes, historiques patients, secrets, journaux et fichiers privés de l'application sont exclus.
Ce dossier n'est pas envoyé sur GitHub ni sur Railway.
"""
        (self.output / "LIRE_MOI.md").write_text(readme, encoding="utf-8")
        intro = (f'<p>{png_count} images PNG · 2 notebooks · sources, données et légendes incluses.</p>'
                 '<nav><a href="#classification">Classification</a> · <a href="#segmentation">Segmentation</a> · '
                 '<a href="LIRE_MOI.md">Guide et limites</a> · <a href="LEGENDES_MEMOIRE.md">Légendes</a> · '
                 '<a href="inventaire.csv">Inventaire CSV</a></nav>'
                 '<div class="notice"><strong>À conserver dans le mémoire</strong><p>Classification : BUSI. Segmentation : BUS-BRA. '
                 'Les scores de sélection de segmentation sont des scores de validation, pas de test. '
                 'Les notebooks n’ont aucune sortie sauvegardée ; les figures disponibles sont sourcées séparément. '
                 'Les inférences de segmentation ajoutées sont des démonstrations externes BUSI, sans vérité terrain.</p></div>')
        (self.output / "index.html").write_text(page_html("Illustrations pour le mémoire", intro + "".join(cards)), encoding="utf-8")
        archive = self.output.with_suffix(".zip")
        if archive.exists():
            raise SystemExit(f"Archive déjà présente, non écrasée : {archive}")
        with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as handle:
            for path in sorted(self.output.rglob("*")):
                if path.is_file():
                    handle.write(path, arcname=self.output.name + "/" + path.relative_to(self.output).as_posix())
        print(json.dumps(dict(dossier=str(self.output), archive=str(archive), images_png=png_count,
                              fichiers=len([p for p in self.output.rglob('*') if p.is_file()]),
                              notebooks={p: {k: v for k, v in s.items() if k != 'cellules_code'} for p, s in self.notebooks.items()}),
                         ensure_ascii=False, indent=2), flush=True)


def page_html(title: str, body: str) -> str:
    css = HtmlFormatter().get_style_defs(".highlight")
    return (f'<!doctype html><html lang="fr"><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">'
            f'<title>{html.escape(title)}</title><style>'
            'body{margin:0 auto;padding:36px 24px;max-width:1440px;background:#f4f7f9;color:#243645;font:16px/1.6 Segoe UI,Arial,sans-serif}'
            'h1,h2,h3{color:#17364a}h2{border-bottom:2px solid #d4e1e7;padding-top:36px}a{color:#156078}nav{display:flex;gap:20px;flex-wrap:wrap}'
            '.notice{background:#fff4d9;border-left:5px solid #bd8b1d;padding:20px;margin:28px 0}.gallery{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:24px}'
            'article,section{background:white;border:1px solid #dce5ea;border-radius:10px;padding:18px;margin:14px 0;overflow:auto}'
            'article img{width:100%;height:270px;object-fit:contain;background:white}article p{font-size:14px}small{color:#617582;overflow-wrap:anywhere}'
            'pre{font:14px/1.55 Consolas,monospace;white-space:pre;overflow:auto}.highlight{padding:14px}table{border-collapse:collapse}td,th{border:1px solid #ddd;padding:8px}'
            + css + f'</style><body><h1>{html.escape(title)}</h1>{body}</body></html>')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default="elements_memoire")
    parser.add_argument("--inferences", action="store_true", help="Add real qualitative segmentation predictions on two public BUSI demo images.")
    args = parser.parse_args()
    output = ROOT / args.output
    if output.resolve().parent != ROOT:
        raise SystemExit("Choisir un nouveau sous-dossier direct de D:\\cancer.")
    plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 11, "axes.titlepad": 12})
    exporter = Export(output)
    for project in NOTEBOOKS:
        exporter.export_notebook(project)
    exporter.supplied_results()
    exporter.classification_plots()
    exporter.segmentation_results(include_inference=args.inferences)
    exporter.finish(Path(__file__).resolve())


if __name__ == "__main__":
    main()
