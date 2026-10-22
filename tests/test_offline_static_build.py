import hashlib
import json
import zipfile
from pathlib import Path

import pytest

from scripts import build_offline_static as static


def fixture_files(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    for name in static.SOURCE_ASSETS:
        (source / name).write_text("public fixture", encoding="utf-8")
    manifest = {"models": {name: {"url": "./models/" + name + ".onnx", "bytes": len(name),
        "sha256": hashlib.sha256(name.encode()).hexdigest()} for name in ("classification", "segmentation")}}
    (source / "assets-manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    archive = tmp_path / "fixture.zip"
    with zipfile.ZipFile(archive, "w") as zipped:
        for name in static.BINARY_ASSETS:
            data = Path(name).stem.encode() if name.endswith(".onnx") else b"public runtime fixture"
            zipped.writestr("busbra_offline_telephone/" + name, data)
        zipped.writestr("busbra_offline_telephone/instance/users.json", "DO NOT PUBLISH")
        zipped.writestr("../../unexpected.txt", "DO NOT EXTRACT")
    (source / "distribution.json").write_text(json.dumps({"archive": {"bytes": archive.stat().st_size,
        "sha256": static.sha256(archive)}}), encoding="utf-8")
    return source, archive


def test_only_explicit_public_files_are_published(tmp_path):
    source, archive = fixture_files(tmp_path)
    output = tmp_path / "public"
    static.build(source, output, archive)
    assert {p.relative_to(output).as_posix() for p in output.rglob("*") if p.is_file()} == set(static.SOURCE_ASSETS + static.BINARY_ASSETS)
    assert not (tmp_path / "unexpected.txt").exists()


def test_unexpected_existing_files_stop_publication(tmp_path):
    source, archive = fixture_files(tmp_path)
    output = tmp_path / "public"
    output.mkdir()
    (output / "private.txt").write_text("keep", encoding="utf-8")
    with pytest.raises(ValueError, match="hors de la liste"):
        static.build(source, output, archive)
    assert (output / "private.txt").read_text() == "keep"


def test_bad_archive_or_model_integrity_stops_build(tmp_path):
    source, archive = fixture_files(tmp_path)
    with archive.open("ab") as handle:
        handle.write(b"corrupt archive")
    with pytest.raises(ValueError, match="SHA-256"):
        static.build(source, tmp_path / "public", archive)
