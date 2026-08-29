"""Architecture boundary test: Engine must be 100% pure Python with zero Django imports."""
from pathlib import Path
import ast
import pytest


@pytest.mark.unit
def test_engine_package_has_zero_django_imports():
    """
    Scans every Python file in the `engine/` directory tree using Python AST.
    Asserts that no module imports `django` or any submodule of `django`.
    """
    engine_root = Path(__file__).resolve().parent.parent.parent / "engine"
    assert engine_root.is_dir(), f"Engine directory not found at {engine_root}"

    python_files = list(engine_root.rglob("*.py"))
    assert len(python_files) > 0, "No python files found in engine/"

    violations = []

    for py_file in python_files:
        content = py_file.read_text(encoding="utf-8")
        tree = ast.parse(content, filename=str(py_file))

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name == "django" or alias.name.startswith("django."):
                        violations.append(f"{py_file.name}:{node.lineno} -> import {alias.name}")
            elif isinstance(node, ast.ImportFrom):
                if node.module and (node.module == "django" or node.module.startswith("django.")):
                    violations.append(f"{py_file.name}:{node.lineno} -> from {node.module} import ...")

    assert len(violations) == 0, f"Found forbidden Django imports in engine/: {violations}"
