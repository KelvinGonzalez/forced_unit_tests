# .github/scripts/validate_pr.py
import os
import sys
import subprocess
import json
import shlex

# --- Configuration ---
CONFIG_PATH = ".github/project_config.json"
# Get required info from environment variables set by the workflow
CONFIG_JSON_STR = os.environ.get("CONFIG_JSON")
BASE_SHA = os.environ.get("BASE_SHA")
HEAD_SHA = os.environ.get("HEAD_SHA")
REGRESSION_REQUIRED = os.environ.get("REGRESSION_REQUIRED", "false").lower() == "true"
GITHUB_WORKSPACE = os.environ.get("GITHUB_WORKSPACE", ".")

# --- Helper Functions ---


def log(message, level="info"):
    """Prints messages, mapping levels to GitHub Actions commands."""
    if level == "error":
        print(f"::error::{message}", file=sys.stderr)
    elif level == "warning":
        print(f"::warning::{message}", file=sys.stdout)
    else:
        print(message, file=sys.stdout)


def run_command(command_str, cwd=None, expect_failure=False, env=None):
    """Runs a shell command, captures output, and checks exit code."""
    log(f"Executing: {command_str}")
    effective_env = {**os.environ, **(env or {})}  # Merge environments
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
            env=effective_env,
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
        success = (expect_failure and process.returncode != 0) or (
            not expect_failure and process.returncode == 0
        )
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


# --- (Optional) Advanced Helper Functions ---
# def translate_paths_to_classes(file_paths):
#     """
#     Placeholder function to translate Java file paths to class names.
#     This needs to be adapted based on the project's structure (Maven/Gradle).
#     Example assumes standard Maven structure. Uncomment and modify if using {TEST_CLASSES}.
#     """
#     log("Translating file paths to class names (example implementation)...", level="warning")
#     classes = []
#     for f_path in file_paths:
#         if "src/test/java/" in f_path and f_path.endswith(".java"):
#             try:
#                 # Extract path relative to src/test/java, remove .java, replace / with .
#                 class_path = f_path.split("src/test/java/")[-1].replace(".java", "").replace("/", ".")
#                 classes.append(class_path)
#             except Exception as e:
#                  log(f"Could not translate path: {f_path}, Error: {e}", level="warning")
#         # Add logic for other languages/structures if needed
#     return ",".join(classes)


