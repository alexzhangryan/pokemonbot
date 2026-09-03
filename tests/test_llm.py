"""Implementation C's backend: the transport, the cache, and the parse.

Everything here is dex-free and network-free on purpose. `champions.search.llm`
imports neither the dex nor numpy, so the prompt-build, the reply-parse and the
cache can be tested without the built simulator -- which is the same property
that lets `scripts/llm_smoke.py` run the whole decision loop against a bare
Ollama. The one thing not tested here is a live model, because a test that needs
a running server is a test that does not run in CI.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from champions.search import llm


class FakeClient:
    """An `LLMClient` that returns a scripted reply and counts its calls."""

    name = "fake:test"

    def __init__(self, reply: str = "[0]") -> None:
        self.reply = reply
        self.calls = 0

    def complete(self, prompt: str) -> str:
        self.calls += 1
        self.last_prompt = prompt
        return self.reply


class RaisingClient:
    name = "fake:raises"

    def complete(self, prompt: str) -> str:
        raise llm.LLMError("boom")


# -- parse_ranking -----------------------------------------------------------


@pytest.mark.parametrize(
    "text, n, expected",
    [
        ("[1,0,2]", 3, [1, 0, 2]),
        ("[2, 1, 0]", 3, [2, 1, 0]),
        # A <think> block is stripped before parsing, numbers inside it ignored.
        ("<think>maybe 9 then 8</think>[2,1,0]", 3, [2, 1, 0]),
        # A fenced code block around the array.
        ("```json\n[0, 2, 1]\n```", 3, [0, 2, 1]),
        # Prose with no array falls back to the integers in order.
        ("Best is 1, then 0, then 2.", 3, [1, 0, 2]),
        # Out-of-range indices are dropped, not clamped; duplicates collapse.
        ("[5, 1, 99, 1, 0]", 3, [1, 0]),
        # Nothing parseable is an empty ranking, which the caller pads.
        ("no numbers at all", 3, []),
        ("", 3, []),
    ],
)
def test_parse_ranking(text: str, n: int, expected: list[int]) -> None:
    assert llm.parse_ranking(text, n) == expected


def test_parse_ranking_takes_the_first_array_with_content() -> None:
    # An empty array is skipped in favour of the next one that has integers.
    assert llm.parse_ranking("first [] then [1, 0]", 2) == [1, 0]


# -- order_indices -----------------------------------------------------------


def test_order_indices_completes_a_partial_ranking() -> None:
    # The model ranked only index 2; the rest follow in natural order.
    assert llm.order_indices([2], 4) == [2, 0, 1, 3]


def test_order_indices_empty_is_natural_order() -> None:
    assert llm.order_indices([], 3) == [0, 1, 2]


def test_order_indices_is_a_full_permutation() -> None:
    order = llm.order_indices([3, 1], 5)
    assert sorted(order) == [0, 1, 2, 3, 4]
    assert order[:2] == [3, 1]


# -- build_prompt ------------------------------------------------------------


def test_build_prompt_numbers_the_candidates() -> None:
    prompt = llm.build_prompt("Turn 3.", ["Protect (idle)", "Flamethrower 46%"])
    assert "Turn 3." in prompt
    assert "0: Protect (idle)" in prompt
    assert "1: Flamethrower 46%" in prompt
    assert "JSON array" in prompt


# -- rank --------------------------------------------------------------------


def test_rank_returns_full_permutation() -> None:
    client = FakeClient("[1, 0]")
    assert llm.rank(client, "hdr", ["a", "b"]) == [1, 0]


def test_rank_pads_a_short_reply() -> None:
    client = FakeClient("[2]")
    assert llm.rank(client, "hdr", ["a", "b", "c"]) == [2, 0, 1]


def test_rank_propagates_backend_errors() -> None:
    with pytest.raises(llm.LLMError):
        llm.rank(RaisingClient(), "hdr", ["a", "b"])


# -- CachingClient -----------------------------------------------------------


def test_caching_client_calls_inner_once(tmp_path: Path) -> None:
    inner = FakeClient("[0]")
    client = llm.CachingClient(inner, tmp_path)

    first = client.complete("a prompt")
    second = client.complete("a prompt")

    assert first == second == "[0]"
    assert inner.calls == 1, "the second call should have hit the cache"
    assert list(tmp_path.glob("*.txt")), "a cache file should have been written"


def test_caching_client_keys_on_prompt(tmp_path: Path) -> None:
    inner = FakeClient("[0]")
    client = llm.CachingClient(inner, tmp_path)

    client.complete("prompt one")
    client.complete("prompt two")

    assert inner.calls == 2
    assert len(list(tmp_path.glob("*.txt"))) == 2


# -- client_from_env ---------------------------------------------------------


def test_client_from_env_unknown_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CHAMPIONS_LLM_BACKEND", "openai")
    with pytest.raises(llm.LLMError):
        llm.client_from_env()


def test_client_from_env_defaults_to_cached_ollama(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CHAMPIONS_LLM_BACKEND", raising=False)
    client = llm.client_from_env()
    assert isinstance(client, llm.CachingClient)
    assert client.name.startswith("ollama:")


def test_client_from_env_can_disable_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CHAMPIONS_LLM_BACKEND", raising=False)
    client = llm.client_from_env(cache=False)
    assert isinstance(client, llm.OllamaClient)


def test_client_from_env_honours_model_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CHAMPIONS_LLM_BACKEND", raising=False)
    monkeypatch.setenv("CHAMPIONS_LLM_MODEL", "some-model:tag")
    client = llm.client_from_env(cache=False)
    assert client.name == "ollama:some-model:tag"


# -- ensure_reachable --------------------------------------------------------


class _FakeResponse:
    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        pass

    def json(self) -> dict:
        return self._payload


def test_ensure_reachable_is_a_noop_for_a_non_ollama_client() -> None:
    # A test fake has no server; ensure_reachable must not try to reach one.
    llm.ensure_reachable(FakeClient())


def test_ensure_reachable_raises_when_the_server_is_down(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*args: object, **kwargs: object) -> None:
        raise llm.httpx.ConnectError("refused")

    monkeypatch.setattr(llm.httpx, "get", boom)
    with pytest.raises(llm.LLMError, match="not reachable"):
        llm.ensure_reachable(llm.OllamaClient(model="qwen2.5:3b-instruct"))


def test_ensure_reachable_raises_when_the_model_is_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        llm.httpx, "get", lambda *a, **k: _FakeResponse({"models": [{"name": "other:latest"}]})
    )
    with pytest.raises(llm.LLMError, match="not pulled"):
        llm.ensure_reachable(llm.OllamaClient(model="qwen2.5:3b-instruct"))


def test_ensure_reachable_passes_when_the_model_is_present(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        llm.httpx,
        "get",
        lambda *a, **k: _FakeResponse({"models": [{"name": "qwen2.5:3b-instruct"}]}),
    )
    llm.ensure_reachable(llm.CachingClient(llm.OllamaClient(model="qwen2.5:3b-instruct")))
