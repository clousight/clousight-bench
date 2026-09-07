"""Stable user-input errors shared by the API and CLI."""


class UserInputError(RuntimeError):
    """A request cannot run because the requested benchmark surface is invalid."""


class UnknownDomainError(UserInputError):
    pass


class UnknownTaskError(UserInputError):
    pass


class UnknownPlatformError(UserInputError):
    pass


class AdapterNotRunnableError(UserInputError):
    pass


class RunCancelled(KeyboardInterrupt):
    """A cancel was requested for an in-flight run.

    Subclasses ``KeyboardInterrupt`` deliberately: the orchestrator already has
    exactly the right handling for one — teardown runs as a ``finally``, an
    ``interrupted`` record is persisted with whatever stages completed, and only
    then is the exception re-raised. A cancel is a Ctrl-C that arrived over the
    filesystem instead of the terminal, so it takes the same path rather than a
    parallel one that could drift out of step with it.
    """
