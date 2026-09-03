"""Implementation C's backend: a swappable language-model client.

`docs/04-decision-engine.md` section 3 names three candidate providers. C is the
language model: the engine computes each candidate's consequences first -- damage,
speed order, knockout thresholds -- and the model only *selects among* candidates
that already carry their computed numbers. It is never asked to do the arithmetic
it is bad at, which is the arrangement that made PokeLLMon and PokeChamp
unreliable and the one section 3 deliberately inverts.

This module is only the transport to a model and the parse of its answer back
into an ordering. The consequences are computed one layer up in
`champions.search.language`, which is why nothing here imports the dex, the
damage layer or numpy: the whole decision loop -- prompt, call, parse -- can be
exercised end to end without the built simulator, which is what
`scripts/llm_smoke.py` does.

## Ollama is a mock, on purpose

A real provider costs money per call, and running an unproven pipeline against a
paid API is the expense this defers. Ollama runs a local model for free behind
the same HTTP shape, so the pipeline can be validated for correctness before a
cent is spent on it. A paid provider drops in behind `LLMClient` with no change
to `language.py`; `client_from_env` is where the switch will live.

## Determinism

`CLAUDE.md` requires the agent to be reproducible from a seed, and a language
model is not deterministic the way an LP solve is. Two things narrow the gap.
The request pins `temperature = 0` and a fixed `seed`, which is as close to
deterministic as the backend offers. And `CachingClient` stores each reply
keyed by the exact prompt, so once a position has been seen its ranking is fixed
on disk and every rerun of the guard reads the same answer rather than paying
for -- and possibly varying on -- a second call. The cache is what makes a
reported discarded-mass number reproducible; the caveat that a *cold* cache can
still vary across backends is real and is stated rather than hidden.
"""

from __future__ import annotations

import hashlib
import os
import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable

import httpx

#: The default local backend. Only Ollama is wired in; a paid provider is
#: deferred until the pipeline is validated (see the module docstring and
#: `docs/DECISIONS.md` D68).
DEFAULT_BACKEND = "ollama"

#: The default mock model. A small modern *instruct* model rather than a
#: reasoning one: the task is to order a list that already carries every number,
#: so chain-of-thought buys nothing and costs latency, which matters on a CPU-only
#: box. Overridable with `CHAMPIONS_LLM_MODEL`; the GPU box will point this at
#: something larger.
DEFAULT_MODEL = "qwen2.5:3b-instruct"

#: Ollama's default address. Overridable with `OLLAMA_HOST`.
DEFAULT_HOST = "http://localhost:11434"

#: Long, because a model cold-loading on CPU can take tens of seconds, and a
#: candidate selection that times out is worse than one that is slow -- the
#: provider falls back to the heuristic ordering, which silently discards the
#: whole point of measuring C.
DEFAULT_TIMEOUT = 180.0

#: Where replies are cached, keyed by `(model, prompt)`. Gitignored, like the
#: other generated data under `data/`.
CACHE_DIR = Path("data/llm")


class LLMError(RuntimeError):
    """A backend call failed. Raised rather than swallowed here so the provider
    that catches it can choose to fall back, and a smoke run can choose to
    surface it."""


@runtime_checkable
class LLMClient(Protocol):
    """The one thing `language.py` needs from a model: a prompt in, text out.

    `name` travels onto the trace so a reader can tell which model produced a
    ranking, the same way `policy_provider` records which provider produced a
    candidate set.
    """

    name: str

    def complete(self, prompt: str) -> str: ...  # pragma: no cover


@dataclass
class OllamaClient:
    """A local model over Ollama's HTTP API.

    Uses `/api/generate` with streaming off: one prompt, one reply, no session
    state. `temperature` and `seed` are pinned for the determinism the module
    docstring describes.
    """

    model: str = DEFAULT_MODEL
    host: str = DEFAULT_HOST
    temperature: float = 0.0
    seed: int = 0
    timeout: float = DEFAULT_TIMEOUT

    @property
    def name(self) -> str:
        return f"ollama:{self.model}"

    def complete(self, prompt: str) -> str:
        payload = {
            "model": self.model,
            "prompt": prompt,
            "stream": False,
            "options": {"temperature": self.temperature, "seed": self.seed},
        }
        try:
            response = httpx.post(f"{self.host}/api/generate", json=payload, timeout=self.timeout)
            response.raise_for_status()
            data = response.json()
        except httpx.HTTPError as error:
            raise LLMError(
                f"Ollama request to {self.host} failed: {error}. Is the server running "
                f"(`ollama serve`) and is the model pulled (`ollama pull {self.model}`)?"
            ) from error
        # Ollama reports a bad model or a corrupt weight file as a 200 with an
        # `error` field rather than an HTTP error, so it has to be checked
        # separately or a broken model looks like an empty reply.
        if isinstance(data, dict) and data.get("error"):
            raise LLMError(f"Ollama returned an error for {self.model!r}: {data['error']}")
        return str(data.get("response", "")) if isinstance(data, dict) else ""


