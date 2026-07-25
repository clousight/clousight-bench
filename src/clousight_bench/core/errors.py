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
