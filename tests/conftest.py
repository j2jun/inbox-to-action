from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

ROOT = Path(__file__).resolve().parent.parent
SAMPLES = ROOT / "samples"
TZ = ZoneInfo("America/Los_Angeles")
NOW = datetime(2026, 11, 2, 9, 30, tzinfo=TZ)


def sample_path(prefix: str) -> Path:
    return next(SAMPLES.glob(f"{prefix}_*.eml"))


@pytest.fixture
def load():
    from inbox_to_action.parse import parse_eml
    return lambda prefix: parse_eml(sample_path(prefix).read_bytes())
