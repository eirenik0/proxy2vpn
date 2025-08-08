# Proxy2VPN Test Suite

This directory contains automated integration tests for the Proxy2VPN script.

## Test Structure

- **Integration Tests**: Complete workflow tests that validate core features
  - `integration_container_lifecycle.sh`: Tests full container lifecycle (create, verify, start/stop, update, delete)
  - `integration_profile_management.sh`: Tests profile creation, verification, and container creation from profiles
  - `integration_server_list.sh`: Tests server list updates, provider/country/city listing, and container creation with specific locations
  - `integration_batch_operations.sh`: Tests batch file creation, container creation from batch file, and bulk operations (up/down/cleanup)
  - `integration_preset_management.sh`: Tests preset creation, listing, and applying presets to create new containers

## Test Runner

- `run_integration_tests.sh`: Runs all integration tests
- `setup_test_env.sh`: Sets up the test environment (profiles, env files, etc.)

## Running Tests

Execute the integration test runner from the project root:

```bash
# Run integration tests
./tests/run_integration_tests.sh
```

## Adding New Tests

To add new integration tests:

1. Create a new script in the tests directory with the naming pattern `integration_*.sh`
2. Make the script executable: `chmod +x tests/integration_my_test.sh`
3. Return exit code 0 for success, non-zero for failure
4. The integration test runner will automatically pick up your new test

## CI/CD Integration

Tests are automatically run on GitHub Actions:
- On pushes to main
- On pull requests to main
- Can be manually triggered from the Actions tab

The GitHub workflow definition is located at `.github/workflows/tests.yml`