# --- Main Logic ---
def main():
    if not all([CONFIG_JSON_STR, BASE_SHA, HEAD_SHA]):
        log(
            "Missing required environment variables (CONFIG_JSON, BASE_SHA, HEAD_SHA).",
            level="error",
        )
        sys.exit(1)

    try:
        config = json.loads(CONFIG_JSON_STR)
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
    log(f"Regression Required: {REGRESSION_REQUIRED}")

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

    # Global flags to track overall validation status
    overall_failure = False
    any_code_changed_without_tests = False
    any_new_tests_found = False
    at_least_one_new_test_failed_on_main = False
    all_required_regressions_passed = True  # Assume true until proven otherwise

    # --- Module Iteration ---
    for i, module in enumerate(config.get("modules", [])):
        module_name = module.get("language_name", f"Module {i+1}")
        log(f"\n--- Validating Module: {module_name} ---")
        module_failed_this_run = False  # Track failure within this module iteration

        # Run setup commands for this module
        setup_commands = module.get("setup_commands", [])
        if setup_commands:
            log(f"Running setup commands for {module_name}...")
            for cmd in setup_commands:
                success, _ = run_command(cmd, cwd=GITHUB_WORKSPACE)
                if not success:
                    log(
                        f"Setup failed for module {module_name}. Skipping further validation.",
                        level="error",
                    )
                    overall_failure = True
                    module_failed_this_run = True
                    break  # Stop setup for this module
            if module_failed_this_run:
                continue  # Move to the next module
        else:
            log("No setup commands defined.")

        # Identify changed files for THIS module
        code_patterns = module.get("code_patterns", [])
        test_patterns = module.get("test_patterns", [])

        changed_code_files = get_changed_files(merge_base, HEAD_SHA, code_patterns)
        changed_test_files = get_changed_files(merge_base, HEAD_SHA, test_patterns)

        if changed_code_files is None or changed_test_files is None:
            log("Failed to get changed files. Skipping module.", level="error")
            overall_failure = True
            continue

        log(f"Changed Code Files: {changed_code_files or '(None)'}")
        log(f"Changed Test Files: {changed_test_files or '(None)'}")

        module_has_code_changes = bool(changed_code_files)
        module_has_test_changes = bool(changed_test_files)
        if module_has_test_changes:
            any_new_tests_found = True

        # Rule 2: Code changed without tests?
        if module_has_code_changes and not module_has_test_changes:
            log(
                f"[{module_name}] Code files changed, but no corresponding test files in this module were changed.",
                level="error",
            )
            any_code_changed_without_tests = True
            overall_failure = True
            module_failed_this_run = True
            # Note: We still might need to run regression tests if required globally

        # Rule 3a: Run NEW tests against BASE (main) - Expect Failure
        if module_has_test_changes and not module_failed_this_run:
            log(f"[{module_name}] Checking new tests against base code ({BASE_SHA})...")

            # Prepare list of only changed CODE files to checkout from base
            code_files_to_checkout_str = ""
            if changed_code_files:  # Only checkout if code files actually changed
                code_files_to_checkout_str = " ".join(
                    [shlex.quote(f) for f in changed_code_files]
                )
                log(
                    f"Temporarily checking out base version of CODE files: {changed_code_files}"
                )
                success_checkout_base, _ = run_command(
                    f"git checkout {BASE_SHA} -- {code_files_to_checkout_str}",
                    cwd=GITHUB_WORKSPACE,
                )
            else:
                # If only test files changed, no need to checkout base code files
                log(
                    "No code files changed in this commit for this module. Running new tests against current PR code (as base)."
                )
                success_checkout_base = (
                    True  # Simulate success as no checkout was needed
                )

            if not success_checkout_base:
                log(
                    "Failed to checkout base version of code files. Skipping 'new tests on base' check.",
                    level="error",
                )
                overall_failure = True
                module_failed_this_run = True
                # Attempt to restore HEAD anyway, just in case some files were checked out partially
                if code_files_to_checkout_str:
                    run_command(
                        f"git checkout {HEAD_SHA} -- {code_files_to_checkout_str}",
                        cwd=GITHUB_WORKSPACE,
                    )  # Best effort restore
            else:
                # Now run the new test files (which are still the PR version)
                # against the potentially modified code files (now base version)
                run_new_cmd_template = module.get("run_new_tests_command")
                if not run_new_cmd_template:
                    log(
                        "`run_new_tests_command` not defined. Skipping 'new tests on base' check.",
                        level="warning",
                    )
                else:
                    test_files_list_str = " ".join(
                        [shlex.quote(f) for f in changed_test_files]
                    )
                    run_new_cmd = run_new_cmd_template.replace(
                        "{TEST_FILES}", test_files_list_str
                    )

                    # --- Advanced Placeholder Handling (Example: {TEST_CLASSES}) ---
                    # If your command needs something other than file paths (like Java class names),
                    # you can add logic here to compute it and replace a different placeholder.
                    # Example (uncomment and adapt if needed):
                    # if "{TEST_CLASSES}" in run_new_cmd:
                    #     test_classes_list_str = translate_paths_to_classes(changed_test_files)
                    #     run_new_cmd = run_new_cmd.replace("{TEST_CLASSES}", test_classes_list_str)
                    #     log(f"Using translated test classes: {test_classes_list_str}")
                    # --- End Advanced Placeholder Handling ---

                    log(
                        f"Running new tests ({changed_test_files}) for {module_name} against base code version (expecting failure)..."
                    )
                    # Expect failure (non-zero exit code) from test runner
                    success, exit_code = run_command(
                        run_new_cmd, cwd=GITHUB_WORKSPACE, expect_failure=True
                    )

                    if success:  # Command returned non-zero as expected
                        log(
                            f"Success: [{module_name}] New tests failed on base code as expected (Exit code {exit_code})."
                        )
                        at_least_one_new_test_failed_on_main = True
                    else:  # Command returned zero (tests PASSED unexpectedly) or failed to run
                        # Treat exit code 0 as the failure condition for this check
                        if exit_code == 0:
                            log(
                                f"[{module_name}] Expected new tests to FAIL on base code, but they PASSED (Exit code 0). This PR may not need the code changes.",
                                level="error",
                            )
                        else:
                            log(
                                f"[{module_name}] Test command failed unexpectedly during 'new tests on base' run (Exit code {exit_code}).",
                                level="error",
                            )
                        overall_failure = True
                        module_failed_this_run = True

                # Restore PR version of CODE files that were checked out from base
                if (
                    code_files_to_checkout_str
                ):  # Only restore if base files were checked out
                    log(
                        f"Restoring PR version of code files ({HEAD_SHA}): {changed_code_files}"
                    )
                    success_checkout_head, _ = run_command(
                        f"git checkout {HEAD_SHA} -- {code_files_to_checkout_str}",
                        cwd=GITHUB_WORKSPACE,
                    )
                    if not success_checkout_head:
                        log(
                            "CRITICAL: Failed to restore PR version of code files after testing on base!",
                            level="error",
                        )
                        overall_failure = True
                        module_failed_this_run = True  # Critical failure

        # Rule 3b / Rule 4: Run ALL tests against PR branch - Expect Success
        run_all_cmd = module.get("run_all_tests_command")
        # Determine if we need to run all tests for this module
        should_run_all_tests = (
            module_has_test_changes or REGRESSION_REQUIRED
        ) and run_all_cmd

        if (
            should_run_all_tests and not module_failed_this_run
        ):  # Only run if module hasn't failed critically yet
            log(
                f"Running ALL tests for {module_name} on PR branch code (expecting success)..."
            )
            success, exit_code = run_command(
                run_all_cmd, cwd=GITHUB_WORKSPACE, expect_failure=False
            )

            if not success:
                log(
                    f"[{module_name}] All tests failed on PR branch (Exit code {exit_code}).",
                    level="error",
                )
                overall_failure = True
                module_failed_this_run = True
                # If regression was required globally, this fails the regression check
                if REGRESSION_REQUIRED:
                    all_required_regressions_passed = False
            else:
                log(f"Success: [{module_name}] All tests passed on PR branch.")

        elif not run_all_cmd and (module_has_test_changes or REGRESSION_REQUIRED):
            log(
                f"[{module_name}] Skipping 'Run ALL tests' because `run_all_tests_command` is not defined.",
                level="warning",
            )

        # Log if skipped because no changes and no regression required
        elif not should_run_all_tests:
            log(
                f"[{module_name}] Skipping 'Run ALL tests' (no relevant changes and regression not required)."
            )

        log(f"--- Finished Module: {module_name} ---")
        # End of module loop

    # --- Final Evaluation ---
    log("\n--- Overall Validation Summary ---")

    final_message = "PASSED"

    if any_code_changed_without_tests:
        log(
            "FAILURE: Code changed in some modules without corresponding tests.",
            level="error",
        )
        final_message = "FAILED"
        # overall_failure should already be true

    # This check is only meaningful if new tests were actually found somewhere
    if any_new_tests_found and not at_least_one_new_test_failed_on_main:
        # Check if the reason was simply that all modules with new tests failed *before* this check could run
        if (
            not overall_failure
        ):  # Only flag this as the primary error if no other critical error occurred
            log(
                "FAILURE: New/modified tests were found, but NONE failed when run against the base 'main' branch code.",
                level="error",
            )
            final_message = "FAILED"
            overall_failure = True  # Ensure overall failure is set

    if REGRESSION_REQUIRED and not all_required_regressions_passed:
        log(
            "FAILURE: Regression testing was required, but 'Run ALL tests' failed in at least one module.",
            level="error",
        )
        final_message = "FAILED"
        # overall_failure should already be true

    # Final check on overall_failure flag which aggregates failures from all steps
    if overall_failure and final_message == "PASSED":
        log("FAILURE: One or more validation steps failed.", level="error")
        final_message = "FAILED"

    log(f"Final Result: {final_message}")

    if final_message == "FAILED":
        sys.exit(1)
    else:
        sys.exit(0)


if __name__ == "__main__":
    main()
