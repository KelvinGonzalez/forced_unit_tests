# test_calculator.py
import pytest
from calculator import add  # Import the specific function


def test_add_positive_numbers():
    """Tests adding two positive numbers."""
    assert add(2, 3) == 5


def test_add_zero():
    """Tests adding zero."""
    assert add(5, 0) == 5
    assert add(0, 7) == 7
    assert add(0, 0) == 0
