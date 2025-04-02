# test_calculator.py
import pytest

# Import the specific functions - NOW INCLUDES subtract
from calculator import add, subtract, multiply

# We can also just import the module
import calculator


# --- Existing Tests ---
def test_add_positive_numbers():
    """Tests adding two positive numbers."""
    assert add(2, 3) == 5


def test_add_zero():
    """Tests adding zero."""
    assert add(5, 0) == 5
    assert add(0, 7) == 7
    assert add(0, 0) == 0


# --- NEW TEST for Subtract (Intentionally flawed for demo) ---
def test_calculator_module_exists_and_can_subtract():
    """
    Checks if the module exists (passes on main) AND
    tests subtraction (passes only on branch).
    THIS IS A BAD TEST designed to fail the workflow rule initially.
    """
    # This part passes even on main (after Merge #1)
    assert calculator is not None
    assert hasattr(calculator, "add")  # Check if add exists (it does on main)

    # This part ONLY passes on the branch with the subtract function
    assert subtract(10, 3) == 7

    # Passes in both base and main, means test is useless
    assert subtract(2, 4) == -2


def test_calculator_multiply():
    assert multiply(3, 6) == 18
    assert multiply(-2, 6) == -12
