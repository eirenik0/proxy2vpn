# Proxy2VPN Test Suite

This directory contains unit tests for the Python-based CLI.

## Running Tests

Run all tests with:

```bash
make test
# or
uv run --with pytest,pytest-xdist pytest -n auto
```

## CI/CD Integration

Tests run automatically on GitHub Actions via `.github/workflows/python-tests.yml`.

## Notes

- Many CLI tests use temporary compose roots; support files such as `control-server-auth.toml` are expected to be resolved relative to the active compose file.
- Non-fleet service-definition flows should use `vpn add`; destructive refresh behavior belongs to `vpn update`.
