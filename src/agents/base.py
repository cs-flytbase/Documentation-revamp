"""Base agent — supports Groq, OpenAI, and Anthropic backends."""

from src.config import (
    GROQ_API_KEY, OPENAI_API_KEY, ANTHROPIC_API_KEY,
    LLM_PROVIDER, get_model_for_agent,
)


def _get_openai_client():
    from openai import OpenAI
    return OpenAI(api_key=OPENAI_API_KEY)


def _get_groq_client():
    from groq import Groq
    return Groq(api_key=GROQ_API_KEY)


def _get_anthropic_client():
    import anthropic
    return anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)


class BaseAgent:
    def __init__(
        self,
        name: str,
        system_prompt: str,
        model: str | None = None,
        temperature: float = 0.2,
        max_tokens: int = 4096,
    ):
        self.name = name
        self.system_prompt = system_prompt
        self.provider = LLM_PROVIDER
        self.model = model or get_model_for_agent(name)
        self.temperature = temperature
        self.max_tokens = max_tokens

        if self.provider == "openai":
            self.client = _get_openai_client()
        elif self.provider == "groq":
            self.client = _get_groq_client()
        else:
            self.client = _get_anthropic_client()

    def run(self, user_message: str) -> str:
        if self.provider in ("openai", "groq"):
            response = self.client.chat.completions.create(
                model=self.model,
                temperature=self.temperature,
                max_tokens=self.max_tokens,
                messages=[
                    {"role": "system", "content": self.system_prompt},
                    {"role": "user", "content": user_message},
                ],
            )
            return response.choices[0].message.content
        else:
            response = self.client.messages.create(
                model=self.model,
                max_tokens=self.max_tokens,
                temperature=self.temperature,
                system=self.system_prompt,
                messages=[{"role": "user", "content": user_message}],
            )
            return response.content[0].text

    def run_with_history(self, messages: list[dict]) -> str:
        if self.provider in ("openai", "groq"):
            full_messages = [{"role": "system", "content": self.system_prompt}]
            full_messages.extend(messages)
            response = self.client.chat.completions.create(
                model=self.model,
                temperature=self.temperature,
                max_tokens=self.max_tokens,
                messages=full_messages,
            )
            return response.choices[0].message.content
        else:
            response = self.client.messages.create(
                model=self.model,
                max_tokens=self.max_tokens,
                temperature=self.temperature,
                system=self.system_prompt,
                messages=messages,
            )
            return response.content[0].text

    def run_with_vision(self, text: str, image_base64: str, media_type: str) -> str:
        if self.provider in ("openai", "groq"):
            response = self.client.chat.completions.create(
                model=self.model,
                temperature=self.temperature,
                max_tokens=self.max_tokens,
                messages=[
                    {"role": "system", "content": self.system_prompt},
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": f"data:{media_type};base64,{image_base64}",
                                },
                            },
                            {"type": "text", "text": text},
                        ],
                    },
                ],
            )
            return response.choices[0].message.content
        else:
            response = self.client.messages.create(
                model=self.model,
                max_tokens=self.max_tokens,
                temperature=self.temperature,
                system=self.system_prompt,
                messages=[{
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": media_type,
                                "data": image_base64,
                            },
                        },
                        {"type": "text", "text": text},
                    ],
                }],
            )
            return response.content[0].text
