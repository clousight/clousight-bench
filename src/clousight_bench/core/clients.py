"""Credential -> SDK client factory (the shared wiring seam).

``credentials.py`` reports *where* a credential comes from; this module is the
one place that turns that resolution plus a resolved endpoint into a live SDK
client. Centralising it means all four providers reuse the same identity
resolution, endpoint, and (future) retry/timeout policy instead of each adapter
re-deriving them.

Construction of the concrete SDK client is deliberately a single, well-marked
seam: ``build()`` raises ``ClientNotWiredError`` until a provider's client
builder is registered via ``register_builder``. This keeps the open-core honest
(no half-working live calls) while giving a wiring point that does not touch any
adapter or task -- you register one builder and every adapter for that provider
gets a real client.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from clousight_bench.core.credentials import (
    PROVIDER_CREDENTIALS,
    CredentialResolution,
    resolve_credentials,
)


class ClientNotWiredError(NotImplementedError):
    """Raised when a live SDK client is requested but no builder is registered.

    The message names the exact missing piece (SDK package, endpoint, credential
    source) so wiring is mechanical, never a guessing game."""


# provider -> callable(context) -> SDK client. Empty in open-core; a commercial
# / private wiring layer registers builders here (or via entry points) so
# ``ClientFactory.build`` returns real clients without adapters changing.
_BUILDERS: dict[str, Callable[[ClientContext], Any]] = {}


def register_builder(provider: str, builder: Callable[[ClientContext], Any]) -> None:
    """Register the client builder for a provider (idempotent last-wins)."""
    _BUILDERS[provider] = builder


@dataclass
class ClientPolicy:
    """Timeout + retry bounds every real-cloud SDK call inherits.

    Centralised so all four clouds share one policy instead of the first wired
    adapter inventing its own. The per-request ``read_timeout_s`` is the real
    guard against a hung live call: the orchestrator's SIGALRM stage deadline is
    main-thread-only and does NOT interrupt a threaded load probe, so a wired
    transport must bound each call itself -- and by the run's remaining deadline
    (``bounded_read_timeout``) so it never runs past the stage's own budget.
    """

    connect_timeout_s: float = 10.0
    read_timeout_s: float = 30.0
    max_attempts: int = 3
    backoff_base_s: float = 0.2
    backoff_max_s: float = 5.0

    @classmethod
    def from_target(cls, target: dict[str, Any] | None) -> ClientPolicy:
        target = target or {}
        timeouts = target.get("timeouts") or {}
        retries = target.get("retries") or {}
        base = cls()
        return cls(
            connect_timeout_s=float(timeouts.get("connect_s", base.connect_timeout_s)),
            read_timeout_s=float(timeouts.get("read_s", base.read_timeout_s)),
            max_attempts=int(retries.get("max_attempts", base.max_attempts)),
            backoff_base_s=float(retries.get("backoff_base_s", base.backoff_base_s)),
            backoff_max_s=float(retries.get("backoff_max_s", base.backoff_max_s)),
        )

    def bounded_read_timeout(self, remaining_s: float | None) -> float:
        """Read timeout for one call, never exceeding the run's remaining budget."""
        if remaining_s is None:
            return self.read_timeout_s
        return min(self.read_timeout_s, max(0.0, remaining_s))

    def backoff_for(self, attempt: int) -> float:
        """Exponential backoff for retry ``attempt`` (1-based), capped."""
        delay = self.backoff_base_s * (2 ** max(0, attempt - 1))
        return min(delay, self.backoff_max_s)


@dataclass
class ClientContext:
    """Everything a builder needs, with the secret still behind the SDK's chain.

    The secret value is never held here: ``credentials`` reports the *source*
    (env var names / profile / file); the SDK's own default chain reads the
    actual value at call time, exactly as the provider CLI would."""

    provider: str
    region: str | None
    endpoint: str | None
    credentials: CredentialResolution
    target: dict[str, Any] = field(default_factory=dict)
    policy: ClientPolicy = field(default_factory=ClientPolicy)
    #: The run's remaining stage deadline (seconds), so a builder can bound each
    #: call by it. None when the run set no --timeout.
    deadline_s: float | None = None


class ClientFactory:
    """Resolve credentials + endpoint into a (future) SDK client for one adapter."""

    def __init__(
        self,
        provider: str | None,
        region: str | None,
        endpoint: str | None,
        target: dict[str, Any] | None = None,
        platform: str | None = None,
        deadline_s: float | None = None,
    ) -> None:
        self.provider = provider
        self.region = region
        self.endpoint = endpoint
        self.target = target or {}
        self.platform = platform
        self.deadline_s = deadline_s

    def credentials(self) -> CredentialResolution:
        """Where this client's credentials resolve from (never the secret)."""
        return resolve_credentials(self.target, platform=self.platform or self.provider)

    def policy(self) -> ClientPolicy:
        """The timeout + retry policy this client's calls inherit."""
        return ClientPolicy.from_target(self.target)

    def sdk_module(self) -> str | None:
        """The provider's SDK package name, or None for an unknown provider."""
        spec = PROVIDER_CREDENTIALS.get(self.provider or "")
        return spec["sdk_module"] if spec else None

    def sdk_available(self) -> bool:
        """Is the provider SDK importable in this environment?"""
        import importlib.util

        mod = self.sdk_module()
        return mod is not None and importlib.util.find_spec(mod) is not None

    def context(self) -> ClientContext:
        return ClientContext(
            provider=self.provider or "",
            region=self.region,
            endpoint=self.endpoint,
            credentials=self.credentials(),
            target=self.target,
            policy=self.policy(),
            deadline_s=self.deadline_s,
        )

    def build(self) -> Any:
        """Return a live SDK client, or raise ClientNotWiredError with guidance.

        Registered-builder path is real; the default raises a message that names
        the SDK package, endpoint, and credential source needed to wire it."""
        builder = _BUILDERS.get(self.provider or "")
        if builder is not None:
            return builder(self.context())

        cred = self.credentials()
        sdk = self.sdk_module() or "the provider SDK"
        cred_hint = f"via {cred.source}" if cred.ok else f"not resolvable: {cred.remediation}"
        raise ClientNotWiredError(
            f"no client builder registered for provider {self.provider!r}. "
            f"To wire it: install {sdk}, then register_builder({self.provider!r}, ...) "
            f"to construct a client for endpoint {self.endpoint or '<unresolved>'} "
            f"using credentials ({cred_hint})."
        )
