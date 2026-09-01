from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


# @lat: [[architecture#Development Verification#Release Automation]]
def test_release_workflows_enforce_single_provenance_path():
    publish = (ROOT / ".github/workflows/publish.yml").read_text()
    prepare = (ROOT / ".github/workflows/release.yml").read_text()
    tests = (ROOT / ".github/workflows/python-tests.yml").read_text()
    makefile = (ROOT / "Makefile").read_text()

    assert "workflow_dispatch" not in publish
    assert "ref: ${{ github.event.release.tag_name }}" in publish
    assert "Validate release provenance" in publish
    assert "skip-existing: true" in publish

    assert "workflow_dispatch" in prepare
    assert "Choose the next version;" in prepare
    assert "uv lock" in prepare
    assert "git add -A pyproject.toml uv.lock CHANGELOG.md news" in prepare
    assert "uv run --locked" in prepare

    assert "'.github/workflows/*.yml'" in tests
    assert "\nrelease:" not in makefile
