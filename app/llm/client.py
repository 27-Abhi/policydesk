"""Pluggable LLM provider adapter for grounded policy answer generation."""

import json
import logging
import os
from abc import ABC, abstractmethod
from typing import Generator, Optional

import httpx

from app.prompts import SYSTEM_GROUNDING_PROMPT, format_policy_answer

logger = logging.getLogger("policydesk.llm")


class BaseLLMClient(ABC):
    """Abstract interface for LLM completion providers."""

    @abstractmethod
    def generate(self, query: str, context: str) -> str:
        """Generate a complete grounded response."""
        pass

    @abstractmethod
    def stream(self, query: str, context: str) -> Generator[str, None, None]:
        """Stream a grounded response in token chunks."""
        pass


class DeterministicMockClient(BaseLLMClient):
    """Zero-cost deterministic fallback for local testing and CI."""

    def generate(self, query: str, context: str) -> str:
        return format_policy_answer(context)

    def stream(self, query: str, context: str) -> Generator[str, None, None]:
        text = self.generate(query, context)
        for word in text.split(" "):
            yield word + " "


class OpenAIClient(BaseLLMClient):
    """OpenAI API provider adapter."""

    def __init__(self, api_key: str, model: Optional[str] = None):
        self.api_key = api_key
        self.model = model or os.getenv("OPENAI_MODEL", "gpt-4o-mini")
        self.base_url = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")

    def generate(self, query: str, context: str) -> str:
        url = f"{self.base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": SYSTEM_GROUNDING_PROMPT},
                {"role": "user", "content": f"Context:\n{context}\n\nQuestion: {query}"},
            ],
            "temperature": 0.0,
        }
        try:
            with httpx.Client(timeout=15.0) as client:
                res = client.post(url, json=payload, headers=headers)
                res.raise_for_status()
                data = res.json()
                return data["choices"][0]["message"]["content"].strip()
        except Exception as exc:
            logger.warning("[LLM:OPENAI] API call failed: %s. Falling back to deterministic output.", exc)
            return DeterministicMockClient().generate(query, context)

    def stream(self, query: str, context: str) -> Generator[str, None, None]:
        url = f"{self.base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": SYSTEM_GROUNDING_PROMPT},
                {"role": "user", "content": f"Context:\n{context}\n\nQuestion: {query}"},
            ],
            "temperature": 0.0,
            "stream": True,
        }
        try:
            with httpx.Client(timeout=30.0) as client:
                with client.stream("POST", url, json=payload, headers=headers) as res:
                    res.raise_for_status()
                    for line in res.iter_lines():
                        if not line or not line.startswith("data: "):
                            continue
                        chunk_str = line[6:].strip()
                        if chunk_str == "[DONE]":
                            break
                        chunk = json.loads(chunk_str)
                        delta = chunk["choices"][0].get("delta", {}).get("content", "")
                        if delta:
                            yield delta
        except Exception as exc:
            logger.warning("[LLM:OPENAI:STREAM] Failed: %s. Yielding deterministic stream.", exc)
            yield from DeterministicMockClient().stream(query, context)


class AnthropicClient(BaseLLMClient):
    """Anthropic Claude API provider adapter."""

    def __init__(self, api_key: str, model: Optional[str] = None):
        self.api_key = api_key
        self.model = model or os.getenv("ANTHROPIC_MODEL", "claude-3-5-sonnet-20241022")
        self.base_url = os.getenv("ANTHROPIC_BASE_URL", "https://api.anthropic.com").rstrip("/")

    def generate(self, query: str, context: str) -> str:
        url = f"{self.base_url}/v1/messages"
        headers = {
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        }
        payload = {
            "model": self.model,
            "max_tokens": 1024,
            "system": SYSTEM_GROUNDING_PROMPT,
            "messages": [
                {"role": "user", "content": f"Context:\n{context}\n\nQuestion: {query}"},
            ],
            "temperature": 0.0,
        }
        try:
            with httpx.Client(timeout=15.0) as client:
                res = client.post(url, json=payload, headers=headers)
                res.raise_for_status()
                data = res.json()
                content_blocks = data.get("content", [])
                text_parts = [b["text"] for b in content_blocks if b.get("type") == "text"]
                return "".join(text_parts).strip()
        except Exception as exc:
            logger.warning("[LLM:ANTHROPIC] API call failed: %s. Falling back to deterministic output.", exc)
            return DeterministicMockClient().generate(query, context)

    def stream(self, query: str, context: str) -> Generator[str, None, None]:
        url = f"{self.base_url}/v1/messages"
        headers = {
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        }
        payload = {
            "model": self.model,
            "max_tokens": 1024,
            "system": SYSTEM_GROUNDING_PROMPT,
            "messages": [
                {"role": "user", "content": f"Context:\n{context}\n\nQuestion: {query}"},
            ],
            "temperature": 0.0,
            "stream": True,
        }
        try:
            with httpx.Client(timeout=30.0) as client:
                with client.stream("POST", url, json=payload, headers=headers) as res:
                    res.raise_for_status()
                    for line in res.iter_lines():
                        if not line or not line.startswith("data: "):
                            continue
                        chunk_str = line[6:].strip()
                        chunk = json.loads(chunk_str)
                        if chunk.get("type") == "content_block_delta":
                            delta = chunk.get("delta", {}).get("text", "")
                            if delta:
                                yield delta
        except Exception as exc:
            logger.warning("[LLM:ANTHROPIC:STREAM] Failed: %s. Yielding deterministic stream.", exc)
            yield from DeterministicMockClient().stream(query, context)


