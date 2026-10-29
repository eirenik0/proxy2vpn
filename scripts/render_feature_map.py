"""Render the JSON feature map as Markdown, or check that the view is current."""

import argparse
import json
from pathlib import Path
import re


def render(data: dict, root: Path) -> str:
    if data["version"] != 1:
        raise ValueError("Unsupported feature-map version")
    features = data["features"]
    identifiers = [feature["id"] for feature in features]
    if not features or len(set(identifiers)) != len(identifiers):
        raise ValueError("Feature IDs must be nonempty and unique")
    graph = data["relationships"]
    nodes = {node["id"]: node["label"] for node in graph["nodes"]}
    if len(nodes) != len(graph["nodes"]):
        raise ValueError("Relationship node IDs must be unique")
    for identifier in [*identifiers, *nodes]:
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]*", identifier):
            raise ValueError(f"Invalid map ID: {identifier}")
    for edge in graph["edges"]:
        if edge["from"] not in nodes or edge["to"] not in nodes:
            raise ValueError("Relationship edge references an unknown node")
    for feature in features:
        for reference in feature["source_refs"] + feature["tests"]:
            path = reference.split("#", 1)[0]
            if not (root / path).is_file():
                raise ValueError(f"Missing feature reference: {path}")
    lines = [
        f"# {data['title']}",
        "",
        data["summary"],
        "",
        "<!-- Generated from docs/feature-map.json. "
        "Run python3 scripts/render_feature_map.py; edit the JSON source. -->",
        "",
        data["scope"],
        "",
        "## Feature relationships",
        "",
        graph["summary"],
        "",
        "```mermaid",
        "flowchart TD",
    ]
    for identifier, label in nodes.items():
        lines.append(f"    {identifier}[{json.dumps(label)}]")
    for edge in graph["edges"]:
        lines.append(f"    {edge['from']} --> {edge['to']}")
    lines.extend(["```", "", graph["notes"]])
    for feature in features:
        lines.extend(["", f"## {feature['title']}", "", feature["summary"]])
        for paragraph in feature["details"]:
            lines.extend(["", paragraph])
        coverage = [f"`{path}`" for path in feature["tests"]]
        coverage.extend(f"[[{spec}]]" for spec in feature["test_specs"])
        note = feature["coverage_note"]
        lines.extend(
            [
                "",
                "Coverage: " + ", ".join(coverage) + "." + (f" {note}" if note else ""),
            ]
        )
    maintenance = data["maintenance"]
    lines.extend(["", "## Navigation and maintenance", "", maintenance["summary"]])
    for paragraph in maintenance["details"]:
        lines.extend(["", paragraph])
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    source = root / "docs/feature-map.json"
    target = root / "lat.md/feature-map.md"
    try:
        rendered = render(json.loads(source.read_text()), root)
    except (OSError, ValueError, KeyError, TypeError) as error:
        parser.exit(1, f"Invalid feature map: {error}\n")
    if args.check:
        if not target.exists() or target.read_text() != rendered:
            print("Feature map is stale. Run python3 scripts/render_feature_map.py")
            return 1
        print("Markdown feature map matches its JSON source.")
    else:
        target.write_text(rendered)
        print(f"Wrote {target.relative_to(root)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
