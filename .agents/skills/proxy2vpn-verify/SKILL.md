---
name: proxy2vpn-verify
description: Select and run proxy2vpn regression checks for CLI, recovery, storage, runtime, egress, or fleet changes, and verify lat.md references before handing off work.
---

# Proxy2VPN verification

Use `Makefile`, `.github/workflows/python-tests.yml`, and `pyproject.toml` as the tool contracts. The project uses the locked uv environment. Apply pstack's `principle-prove-it-works` when available and report observed outcomes.

## Focused checks

Run the bundled helper from any directory. Supply one or more scopes; repeated test files are deduplicated:

```bash
python3 .agents/skills/proxy2vpn-verify/scripts/run_checks.py recovery storage --dry-run
python3 .agents/skills/proxy2vpn-verify/scripts/run_checks.py recovery storage
```

Scopes are `cli`, `recovery`, `storage`, `runtime`, `egress`, `fleet`, and `full`. Use `--dry-run` to inspect exact commands. The helper runs selected tests followed by `lat check`, stops on failure, and returns the failing command's exit code. `full` uses the repository's `make test` target. It does not enable live-security tests.

For changes outside these scopes, run the matching tests directly with `uv run --locked pytest ...` and run `lat check` separately. Documentation-only work needs `lat check`; skill changes also need the skill-creator validator and direct checks of any new helper.

## Broader verification

For Python source or test changes, run `make fmt-check` and `make lint` as appropriate. Cross-cutting changes may need `make test`. Run `make changelog-check` when release fragments change. `make lint` uses `uvx ty check`, so its type-checker invocation can need network access.

`make test-live-security` is a separate opt-in Docker/integration workflow. Read its harness and configuration before running it in a suitable disposable environment. Do not run it as a substitute for isolated regression tests or point it at a production compose root.

When behavior changes, update its lat.md concept and test specifications. Give each required spec exactly one nearby `@lat:` reference in the covering test. Run `lat check` before completion.

Report which checks actually ran, their results, and any blocked checks. Distinguish a command preview or test collection from executed tests. After passing checks, repeat only if new edits or unresolved failures justify it.