@dataclass
class CachingClient:
    """A client that stores each reply on disk, keyed by `(model, prompt)`.

    One file per prompt hash rather than one growing JSON blob: the guard is
    single-process per policy, but a file-per-key cache never has to read,
    rewrite and possibly corrupt a large shared file, and a half-written entry
    is one lost position rather than a lost cache.
    """

    inner: LLMClient
    directory: Path = CACHE_DIR

    @property
    def name(self) -> str:
        return self.inner.name

    def _path(self, prompt: str) -> Path:
        key = hashlib.sha256(f"{self.name}\x00{prompt}".encode()).hexdigest()
        return self.directory / f"{key}.txt"

    def complete(self, prompt: str) -> str:
        path = self._path(prompt)
        if path.exists():
            return path.read_text(encoding="utf-8")
        reply = self.inner.complete(prompt)
        self.directory.mkdir(parents=True, exist_ok=True)
        path.write_text(reply, encoding="utf-8")
        return reply


def ensure_reachable(client: LLMClient) -> None:
    """Raise `LLMError` if an Ollama-backed client cannot reach its server or model.

    The provider falls back to the heuristic on any call that fails, which is
    correct in a live battle -- a candidate provider must not crash mid-turn --
    but silently wrong when *measuring* C: a down server would make every
    position fall back to A and report A's numbers under C's name. So a guard run
    calls this once before it starts and fails loudly instead. A no-op for a
    client that is not Ollama-backed (a test fake, a future provider that has its
    own liveness story).
    """
    base = client.inner if isinstance(client, CachingClient) else client
    if not isinstance(base, OllamaClient):
        return
    try:
        response = httpx.get(f"{base.host}/api/tags", timeout=10.0)
        response.raise_for_status()
        models = {str(m.get("name")) for m in response.json().get("models", [])}
    except (httpx.HTTPError, ValueError) as error:
        raise LLMError(
            f"Ollama at {base.host} is not reachable: {error}. Start it with `ollama serve`."
        ) from error
    if base.model not in models:
        raise LLMError(
            f"model {base.model!r} is not pulled on {base.host}. Pull it with "
            f"`ollama pull {base.model}` (have: {sorted(models)})."
        )


def client_from_env(cache: bool | None = None) -> LLMClient:
    """The client the environment asks for, wrapped in a cache unless told not to.

    The one place a backend is chosen, so switching from the Ollama mock to a
    paid provider is a change here and nowhere else. `CHAMPIONS_LLM_BACKEND`,
    `CHAMPIONS_LLM_MODEL`, `OLLAMA_HOST`, `CHAMPIONS_LLM_CACHE` (`0` disables),
    and `CHAMPIONS_LLM_CACHE_DIR`.
    """
    backend = os.environ.get("CHAMPIONS_LLM_BACKEND", DEFAULT_BACKEND).lower()
    if backend == "ollama":
        client: LLMClient = OllamaClient(
            model=os.environ.get("CHAMPIONS_LLM_MODEL", DEFAULT_MODEL),
            host=os.environ.get("OLLAMA_HOST", DEFAULT_HOST),
        )
    else:
        raise LLMError(
            f"unknown LLM backend {backend!r}. Only 'ollama' is wired in; a paid provider "
            f"is deferred until the pipeline is validated (docs/DECISIONS.md D68)."
        )

    use_cache = cache if cache is not None else os.environ.get("CHAMPIONS_LLM_CACHE", "1") != "0"
    if use_cache:
        directory = Path(os.environ.get("CHAMPIONS_LLM_CACHE_DIR", str(CACHE_DIR)))
        return CachingClient(client, directory)
    return client


