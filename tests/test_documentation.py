from __future__ import annotations

import ast
import re
from pathlib import Path
from urllib.parse import unquote

import pytest

from app.cli import main

ROOT = Path(__file__).parent.parent
MARKDOWN_FILES = tuple(
    sorted(
        path
        for path in ROOT.rglob("*.md")
        if ".git" not in path.parts and ".venv" not in path.parts
    )
)
LINK = re.compile(r"\[[^]]+]\(([^)]+)\)")
ENV_NAME = re.compile(r"\bKW_[A-Z0-9]+(?:_[A-Z0-9]+)+\b")


def test_all_relative_markdown_links_resolve() -> None:
    broken: list[str] = []
    for document in MARKDOWN_FILES:
        for raw in LINK.findall(document.read_text(encoding="utf-8")):
            link = raw.strip().strip("<>").split("#", 1)[0]
            if not link or link.startswith(("https://", "http://", "mailto:")):
                continue
            target = (document.parent / unquote(link)).resolve()
            if not target.is_relative_to(ROOT) or not target.exists():
                broken.append(f"{document.relative_to(ROOT)} -> {raw}")
    assert broken == [], "Ungültige interne Dokumentationslinks:\n" + "\n".join(broken)


def test_every_runtime_environment_variable_is_in_the_configuration_reference() -> None:
    names: set[str] = set()
    for source in (ROOT / "app").rglob("*.py"):
        names.update(ENV_NAME.findall(source.read_text(encoding="utf-8")))
    names.update(ENV_NAME.findall((ROOT / ".env.example").read_text(encoding="utf-8")))
    reference = (ROOT / "docs" / "configuration.md").read_text(encoding="utf-8")
    missing = sorted(name for name in names if f"`{name}`" not in reference)
    assert missing == [], "Nicht dokumentierte Umgebungsvariablen: " + ", ".join(missing)


def test_every_cli_command_is_in_the_cli_reference(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as stopped:
        main(["--help"])
    assert stopped.value.code == 0
    help_text = capsys.readouterr().out
    commands = set(re.findall(r"^    ([a-z][a-z0-9-]+)\s", help_text, re.MULTILINE))
    reference = (ROOT / "docs" / "cli.md").read_text(encoding="utf-8")
    missing = sorted(command for command in commands if f"`{command}" not in reference)
    assert commands
    assert missing == [], "Nicht dokumentierte CLI-Befehle: " + ", ".join(missing)


def test_public_python_interfaces_have_docstrings() -> None:
    missing: list[str] = []
    for source in (ROOT / "app").rglob("*.py"):
        if "migrations" in source.parts:
            continue
        for node in ast.parse(source.read_text(encoding="utf-8")).body:
            if (
                isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
                and not node.name.startswith("_")
                and ast.get_docstring(node) is None
            ):
                missing.append(f"{source.relative_to(ROOT)}:{node.lineno} {node.name}")
    assert missing == [], "Öffentliche Python-Schnittstellen ohne Docstring:\n" + "\n".join(missing)
