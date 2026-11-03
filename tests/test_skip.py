import pytest

from conftest import NOW
from inbox_to_action.skip import skip_reason


@pytest.mark.parametrize("prefix,reason", [
    ("21", "newsletter"), ("23", "marketing"), ("22", "receipt"),
    ("12", None), ("13", None), ("14", None), ("11", None),
])
def test_skip_reasons(load, prefix, reason):
    assert skip_reason(load(prefix), NOW) == reason
