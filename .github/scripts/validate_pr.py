import os
import sys
import subprocess
import json
import shlex

CONFIG_PATH = ".github/project_config.json"
BASE_SHA = os.environ.get("BASE_SHA")
HEAD_SHA = os.environ.get("HEAD_SHA")
LABELS = os.environ.get("LABELS")
GITHUB_WORKSPACE = os.environ.get("GITHUB_WORKSPACE", ".")


def log(message, level="info"):
    """Prints messages, mapping levels to GitHub Actions commands."""
    if level == "error":
        print(f"::error::{message}", file=sys.stderr)
    elif level == "warning":
        print(f"::warning::{message}", file=sys.stdout)
    else:
        print(message, file=sys.stdout)


def run_command(command_str, cwd=GITHUB_WORKSPACE, expect_failure=False):
    """Runs a shell command, captures output, and checks exit code."""
    log(f"Executing: {command_str}")
    try:
        # Using shell=True for simplicity with user-provided commands.
        # Ensure commands in config are trustworthy.
        process = subprocess.run(
            command_str,
            shell=True,
            check=False,  # Don't automatically raise exception
            capture_output=True,
            text=True,
            cwd=cwd,
        )
        log(f"Exit Code: {process.returncode}")
        # Log stdout/stderr conditionally to avoid excessive logging
        if process.stdout and process.returncode != 0:  # Log stdout if command failed
            log("Stdout:\n" + process.stdout.strip())
        elif process.stdout:  # Log brief stdout confirmation if successful
            log("Stdout (first 200 chars): " + process.stdout.strip()[:200] + "...")
        if process.stderr:
            log("Stderr:\n" + process.stderr.strip(), level="warning")

        # Check exit code based on expectation
        success = expect_failure == (process.returncode != 0)  # XOR operation
        expected = "non-zero" if expect_failure else "zero"

        if not success:
            log(
                f"Command failed! Expected exit code {expected}, got {process.returncode}.",
                level="error",
            )
            return False, process.returncode
        else:
            log("Command finished successfully (as expected).")
            return True, process.returncode

    except Exception as e:
        log(f"Failed to execute command: {command_str}\nError: {e}", level="error")
        return False, 1


def get_changed_files(base_sha, head_sha, patterns):
    """Gets changed files between two commits matching given patterns."""
    if not patterns:
        return []
    # Quote each pattern individually before joining
    pattern_args = " ".join([shlex.quote(p) for p in patterns])
    cmd = f"git diff --name-only --diff-filter=ACMRTUXB {base_sha} {head_sha} -- {pattern_args}"
    log(f"Finding changed files: {cmd}")
    try:
        # Use shell=False here as we are controlling the command structure
        process = subprocess.run(
            cmd,
            shell=True,  # Use shell=True as complex patterns might need shell expansion
            check=True,  # Fail if git diff errors
            capture_output=True,
            text=True,
            cwd=GITHUB_WORKSPACE,
        )
        files = process.stdout.strip().splitlines()
        return [f for f in files if f]  # Filter out empty lines
    except subprocess.CalledProcessError as e:
        log(f"Error running git diff: {e}", level="error")
        log(f"Stderr: {e.stderr}", level="error")
        return None  # Indicate error
    except Exception as e:
        log(f"Unexpected error in get_changed_files: {e}", level="error")
        return None


