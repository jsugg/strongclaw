from clawops.hypermemory.noise import is_noise


def test_noise_denial_phrase() -> None:
    assert is_noise("I don't remember any matching memory.")


def test_noise_greeting() -> None:
    assert is_noise("Hello")


def test_noise_short_text() -> None:
    assert is_noise("short")


def test_noise_valid_fact() -> None:
    assert not is_noise("Deploy approvals require two reviewers.")


def test_noise_json_blob() -> None:
    assert is_noise('{"results": []}')
