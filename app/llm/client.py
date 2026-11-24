"""Pluggable LLM provider adapter for grounded policy generation and structured agent calls."""

import json
import logging
import os
import re
from abc import ABC, abstractmethod
from typing import Any, Generator, Optional

import httpx

from app.observability import estimate_tokens
from app.prompts import SYSTEM_GROUNDING_PROMPT, format_policy_answer

logger = logging.getLogger("policydesk.llm")


def _extract_relevant_excerpt(query: str, context: str, max_chars: int = 750) -> str:
    """Extract focused query-aligned sentences from long policy context without raw header noise."""
    if len(context) <= 320:
        return context

    # Focus on the top-ranked passage block before any '---' separator
    primary_block = context.split("\n\n---\n\n")[0]
    source_tags = re.findall(r"\[Source:[^\]]+\]", primary_block)
    cleaned = re.sub(r"\[Source:[^\]]+\]\s*", "", primary_block)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()

    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", cleaned) if len(s.strip()) > 12]
    if not sentences:
        excerpt = cleaned[:max_chars].rstrip()
        return f"{excerpt} {source_tags[0]}".strip() if source_tags else excerpt

    q_words = {w for w in re.findall(r"\b[a-z0-9]{3,}\b", (query or "").lower())}
    scored: list[tuple[int, int, int, str]] = []
    for idx, sent in enumerate(sentences):
        s_words = set(re.findall(r"\b[a-z0-9]{3,}\b", sent.lower()))
        overlap = len(q_words & s_words)
        # Prefer informative sentences containing specific figures, timeframes, or named funds
        info_bonus = 1 if re.search(r"\b(?:deaf|rbi|bsbda|\d+\s*(?:years?|days?|months?|%)|\$|₹)\b", sent, re.IGNORECASE) else 0
        scored.append((overlap, info_bonus, -idx, sent))

    scored.sort(reverse=True)
    chosen: list[str] = []
    total_len = 0
    for _, _, _, sent in scored:
        if total_len + len(sent) > max_chars and chosen:
            break
        chosen.append(sent)
        total_len += len(sent) + 1
        if len(chosen) >= 4:
            break

    body = " ".join(chosen) if chosen else cleaned[:max_chars]
    if source_tags and source_tags[0] not in body:
        body = f"{body}\n\n{source_tags[0]}"
    return body


def _parse_json_object(raw_text: str) -> Optional[dict[str, Any]]:
    if not raw_text:
        return None
    match = re.search(r"\{.*\}", raw_text, re.DOTALL)
    if not match:
        return None
    try:
        obj = json.loads(match.group(0))
        return obj if isinstance(obj, dict) else None
    except json.JSONDecodeError:
        return None