# -- prompt and parse --------------------------------------------------------

#: The task, stated once. It tells the model what it is *not* doing (arithmetic)
#: as firmly as what it is, because the failure section 3 warns about is a model
#: that second-guesses a computed number.
SYSTEM_PROMPT = (
    "You are the candidate-selection stage of a Pokemon Champions doubles battle agent. "
    "A search engine has already computed each candidate action's consequences for this "
    "turn -- damage as a percentage of the target's remaining HP, whether it knocks the "
    "target out, speed, and threats to your own Pokemon. Do not recompute any number; "
    "trust the ones given. Your only job is to order the actions from best to worst to "
    "play this turn, reasoning about the computed numbers."
)

#: What the model must return. No bracketed example, so the only array in the
#: reply is the answer and the parser is not fighting an echoed sample.
DEFAULT_INSTRUCTION = (
    "Respond with only a JSON array of the candidate numbers, ordered best first, "
    "including every candidate exactly once. Output nothing except the array."
)

_THINK = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)
_ARRAY = re.compile(r"\[[^\[\]]*\]", re.DOTALL)
_INT = re.compile(r"-?\d+")


def build_prompt(
    header: str,
    briefs: Sequence[str],
    instruction: str = DEFAULT_INSTRUCTION,
) -> str:
    """The full prompt: the task, the board, the numbered candidates, the ask.

    `header` is the board summary and `briefs[i]` is candidate `i`'s computed
    consequences. The index is the candidate's identity throughout, so the model
    answers with numbers and `parse_ranking` maps them straight back to actions.
    """
    lines = [SYSTEM_PROMPT, ""]
    if header:
        lines += [header, ""]
    lines.append("Candidate actions:")
    lines += [f"{i}: {brief}" for i, brief in enumerate(briefs)]
    lines += ["", instruction]
    return "\n".join(lines)


def parse_ranking(text: str, n: int) -> list[int]:
    """A model's reply as an ordered, deduplicated list of indices in `[0, n)`.

    Robust to the ways a small local model goes off script: a `<think>` block, a
    fenced code block, prose wrapped around the array, or no array at all and
    just a run of integers. The order is the model's; the first occurrence of
    each index wins, and out-of-range numbers are dropped rather than clamped
    (clamping would invent a preference the model did not state). A return of
    fewer than `n` indices -- or none -- is fine: the caller pads with its own
    ordering, which is the fallback the language provider always has.
    """
    cleaned = _THINK.sub(" ", text)

    numbers: list[int] = []
    arrays = _ARRAY.findall(cleaned)
    for chunk in arrays:
        found = [int(m) for m in _INT.findall(chunk)]
        if found:
            numbers = found
            break
    if not numbers:
        numbers = [int(m) for m in _INT.findall(cleaned)]

    ordered: list[int] = []
    seen: set[int] = set()
    for value in numbers:
        if 0 <= value < n and value not in seen:
            seen.add(value)
            ordered.append(value)
    return ordered


def order_indices(ranking: Sequence[int], n: int) -> list[int]:
    """The model's ranking, completed to a full permutation of `range(n)`.

    Whatever the model ranked comes first in its order; anything it left out --
    because it dropped indices, repeated some, or failed entirely -- is appended
    in natural order, which is the order the caller shortlisted the candidates
    in. So a model that returns nothing yields the caller's own ranking, and a
    model that ranks only its top few still has every candidate accounted for.
    """
    seen = set(ranking)
    tail = [i for i in range(n) if i not in seen]
    return [i for i in ranking if 0 <= i < n] + tail


def rank(
    client: LLMClient,
    header: str,
    briefs: Sequence[str],
    instruction: str = DEFAULT_INSTRUCTION,
) -> list[int]:
    """Ask `client` to order `briefs`, and return a full ranking of their indices.

    Raises `LLMError` if the backend does; the caller decides whether that is a
    fall-back-to-heuristic or a stop. The returned list is always a permutation
    of `range(len(briefs))` (see `order_indices`), so a caller can index into its
    candidate list without checking for gaps.
    """
    reply = client.complete(build_prompt(header, briefs, instruction))
    return order_indices(parse_ranking(reply, len(briefs)), len(briefs))
