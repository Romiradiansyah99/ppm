import textwrap
from pathlib import Path

from ppm.corpus.manifest import glob_match, resolve_corpus


def _write(path: Path, text: str = "x") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_glob_matching():
    assert glob_match("a/b/c.pdf", "**/*.pdf")
    assert glob_match("c.pdf", "**/*.pdf")
    assert glob_match("a/b/c.pdf", "a/**/*.pdf")
    assert not glob_match("a/b/c.docx", "**/*.pdf")


def test_resolver_applies_skip_licence_and_override(tmp_path):
    base = tmp_path
    _write(base / "IDPM/1. Design/101 - Concept.docx")
    _write(base / "IDPM/1. Design/~$lock.docx")
    _write(base / "T&T/PM/0 - Manual.pdf")
    _write(base / "T&T/PM/191209 Barclays Stamford_PEP_V7.docx")
    _write(base / "T&T/PM/RICS Guidance Note.pdf")
    _write(base / "T&T/PM/legacy.doc")
    _write(base / "T&T/PM/archive.zip")
    _write(base / "T&T/PM/old.xls")

    (base / "client_blocklist.txt").write_text("Barclays\n", encoding="utf-8")
    manifest = base / "manifest.yaml"
    manifest.write_text(textwrap.dedent("""
        version: 1
        base_dir: "{base}"
        skip_paths: []
        exclude:
          - "**/~$*"
          - "**/*.zip"
        roots:
          - collection: ttpm
            path: "T&T/PM"
            licence: office_authored
          - collection: idpm
            path: "IDPM"
            licence: office_authored
        overrides:
          - match: "**/RICS Guidance Note.pdf"
            licence: third_party_reference
            index: false
          - match: "**/0 - Manual.pdf"
            collection: procedures
            is_template: false
    """).format(base=base.as_posix()), encoding="utf-8")

    report = resolve_corpus(manifest)
    entries = {entry.path.name: entry for entry in report.entries}

    assert "~$lock.docx" not in entries
    assert "archive.zip" not in entries
    assert entries["RICS Guidance Note.pdf"].index is False
    assert entries["RICS Guidance Note.pdf"].licence_class == "third_party_reference"
    assert entries["0 - Manual.pdf"].collection == "procedures"
    assert entries["0 - Manual.pdf"].is_template is False
    assert entries["legacy.doc"].index is False
    assert entries["101 - Concept.docx"].stage == "design"
    assert entries["101 - Concept.docx"].format == "docx"
    assert entries["191209 Barclays Stamford_PEP_V7.docx"].title.startswith("191209 [CLIENT]")
    assert any(item.reason.startswith("unsupported_format") for item in report.excluded)


def test_manifest_missing_root_is_a_warning(tmp_path):
    manifest = tmp_path / "manifest.yaml"
    manifest.write_text(
        "version: 1\nbase_dir: '" + tmp_path.as_posix() + "'\nroots:\n  - collection: x\n    path: nope\n",
        encoding="utf-8",
    )
    report = resolve_corpus(manifest)
    assert report.entries == []
    assert any("missing root" in warning for warning in report.warnings)
