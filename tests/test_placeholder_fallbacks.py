"""A placeholder's fallback may read other placeholders: ``${env:RUN_DIR,${env:HOME}/runs}``.

The fallback is author text, interpolated like any author string — ``${...}`` placeholders and bare ``$VAR`` — and
only when the placeholder misses. Before, the first ``}`` ended the placeholder whatever opened before it, so a nested
fallback came out as silent garbage: ``${env:A,${env:B}}/x`` resolved to ``${env:B}/x`` with A unset and to
``<A>/x}`` with A set.
"""

import pytest

from confluid import load
from confluid.exceptions import ConfigurationError

A, B, C = "CONFLUID_TEST_FALLBACK_A", "CONFLUID_TEST_FALLBACK_B", "CONFLUID_TEST_FALLBACK_C"


@pytest.fixture(autouse=True)
def _unset(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (A, B, C):
        monkeypatch.delenv(name, raising=False)


def test_an_unset_variable_falls_back_to_another_variable_and_text(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(B, "/store")
    assert load(f"p: ${{env:{A},${{env:{B}}}/runs}}/waivefront")["p"] == "/store/runs/waivefront"


def test_a_set_variable_never_reads_its_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(A, "/runs")  # B unset: the fallback would not resolve, and is never asked
    assert load(f"p: ${{env:{A},${{env:{B}}}/runs}}/waivefront")["p"] == "/runs/waivefront"


def test_a_whole_fallback_placeholder_keeps_its_type(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(B, "8080")
    value = load(f"p: ${{env:{A},${{env:{B}}}}}")["p"]
    assert value == 8080 and isinstance(value, int)


def test_a_fallback_nests_as_deep_as_it_is_written(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(C, "/c")
    assert load(f"p: ${{env:{A},${{env:{B},${{env:{C}}}/b}}/a}}")["p"] == "/c/b/a"


def test_a_fallback_reads_a_config_key(monkeypatch: pytest.MonkeyPatch) -> None:
    assert load(f"train: {{root: /r}}\np: ${{env:{A},${{train.root}}/runs}}")["p"] == "/r/runs"


def test_the_plain_name_and_the_config_key_forms_take_a_nested_fallback_too(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(B, "/store")
    assert load(f"p: ${{{A}:${{{B}}}/x}}")["p"] == "/store/x"
    assert load(f"train: {{}}\np: ${{train.missing:${{env:{B}}}}}")["p"] == "/store"


def test_a_bare_variable_in_a_fallback_is_expanded_as_author_text(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(B, "/store")
    assert load(f"p: ${{env:{A},${B}/runs}}/waivefront")["p"] == "/store/runs/waivefront"


def test_a_fallback_that_does_not_resolve_leaves_the_whole_placeholder_as_written() -> None:
    text = f"${{env:{A},${{env:{B}}}/runs}}/waivefront"
    assert load(f"p: {text}")["p"] == text


def test_a_fallback_without_a_placeholder_is_unchanged() -> None:
    value = load(f"p: ${{env:{A},8080}}")["p"]
    assert value == 8080 and isinstance(value, int)
    # no `${` inside: the first `}` still ends the placeholder, the rest is text
    assert load(f'p: "${{env:{A},x}}y}}"')["p"] == "xy}"


def test_a_value_substituted_into_a_fallback_is_never_scanned_again(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(B, f"${{env:{C}}}")
    monkeypatch.setenv(C, "/c")
    assert load(f"p: ${{env:{A},${{env:{B}}}/x}}")["p"] == f"${{env:{C}}}/x"


def test_a_reference_is_refused_as_a_fallback() -> None:
    with pytest.raises(ConfigurationError, match="reference"):
        load(f"x: 1\np: ${{env:{A},${{ref:x}}}}")
    with pytest.raises(ConfigurationError, match="reference"):
        load(f"x: 1\np: ${{env:{A},${{ref:x}}/y}}")
