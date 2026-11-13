from orbit_api.limits import DEFAULT_RPM, SCOPE


def test_default_is_50_per_key():
    assert DEFAULT_RPM == 50
    assert SCOPE == "per_key"