class BaseLLMClient(ABC):
    """Abstract interface for LLM completion providers."""

    def __init__(self) -> None:
        self.last_usage: dict[str, int] = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}

    def _record_usage(self, prompt_text: str, completion_text: str, raw_usage: Optional[dict[str, Any]] = None) -> None:
        if raw_usage and isinstance(raw_usage, dict):
            pt = int(raw_usage.get("prompt_tokens") or raw_usage.get("input_tokens") or 0)
            ct = int(raw_usage.get("completion_tokens") or raw_usage.get("output_tokens") or 0)
            tt = int(raw_usage.get("total_tokens") or (pt + ct))
            if tt > 0:
                self.last_usage = {"prompt_tokens": pt, "completion_tokens": ct, "total_tokens": tt}
                return
        pt_est = estimate_tokens(prompt_text)
        ct_est = estimate_tokens(completion_text)
        self.last_usage = {"prompt_tokens": pt_est, "completion_tokens": ct_est, "total_tokens": pt_est + ct_est}

    @abstractmethod
    def generate(self, query: str, context: str) -> str:
        """Generate a complete grounded response."""

    @abstractmethod
    def stream(self, query: str, context: str) -> Generator[str, None, None]:
        """Stream a grounded response in token chunks."""

    def complete_messages(
        self,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.0,
        max_tokens: int = 512,
    ) -> str:
        """Execute a generic system+user completion call."""
        return self.generate(query=user_prompt, context=system_prompt)

    def complete_json(
        self,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.0,
        max_tokens: int = 256,
    ) -> Optional[dict[str, Any]]:
        """Execute a structured JSON completion and parse the response object."""
        raw = self.complete_messages(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        return _parse_json_object(raw)


class DeterministicMockClient(BaseLLMClient):
    """Zero-cost deterministic fallback for local testing and CI."""

    def generate(self, query: str, context: str) -> str:
        excerpt = _extract_relevant_excerpt(query, context)
        output = format_policy_answer(excerpt)
        self._record_usage(f"{query}\n{context}", output)
        return output

    def stream(self, query: str, context: str) -> Generator[str, None, None]:
        text = self.generate(query, context)
        for word in text.split(" "):
            yield word + " "


class OpenAIClient(BaseLLMClient):
    """OpenAI and OpenAI-compatible API provider adapter."""

    def __init__(self, api_key: str, model: Optional[str] = None):
        super().__init__()
        self.api_key = api_key
        self.model = model or os.getenv("OPENAI_MODEL", "gpt-4o-mini")
        self.base_url = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")

    def complete_messages(
        self,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.0,
        max_tokens: int = 512,
    ) -> str:
        url = f"{self.base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        with httpx.Client(timeout=30.0) as client:
            res = client.post(url, json=payload, headers=headers)
            res.raise_for_status()
            data = res.json()
            text = data["choices"][0]["message"]["content"].strip()
            self._record_usage(f"{system_prompt}\n{user_prompt}", text, data.get("usage"))
            return text

    def generate(self, query: str, context: str) -> str:
        user_prompt = f"Context:\n{context}\n\nQuestion: {query}"
        try:
            return self.complete_messages(SYSTEM_GROUNDING_PROMPT, user_prompt, temperature=0.0, max_tokens=512)
        except Exception as exc:
            logger.warning("[LLM:OPENAI] API call failed: %s. Falling back to deterministic output.", exc)
            mock = DeterministicMockClient()
            out = mock.generate(query, context)
            self.last_usage = mock.last_usage
            return out

    def stream(self, query: str, context: str) -> Generator[str, None, None]:
        url = f"{self.base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        user_prompt = f"Context:\n{context}\n\nQuestion: {query}"
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": SYSTEM_GROUNDING_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 0.0,
            "stream": True,
        }
        collected: list[str] = []
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
                            collected.append(delta)
                            yield delta
            self._record_usage(user_prompt, "".join(collected))
        except Exception as exc:
            logger.warning("[LLM:OPENAI:STREAM] Failed: %s. Yielding deterministic stream.", exc)
            yield from DeterministicMockClient().stream(query, context)


