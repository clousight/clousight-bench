"""Judge base — make the ``judge-based`` reproducibility class real.

A :class:`JudgeModel` is the LLM-as-judge seam: a metric that needs a fuzzy
judgment asks a judge for a CATEGORICAL verdict (never a free float), and fixed
arithmetic maps the verdict to a score. We deliberately reject G-Eval's
top-logprob weighted-sum (provider-dependent, non-reproducible); categorical
verdicts at ``temperature=0`` plus recorded judge provenance are auditable even
though a judge run is not bit-reproducible — which is exactly what
``reproducibility_class="judge-based"`` means.

``judge_emit`` is the portability helper (the highest-leverage DeepEval borrow):
it gets a structured object from any judge — native JSON-schema mode when the
model supports it, else a prompt-with-example plus brace-slice + trailing-comma
repair (``_trim_and_load_json``) — converging on a caller ``extract`` callback.
This decouples "what shape I want" from "does this judge support JSON mode".
"""

from __future__ import annotations

import hashlib
import json
import re
from abc import ABC, abstractmethod
from collections.abc import Callable
from pathlib import Path
from typing import Any


class JudgeError(RuntimeError):
    """A judge produced output that could not be parsed into the requested shape."""


class JudgeModel(ABC):
    """A pluggable LLM-as-judge. Concrete judges (endpoint / recorded / mock)
    supply the transport; metrics depend only on this surface."""

    @abstractmethod
    def model_id(self) -> str:
        """Stable identifier of the judge model (for provenance)."""

    @abstractmethod
    def generate(self, prompt: str) -> str:
        """Return the judge's raw text completion at temperature 0."""

    def generate_schema(self, prompt: str, schema: dict[str, Any]) -> dict[str, Any]:
        """Return a parsed object using native structured output. Judges without
        native JSON-schema support leave this unimplemented and rely on
        ``generate`` + :func:`_trim_and_load_json` via :func:`judge_emit`."""
        raise NotImplementedError

    def capabilities(self) -> dict[str, bool]:
        """Feature probes so a metric degrades gracefully across judge models.
        Default: no native json-schema, no logprobs (the robust fallback path)."""
        return {"json_schema": False, "logprobs": False}


class JudgeProvider(ABC):
    """Factory for a :class:`JudgeModel`, registered under the
    ``clousight_bench.judges`` entry-point group — the config-connect seam for
    judges (open-source AND commercial).

    A judge model needs per-run config (endpoint / model / credentials), so it
    cannot be a zero-arg plugin like a Metric. Instead a zero-arg *provider* is
    registered by ``name``; a run selects it via ``config['provider']`` and the
    provider ``build``s the concrete judge from the rest of the config. A
    commercial judge ships its own provider from a private package.
    """

    name: str = "abstract"
    requires_plugin_api: str = ">=1.0,<2.0"

    @abstractmethod
    def build(self, config: dict[str, Any]) -> JudgeModel:
        """Construct a JudgeModel from run config (endpoint/model/credentials/…)."""


_JSON_OBJ_RE = re.compile(r"\{.*\}", re.DOTALL)
_TRAILING_COMMA_RE = re.compile(r",\s*([}\]])")


def _trim_and_load_json(text: str) -> dict[str, Any]:
    """Best-effort parse of a JSON object out of a model reply.

    Slices the first ``{`` … last ``}`` (models wrap JSON in prose/fences) and
    repairs trailing commas. Raises :class:`JudgeError` if no object parses — a
    judge that cannot emit parseable JSON is an error, isolated per-item by the
    metric runner (``status="error"``), never a silent zero.
    """
    m = _JSON_OBJ_RE.search(text or "")
    if not m:
        raise JudgeError(f"no JSON object in judge reply: {text[:200]!r}")
    blob = _TRAILING_COMMA_RE.sub(r"\1", m.group(0))
    try:
        data = json.loads(blob)
    except json.JSONDecodeError as exc:
        raise JudgeError(f"unparseable judge JSON: {exc}: {blob[:200]!r}") from exc
    if not isinstance(data, dict):
        raise JudgeError(f"judge JSON was not an object: {type(data).__name__}")
    return data


def judge_emit(
    judge: JudgeModel,
    prompt: str,
    schema: dict[str, Any],
    extract: Callable[[dict[str, Any]], Any],
) -> Any:
    """Get a structured verdict from ``judge`` and run ``extract`` on it.

    Native JSON-schema path when the judge advertises it; otherwise
    ``generate`` + :func:`_trim_and_load_json`. ``extract`` maps the parsed dict
    to the metric's value (and validates it — raise inside ``extract`` to reject
    an out-of-vocabulary verdict).
    """
    if judge.capabilities().get("json_schema"):
        data = judge.generate_schema(prompt, schema)
    else:
        data = _trim_and_load_json(judge.generate(prompt))
    return extract(data)


class CachingJudge(JudgeModel):
    """Content-addressed judge cache: memoize a judge's replies by
    ``sha256(model_id + prompt)`` to a JSON file, so re-running a judge-based eval
    reuses verdicts and never re-pays the slow, costly LLM calls.

    Only judge scoring is cached — never environmental numbers (which depend on
    live state and must re-execute). Keyed on the judge model id + the exact
    prompt, so a different model or a changed prompt is a miss. Delegates
    identity/capabilities to the wrapped judge.
    """

    def __init__(self, inner: JudgeModel, cache_path: str | Path) -> None:
        self._inner = inner
        self._path = Path(cache_path)
        self._cache: dict[str, str] = {}
        if self._path.exists():
            try:
                loaded = json.loads(self._path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    self._cache = {str(k): str(v) for k, v in loaded.items()}
            except Exception:  # noqa: BLE001 - a corrupt cache is discarded, never fatal
                self._cache = {}

    def model_id(self) -> str:
        return self._inner.model_id()

    def capabilities(self) -> dict[str, bool]:
        return self._inner.capabilities()

    def _key(self, tag: str, prompt: str) -> str:
        return hashlib.sha256(f"{self._inner.model_id()}\x00{tag}\x00{prompt}".encode()).hexdigest()

    def _flush(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(self._path.suffix + ".tmp")
        tmp.write_text(json.dumps(self._cache), encoding="utf-8")
        tmp.replace(self._path)

    def generate(self, prompt: str) -> str:
        key = self._key("generate", prompt)
        if key in self._cache:
            return self._cache[key]
        out = self._inner.generate(prompt)
        self._cache[key] = out
        self._flush()
        return out

    def generate_schema(self, prompt: str, schema: dict[str, Any]) -> dict[str, Any]:
        key = self._key("schema", prompt)
        if key in self._cache:
            return json.loads(self._cache[key])
        out = self._inner.generate_schema(prompt, schema)
        self._cache[key] = json.dumps(out)
        self._flush()
        return out
