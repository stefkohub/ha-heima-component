from custom_components.heima.runtime.policy import resolve_house_state


def test_house_state_priority_vacation_wins():
    state, reason = resolve_house_state(
        anyone_home=True,
        vacation_mode=True,
        guest_mode=True,
        sleep_window=True,
        relax_mode=True,
        work_window=True,
    )
    assert state == "vacation"
    assert reason == "vacation_mode"


def test_house_state_away_when_no_presence():
    state, reason = resolve_house_state(
        anyone_home=False,
        vacation_mode=False,
        guest_mode=False,
        sleep_window=False,
        relax_mode=False,
        work_window=False,
    )
    assert state == "away"
    assert reason == "no_presence"


def test_house_state_home_default():
    state, reason = resolve_house_state(
        anyone_home=True,
        vacation_mode=False,
        guest_mode=False,
        sleep_window=False,
        relax_mode=False,
        work_window=False,
    )
    assert state == "home"
    assert reason == "default"
