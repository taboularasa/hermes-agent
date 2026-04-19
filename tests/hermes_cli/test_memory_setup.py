from hermes_cli.memory_setup import _get_available_providers


def test_get_available_providers_includes_zep():
    matches = [
        (name, hint, provider)
        for name, hint, provider in _get_available_providers()
        if name == "zep"
    ]

    assert matches
    name, hint, provider = matches[0]
    assert name == "zep"
    assert hint == "API key / local"
    assert provider.name == "zep"
