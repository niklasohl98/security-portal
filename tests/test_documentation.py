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
ROUTE = re.compile(
    r'@(?:app|router)\.(get|post|put|patch|delete)\(\s*["\']([^"\']+)["\']',
    re.MULTILINE,
)
AUDIT_CODE = re.compile(
    r'["\']((?:auth|backup|exception|export|external_backup|identity|report|sync|user)\.[a-z_]+)["\']'
)


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


def test_every_web_route_is_in_the_user_guide() -> None:
    routes: set[tuple[str, str]] = set()
    for source in (ROOT / "app").rglob("*.py"):
        routes.update(
            (method.upper(), path)
            for method, path in ROUTE.findall(source.read_text(encoding="utf-8"))
        )
    reference = (ROOT / "docs" / "user-guide.md").read_text(encoding="utf-8")
    missing = sorted(
        f"{method} {path}" for method, path in routes if f"`{method} {path}`" not in reference
    )
    assert routes
    assert missing == [], "Nicht dokumentierte Webrouten: " + ", ".join(missing)


def test_every_database_table_is_in_the_data_model_reference() -> None:
    model_tree = ast.parse((ROOT / "app" / "db" / "models.py").read_text(encoding="utf-8"))
    tables: set[str] = set()
    for node in ast.walk(model_tree):
        if not isinstance(node, ast.Assign):
            continue
        if (
            any(
                isinstance(target, ast.Name) and target.id == "__tablename__"
                for target in node.targets
            )
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
        ):
            tables.add(node.value.value)
    reference = (ROOT / "docs" / "data-model.md").read_text(encoding="utf-8")
    missing = sorted(table for table in tables if f"`{table}`" not in reference)
    assert tables
    assert missing == [], "Nicht dokumentierte Datenbanktabellen: " + ", ".join(missing)


def test_every_static_audit_code_is_in_the_audit_reference() -> None:
    codes: set[str] = set()
    for source in (ROOT / "app").rglob("*.py"):
        if source == ROOT / "app" / "db" / "models.py" or "migrations" in source.parts:
            continue  # Schema-Fremdschlüssel wie ``identity.id`` sind keine Ereigniscodes.
        codes.update(AUDIT_CODE.findall(source.read_text(encoding="utf-8")))
    reference = (ROOT / "docs" / "audit-log.md").read_text(encoding="utf-8")
    missing = sorted(code for code in codes if f"`{code}`" not in reference)
    assert codes
    assert missing == [], "Nicht dokumentierte Audit-Codes: " + ", ".join(missing)


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
