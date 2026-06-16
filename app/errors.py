"""Exception types for the ZillaSoft Local Manager."""


class ConfigError(Exception):
    """Base class for configuration errors."""


class CredentialWriteError(ConfigError):
    """Raised when an agent attempts to write a credential key.

    Credentials are Mario-only and edited via the UI settings panel. Agents
    have read access but any write attempt raises this error.
    """

    def __init__(self, key: str):
        self.key = key
        super().__init__(
            f"'{key}' is a protected credential and can only be edited by Mario "
            f"via the UI settings panel. Agents cannot write credential keys."
        )


class ConfigValidationError(ConfigError):
    """Raised when a config value fails schema validation on write."""


class AgentError(Exception):
    """Base class for agent / Anthropic-call errors."""


class PayloadTooLargeError(AgentError):
    """Raised when an inter-agent payload exceeds the token limit and cannot
    be reduced (e.g. summarization still over budget)."""

    def __init__(self, tokens: int, limit: int):
        self.tokens = tokens
        self.limit = limit
        super().__init__(
            f"Inter-agent payload is ~{tokens} tokens, over the {limit}-token "
            f"limit, and could not be reduced."
        )
