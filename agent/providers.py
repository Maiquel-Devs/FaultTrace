from anthropic import Anthropic
from google import genai
from google.genai import types as genai_types
from mistralai.client import Mistral
from openai import OpenAI

from .contracts import LLMProvider, LLMResponse
from .errors import LLMProviderError


def _provider_error(provider):
    return LLMProviderError(
        f"Não foi possível comunicar com {provider}. Verifique a credencial e o modelo."
    )


def _require_content(content, provider):
    if not content or not content.strip():
        raise LLMProviderError(f"{provider} retornou uma resposta vazia.")
    return content.strip()


class MistralProvider(LLMProvider):
    provider_name = "MISTRAL"

    def generate(self, messages, *, max_tokens=None):
        request = {
            "model": self.model,
            "messages": [
                {"role": message.role, "content": message.content}
                for message in messages
            ],
            "timeout_ms": int(self.timeout * 1000),
        }
        if max_tokens is not None:
            request["max_tokens"] = max_tokens
        try:
            with Mistral(api_key=self._api_key) as client:
                response = client.chat.complete(**request)
            content = response.choices[0].message.content
            if isinstance(content, list):
                content = "".join(
                    getattr(part, "text", "") or "" for part in content
                )
            content = _require_content(content, "Mistral")
        except LLMProviderError:
            raise
        except Exception:
            raise _provider_error("Mistral") from None
        return LLMResponse(content=content, provider=self.provider_name, model=self.model)


class OpenAIProvider(LLMProvider):
    provider_name = "OPENAI"

    def generate(self, messages, *, max_tokens=None):
        request = {
            "model": self.model,
            "input": [
                {
                    "role": "developer" if message.role == "system" else message.role,
                    "content": message.content,
                }
                for message in messages
            ],
            "store": False,
        }
        if max_tokens is not None:
            request["max_output_tokens"] = max_tokens
        try:
            with OpenAI(
                api_key=self._api_key,
                timeout=self.timeout,
                max_retries=0,
            ) as client:
                response = client.responses.create(**request)
            content = _require_content(response.output_text, "OpenAI")
        except LLMProviderError:
            raise
        except Exception:
            raise _provider_error("OpenAI") from None
        return LLMResponse(content=content, provider=self.provider_name, model=self.model)


class GeminiProvider(LLMProvider):
    provider_name = "GEMINI"

    def generate(self, messages, *, max_tokens=None):
        system_instruction = "\n".join(
            message.content for message in messages if message.role == "system"
        )
        contents = [
            genai_types.Content(
                role="model" if message.role == "assistant" else "user",
                parts=[genai_types.Part(text=message.content)],
            )
            for message in messages
            if message.role != "system"
        ]
        config = genai_types.GenerateContentConfig(
            system_instruction=system_instruction or None,
            max_output_tokens=max_tokens,
        )
        client = None
        try:
            client = genai.Client(
                api_key=self._api_key,
                http_options=genai_types.HttpOptions(
                    timeout=int(self.timeout * 1000)
                ),
            )
            response = client.models.generate_content(
                model=self.model,
                contents=contents,
                config=config,
            )
            content = _require_content(response.text, "Google Gemini")
        except LLMProviderError:
            raise
        except Exception:
            raise _provider_error("Google Gemini") from None
        finally:
            if client is not None:
                client.close()
        return LLMResponse(content=content, provider=self.provider_name, model=self.model)


class AnthropicProvider(LLMProvider):
    provider_name = "ANTHROPIC"

    def generate(self, messages, *, max_tokens=None):
        system = "\n".join(
            message.content for message in messages if message.role == "system"
        )
        request = {
            "model": self.model,
            "max_tokens": max_tokens or 512,
            "messages": [
                {"role": message.role, "content": message.content}
                for message in messages
                if message.role != "system"
            ],
        }
        if system:
            request["system"] = system
        try:
            with Anthropic(
                api_key=self._api_key,
                timeout=self.timeout,
                max_retries=0,
            ) as client:
                response = client.messages.create(**request)
            content = "".join(
                block.text for block in response.content if block.type == "text"
            )
            content = _require_content(content, "Anthropic Claude")
        except LLMProviderError:
            raise
        except Exception:
            raise _provider_error("Anthropic Claude") from None
        return LLMResponse(content=content, provider=self.provider_name, model=self.model)
