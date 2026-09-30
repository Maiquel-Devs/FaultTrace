class LLMError(Exception):
    pass


class LLMConfigurationError(LLMError):
    pass


class LLMProviderError(LLMError):
    pass


class AgentError(Exception):
    pass


class AgentAuthorizationError(AgentError):
    pass


class AgentLoopLimitError(AgentError):
    pass


class AgentExecutionError(AgentError):
    pass
