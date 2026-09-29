class LLMError(Exception):
    pass


class LLMConfigurationError(LLMError):
    pass


class LLMProviderError(LLMError):
    pass
