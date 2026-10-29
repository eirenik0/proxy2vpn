# Project development skills

Repository skills capture the ownership and verification rules needed for changes to the CLI, VPN fleet, and watchdog. They complement pstack's general engineering workflows.

## Discovery and routing

`AGENTS.md` routes tasks to skills in `.agents/skills/`, while the existing remote operations skill remains in `.codex/skills/proxy2vpn-ops/`.

`proxy2vpn-design` covers ownership and interface changes using [[architecture]], [[egress]], [[gluetun-runtime]], and [[fleet]]. `proxy2vpn-watchdog` covers decision and execution changes using [[recovery-policy]], [[agent-storage-tests]], and [[agent-evidence-tests]]. The existing `lat-md` skill defines documentation syntax.

General investigation, design, and review guidance stays in the installed pstack plugin. Project skills state the repository contracts directly so they also work when that plugin is unavailable. Installing these skills does not change runtime behavior or authorize production operations.

## Verification scopes

`proxy2vpn-verify` provides a command helper for reproducible regression selection, using locked dependencies and the existing Make targets.

The helper at `.agents/skills/proxy2vpn-verify/scripts/run_checks.py` accepts CLI, recovery, storage, runtime, egress, fleet, and full scopes. Focused scopes select existing regression files and deduplicate overlaps. Full scope runs `make test`. Successful tests are followed by `lat check`; a failing command stops execution with a nonzero status.

`--dry-run` prints commands without executing them. Focused selections check that every named test file exists before execution. These scopes are starting points; changes crossing their boundaries require the corresponding additional checks. Live-security testing remains an explicit separate workflow. See [[architecture#Development Verification]] for the repository tool contracts.
