from types import SimpleNamespace

from custom_components.heima.models import HeimaOptions
from custom_components.heima.runtime.snapshot import DecisionSnapshot


def test_options_defaults():
    entry = SimpleNamespace(options={})
    options = HeimaOptions.from_entry(entry)
    assert options.engine_enabled is True
    assert options.timezone == "UTC"
    assert options.language == "en"


def test_snapshot_empty_defaults():
    snap = DecisionSnapshot.empty()
    assert snap.house_state == "unknown"
    assert snap.anyone_home is False
    assert snap.people_count == 0
    assert snap.heating_intent == "auto"
