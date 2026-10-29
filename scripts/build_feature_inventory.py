"""Build or check the CLI inventory without importing or running application code."""

import argparse
import ast
from pathlib import Path


def inventory(root: Path) -> str:
    cli = root / "src/proxy2vpn/cli"
    main = ast.parse((cli / "main.py").read_text())
    groups = []
    for node in main.body:
        if not isinstance(node, ast.Expr) or not isinstance(node.value, ast.Call):
            continue
        call = node.value
        if not (
            isinstance(call.func, ast.Attribute)
            and isinstance(call.func.value, ast.Name)
            and call.func.value.id == "app"
            and call.func.attr == "add_typer"
        ):
            continue
        group = next(kw.value for kw in call.keywords if kw.arg == "name")
        name = ast.literal_eval(group)
        target = call.args[0]
        if not isinstance(target, ast.Attribute) or target.attr != "app":
            raise ValueError(f"Unsupported CLI registration: {name}")
        module = target.value.id
        path = cli / "commands" / f"{module}.py"
        commands = []
        for function in ast.parse(path.read_text()).body:
            if not isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for decorator in function.decorator_list:
                if not (
                    isinstance(decorator, ast.Call)
                    and isinstance(decorator.func, ast.Attribute)
                    and isinstance(decorator.func.value, ast.Name)
                    and decorator.func.value.id == "app"
                    and decorator.func.attr == "command"
                ):
                    continue
                command = (
                    ast.literal_eval(decorator.args[0])
                    if decorator.args
                    else function.name.replace("_", "-")
                )
                source = path.relative_to(root).as_posix()
                commands.append((command, source, function.lineno))
        groups.append((name, commands))
    total = sum(len(commands) for _, commands in groups)
    lines = [
        "# CLI command inventory",
        "",
        f"The CLI registers {total} commands in {len(groups)} groups. "
        "This inventory records command entry points, not live fleet state.",
        "",
        "Generated from `app.add_typer` registrations and `app.command` decorators. "
        "Regenerate with `python3 scripts/build_feature_inventory.py`; "
        "check freshness with `python3 scripts/build_feature_inventory.py --check`.",
        "",
        "See the [feature map](../lat.md/feature-map.md) for behavior, ownership, "
        "state, constraints, and regression coverage.",
    ]
    for name, commands in groups:
        lines.extend(["", f"## {name}", ""])
        for command, source, line in commands:
            lines.append(f"- [`proxy2vpn {name} {command}`](../{source}#L{line})")
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    target = root / "docs/feature-commands.md"
    rendered = inventory(root)
    if args.check:
        if not target.exists() or target.read_text() != rendered:
            print(
                "CLI inventory is stale. Run python3 scripts/build_feature_inventory.py"
            )
            return 1
        print("CLI inventory matches source registrations.")
    else:
        target.write_text(rendered)
        print(f"Wrote {target.relative_to(root)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
