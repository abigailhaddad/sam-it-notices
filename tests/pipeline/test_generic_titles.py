import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from build_rfp_signals import _filter_personnel


def test_employees_is_generic_title():
    roles = [{"title": "Employees", "min_years_experience": 5},
             {"title": "Vendors", "min_years_experience": 5},
             {"title": "Contractor(s)", "min_years_experience": 5},
             {"title": "Senior Developer", "min_years_experience": 5}]
    assert [r["title"] for r in _filter_personnel(roles)] == ["Senior Developer"]
