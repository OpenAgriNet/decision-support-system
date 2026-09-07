"""AST import extraction, shared by the boundary suites.

The framework boundary only needs the root module (`pydantic_ai`). The transport
boundary needs the full dotted path, because `dss.core.shared.models` is allowed
where `dss.core.intent.service` is not.
"""

from __future__ import annotations

import ast


def imported_modules(tree: ast.AST) -> set[str]:
    """Full dotted module names. `from a.b import c` yields `"a.b"`.

    Relative imports yield nothing — they cannot cross a package boundary.
    """
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names.add(node.module)
    return names


def dynamic_import_modules(tree: ast.AST) -> set[str]:
    """Module names passed as string literals to `import_module`/`__import__`.

    A computed name is undetectable; no static check closes that gap.
    """
    found: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        target = node.func
        is_import_call = (
            isinstance(target, ast.Name) and target.id == "__import__"
        ) or (isinstance(target, ast.Attribute) and target.attr == "import_module")
        if not is_import_call:
            continue
        found.update(
            arg.value
            for arg in node.args
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str)
        )
    return found
