# Proxy2VPN Test Suite

This directory contains automated tests for the Proxy2VPN script.

## Test Structure

- **Integration Tests**: Complete workflow tests that validate core features
  - `integration_container_lifecycle.sh`: Tests full container lifecycle (create, verify, start/stop, update, delete)
  - `integration_profile_management.sh`: Tests profile creation, verification, and container creation from profiles
  - `integration_server_list.sh`: Tests server list updates, provider/country/city listing, and container creation with specific locations
  - `integration_batch_operations.sh`: Tests batch file creation, container creation from batch file, and bulk operations (up/down/cleanup)
  - `integration_preset_management.sh`: Tests preset creation, listing, and applying presets to create new containers
- **Unit Tests**: Individual function/command tests (in `unit_tests/` directory)

## Test Runners

- `run_integration_tests.sh`: Runs only integration tests
- `run_unit_tests.sh`: Runs only unit tests
- `run_all_tests.sh`: Runs both integration and unit tests
- `setup_test_env.sh`: Sets up the test environment (profiles, env files, etc.)

## Running Tests

Execute any of the test runners from the project root:

```bash
# Run only integration tests
./tests/run_integration_tests.sh

# Run only unit tests
./tests/run_unit_tests.sh

# Run all tests
./tests/run_all_tests.sh
```

## Adding New Tests

### Adding Integration Tests

To add new integration tests:

1. Create a new script in the tests directory with the naming pattern `integration_*.sh`
2. Make the script executable: `chmod +x tests/integration_my_test.sh`
3. Return exit code 0 for success, non-zero for failure
4. The integration test runner will automatically pick up your new test

### Adding Unit Tests

To add new unit tests:

1. Create a new script in the unit_tests directory with the naming pattern `verify_*.sh`
2. Make the script executable: `chmod +x tests/unit_tests/verify_my_test.sh`
3. Return exit code 0 for success, non-zero for failure
4. The unit test runner will automatically pick up your new test

## Integration vs Unit Tests

- **Integration Tests**: Test complete workflows across multiple components
  - Focus on validating end-to-end functionality
  - Verify that system components work together correctly
  - Test realistic user scenarios and complete features
  - Cover the most common and critical user workflows

- **Unit Tests**: Test individual functions or commands in isolation
  - Focus on testing specific functions and edge cases
  - Help to identify bugs in individual components
  - Provide finer-grained validation
  - Useful for regression testing

## CI/CD Integration

Tests are automatically run on GitHub Actions:
- On pushes to main
- On pull requests to main
- Can be manually triggered from the Actions tab

The GitHub workflow definition is located at `.github/workflows/tests.yml`