def validate_module(module: dict, merge_base: str, labels: list[str] = None) -> bool:
    if not labels:
        labels = []

    require_regression = "require-regression" in labels
    skip_base_must_fail = "skip-base-must-fail" in labels

    module_name = module.get("language_name")
    log(f"\n--- Validating Module: {module_name} ---")

    # Get changed files
    code_patterns = module.get("code_patterns", [])
    test_patterns = module.get("test_patterns", [])
    changed_code_files = get_changed_files(merge_base, HEAD_SHA, code_patterns)
    changed_test_files = get_changed_files(merge_base, HEAD_SHA, test_patterns)

    log(f"Changed Code Files: {changed_code_files}")
    log(f"Changed Test Files: {changed_test_files}")

    has_code_changes = bool(changed_code_files)
    has_test_changes = bool(changed_test_files)

    if not has_code_changes and not has_test_changes:
        if not require_regression:
            log("No code or test changes detected. Skipping module...")
            return True
        else:
            log("No code or test changes detected, however regression is required")

    # Rule 1: ALL code changes must have tests
    if has_code_changes and not has_test_changes:
        log(f"Detected changed code files but no tests.", level="error")
        return False
    
    # Code from here onwards will only run if there are test changes or if regression is required

    # Run setup commands for this module
    setup_commands = module.get("setup_commands", [])
    if setup_commands:
        log(f"Running setup commands for {module_name}...")
        for cmd in setup_commands:
            success, _ = run_command(cmd)
            if not success:
                log(
                    f"Setup failed for module {module_name}. Skipping further validation.",
                    level="error",
                )
                return False
    else:
        log("No setup commands defined.")

    # Rule 2: At least *1* test file must fail when run on base branch
    # Skip if PR includes label
    if not skip_base_must_fail and has_test_changes:
        code_files_to_checkout = " ".join([shlex.quote(f) for f in changed_code_files])
        log(
            f"Temporarily checking out base version of CODE files: {changed_code_files}"
        )
        success_checkout_base, _ = run_command(
            f"git checkout {BASE_SHA} -- {code_files_to_checkout}"
        )
        if not success_checkout_base:
            log("Checking files to base failed.", level="error")
            return False

        run_new_cmd_template: str = module.get("run_new_tests_command")
        if not run_new_cmd_template:
            log(
                "`run_new_tests_command` not defined.",
                level="error",
            )
            return False

        test_files_list_str = " ".join([shlex.quote(f) for f in changed_test_files])
        run_new_cmd = run_new_cmd_template.replace("{TEST_FILES}", test_files_list_str)
        log(
            f"Running new tests ({changed_test_files}) for {module_name} against base code version (expecting failure)..."
        )
        # Expect failure (non-zero exit code) from test runner
        success, exit_code = run_command(run_new_cmd, expect_failure=True)
        if not success:
            if exit_code == 0:
                log(
                    f"Expected new tests to FAIL on base code, but they PASSED (Exit code 0). This PR may not need the code changes.",
                    level="error",
                )
            else:
                log(
                    f"Test command failed unexpectedly during 'new tests on base' run (Exit code {exit_code}).",
                    level="error",
                )
            return False
        log("SUCCESS: At least 1 test file fails on base")

        # Restore PR version of files that were checked out from base
        log(f"Restoring PR version of code files ({HEAD_SHA}): {changed_code_files}")
        success_checkout_head, _ = run_command(
            f"git checkout {HEAD_SHA} -- {code_files_to_checkout}"
        )
        if not success_checkout_head:
            log(
                "CRITICAL: Failed to restore PR version of code files after testing on base!",
                level="error",
            )
            return False

    # Rule 3: ALL test must pass on PR branch
    run_all_cmd: str = module.get("run_all_tests_command")
    if not run_all_cmd:
        log("`run_all_tests_command` not defined.", level="error")
        return False

    log(f"Running all tests for {module_name} on PR branch code (expecting success)...")
    success, exit_code = run_command(
        run_all_cmd, cwd=GITHUB_WORKSPACE, expect_failure=False
    )

    if not success:
        log(
            f"Some tests failed on PR branch (Exit code {exit_code}).",
            level="error",
        )
        return False
    else:
        log(f"SUCCESS: All tests passed on PR branch.")

    log(f"--- Finished Module: {module_name} ---")
    return True


def main():
    if not all([BASE_SHA, HEAD_SHA, LABELS]):
        log(
            "Missing required environment variables (BASE_SHA, HEAD_SHA, LABELS).",
            level="error",
        )
        sys.exit(1)

    labels = [label["name"].lower() for label in json.loads(LABELS)]

    skip_test_validation = "skip-test-validation" in labels

    if skip_test_validation:
        log("This PR does not require test validation. Skipping...")
        sys.exit(0)

    try:
        with open(CONFIG_PATH) as file:
            config: dict = json.load(file)
        if "modules" not in config or not isinstance(config["modules"], list):
            log(
                "Invalid configuration: 'modules' array not found or not a list.",
                level="error",
            )
            sys.exit(1)
    except json.JSONDecodeError as e:
        log(f"Failed to parse configuration JSON: {e}", level="error")
        sys.exit(1)

    log(f"Base SHA: {BASE_SHA}")
    log(f"Head SHA: {HEAD_SHA}")
    log(f"Labels: {labels}")

    # Add workspace to git safe directories (important for actions runner)
    run_command(f"git config --global --add safe.directory {GITHUB_WORKSPACE}")

    # Calculate merge base ONCE
    merge_base_cmd = f"git merge-base {HEAD_SHA} {BASE_SHA}"
    try:
        process = subprocess.run(
            merge_base_cmd,
            shell=True,
            check=True,
            capture_output=True,
            text=True,
            cwd=GITHUB_WORKSPACE,
        )
        merge_base = process.stdout.strip()
        if not merge_base:
            raise ValueError("Merge base calculation returned empty.")
        log(f"Calculated Merge Base: {merge_base}")
    except (subprocess.CalledProcessError, ValueError) as e:
        log(f"Error calculating merge base: {e}", level="error")
        sys.exit(1)

    for module in config["modules"]:
        success = validate_module(module=module, merge_base=merge_base, labels=labels)
        if not success:
            log(f"FAILURE: Module {module.get('language_name')} failed.")
            sys.exit(1)

    log("SUCCESS: All modules passed")
    sys.exit(0)


if __name__ == "__main__":
    main()