class AnthropicClient(BaseLLMClient):
    """Anthropic Claude API provider adapter."""

    def __init__(self, api_key: str, model: Optional[str] = None):
        super().__init__()
        self.api_key = api_key
        self.model = model or os.getenv("ANTHROPIC_MODEL", "claude-3-5-sonnet-20241022")
        self.base_url = os.getenv("ANTHROPIC_BASE_URL", "https://api.anthropic.com").rstrip("/")

    def complete_messages(
        self,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.0,
        max_tokens: int = 1024,
    ) -> str:
        url = f"{self.base_url}/v1/messages"
        headers = {
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        }
        payload = {
            "model": self.model,
            "max_tokens": max_tokens,
            "system": system_prompt,
            "messages": [
                {"role": "user", "content": user_prompt},
            ],
            "temperature": temperature,
        }
        with httpx.Client(timeout=30.0) as client:
            res = client.post(url, json=payload, headers=headers)
            res.raise_for_status()
            data = res.json()
            content_blocks = data.get("content", [])
            text_parts = [b["text"] for b in content_blocks if b.get("type") == "text"]
            text = "".join(text_parts).strip()
            self._record_usage(f"{system_prompt}\n{user_prompt}", text, data.get("usage"))
            return text

    def generate(self, query: str, context: str) -> str:
        user_prompt = f"Context:\n{context}\n\nQuestion: {query}"
        try:
            return self.complete_messages(SYSTEM_GROUNDING_PROMPT, user_prompt, temperature=0.0, max_tokens=1024)
        except Exception as exc:
            logger.warning("[LLM:ANTHROPIC] API call failed: %s. Falling back to deterministic output.", exc)
            mock = DeterministicMockClient()
            out = mock.generate(query, context)
            self.last_usage = mock.last_usage
            return out

    def stream(self, query: str, context: str) -> Generator[str, None, None]:
        url = f"{self.base_url}/v1/messages"
        headers = {
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        }
        user_prompt = f"Context:\n{context}\n\nQuestion: {query}"
        payload = {
            "model": self.model,
            "max_tokens": 1024,
            "system": SYSTEM_GROUNDING_PROMPT,
            "messages": [
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 0.0,
            "stream": True,
        }
        collected: list[str] = []
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
                                collected.append(delta)
                                yield delta
            self._record_usage(user_prompt, "".join(collected))
        except Exception as exc:
            logger.warning("[LLM:ANTHROPIC:STREAM] Failed: %s. Yielding deterministic stream.", exc)
            yield from DeterministicMockClient().stream(query, context)


class OllamaClient(BaseLLMClient):
    """Local Ollama instance provider adapter."""

    def __init__(self, base_url: Optional[str] = None, model: Optional[str] = None):
        super().__init__()
        self.base_url = (base_url or os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")).rstrip("/")
        self.model = model or os.getenv("OLLAMA_MODEL", "gemma3:1b")

    def complete_messages(
        self,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.0,
        max_tokens: int = 512,
    ) -> str:
        url = f"{self.base_url}/api/chat"
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "stream": False,
            "options": {"temperature": temperature, "num_predict": max_tokens},
        }
        with httpx.Client(timeout=60.0) as client:
            res = client.post(url, json=payload)
            res.raise_for_status()
            data = res.json()
            text = data.get("message", {}).get("content", "").strip()
            self._record_usage(f"{system_prompt}\n{user_prompt}", text)
            return text

    def generate(self, query: str, context: str) -> str:
        user_prompt = f"Context:\n{context}\n\nQuestion: {query}"
        try:
            return self.complete_messages(SYSTEM_GROUNDING_PROMPT, user_prompt, temperature=0.0, max_tokens=512)
        except Exception as exc:
            logger.warning("[LLM:OLLAMA] Local model call failed: %s. Falling back to deterministic output.", exc)
            mock = DeterministicMockClient()
            out = mock.generate(query, context)
            self.last_usage = mock.last_usage
            return out

    def stream(self, query: str, context: str) -> Generator[str, None, None]:
        url = f"{self.base_url}/api/chat"
        user_prompt = f"Context:\n{context}\n\nQuestion: {query}"
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": SYSTEM_GROUNDING_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            "stream": True,
            "options": {"temperature": 0.0},
        }
        collected: list[str] = []
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
                            collected.append(delta)
                            yield delta
            self._record_usage(user_prompt, "".join(collected))
        except Exception as exc:
            logger.warning("[LLM:OLLAMA:STREAM] Failed: %s. Yielding deterministic stream.", exc)
            yield from DeterministicMockClient().stream(query, context)


def get_llm_client() -> BaseLLMClient:
    """Factory resolving the active LLM provider based on environment configuration."""
    anthropic_key = os.getenv("ANTHROPIC_API_KEY")
    if anthropic_key and anthropic_key.strip():
        logger.info("[LLM:PROVIDER] Selected Anthropic Claude provider (%s)", os.getenv("ANTHROPIC_MODEL", "claude-3-5-sonnet-20241022"))
        return AnthropicClient(api_key=anthropic_key.strip())

    openai_key = os.getenv("OPENAI_API_KEY") or os.getenv("OPEN_AI_API_KEY")
    if openai_key and openai_key.strip():
        logger.info("[LLM:PROVIDER] Selected OpenAI provider (%s)", os.getenv("OPENAI_MODEL", "gpt-4o-mini"))
        return OpenAIClient(api_key=openai_key.strip())

    ollama_url = os.getenv("OLLAMA_BASE_URL")
    if ollama_url and ollama_url.strip():
        logger.info("[LLM:PROVIDER] Selected local Ollama provider at %s (%s)", ollama_url, os.getenv("OLLAMA_MODEL", "gemma3:1b"))
        return OllamaClient(base_url=ollama_url.strip())

    return DeterministicMockClient()