class OllamaClient(BaseLLMClient):
    """Local Ollama instance provider adapter."""

    def __init__(self, base_url: Optional[str] = None, model: Optional[str] = None):
        self.base_url = (base_url or os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")).rstrip("/")
        self.model = model or os.getenv("OLLAMA_MODEL", "gemma3:1b")

    def generate(self, query: str, context: str) -> str:
        url = f"{self.base_url}/api/chat"
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": SYSTEM_GROUNDING_PROMPT},
                {"role": "user", "content": f"Context:\n{context}\n\nQuestion: {query}"},
            ],
            "stream": False,
            "options": {"temperature": 0.0},
        }
        try:
            with httpx.Client(timeout=60.0) as client:
                res = client.post(url, json=payload)
                res.raise_for_status()
                data = res.json()
                return data.get("message", {}).get("content", "").strip()
        except Exception as exc:
            logger.warning("[LLM:OLLAMA] Local model call failed: %s. Falling back to deterministic output.", exc)
            return DeterministicMockClient().generate(query, context)

    def stream(self, query: str, context: str) -> Generator[str, None, None]:
        url = f"{self.base_url}/api/chat"
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": SYSTEM_GROUNDING_PROMPT},
                {"role": "user", "content": f"Context:\n{context}\n\nQuestion: {query}"},
            ],
            "stream": True,
            "options": {"temperature": 0.0},
        }
        try:
            with httpx.Client(timeout=60.0) as client:
                with client.stream("POST", url, json=payload) as res:
                    res.raise_for_status()
                    for line in res.iter_lines():
                        if not line:
                            continue
                        data = json.loads(line)
                        delta = data.get("message", {}).get("content", "")
                        if delta:
                            yield delta
        except Exception as exc:
            logger.warning("[LLM:OLLAMA:STREAM] Failed: %s. Yielding deterministic stream.", exc)
            yield from DeterministicMockClient().stream(query, context)


def get_llm_client() -> BaseLLMClient:
    """Factory resolving the active LLM provider based on environment configuration."""
    anthropic_key = os.getenv("ANTHROPIC_API_KEY")
    if anthropic_key and anthropic_key.strip():
        logger.info("[LLM:PROVIDER] Selected Anthropic Claude provider (%s)", os.getenv("ANTHROPIC_MODEL", "claude-3-5-sonnet-20241022"))
        return AnthropicClient(api_key=anthropic_key.strip())

    openai_key = os.getenv("OPENAI_API_KEY")
    if openai_key and openai_key.strip():
        logger.info("[LLM:PROVIDER] Selected OpenAI provider (%s)", os.getenv("OPENAI_MODEL", "gpt-4o-mini"))
        return OpenAIClient(api_key=openai_key.strip())

    ollama_url = os.getenv("OLLAMA_BASE_URL")
    if ollama_url and ollama_url.strip():
        logger.info("[LLM:PROVIDER] Selected local Ollama provider at %s (%s)", ollama_url, os.getenv("OLLAMA_MODEL", "gemma3:1b"))
        return OllamaClient(base_url=ollama_url.strip())

    return DeterministicMockClient()
