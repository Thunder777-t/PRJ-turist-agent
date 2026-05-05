from __future__ import annotations

import json
import os
from typing import Any

import httpx

from ..env_loader import load_project_dotenv


def _resolve_api_key() -> str:
    return (
        os.getenv("DEEPSEEK_API_KEY")
        or os.getenv("DeepSeek_API_KEY")
        or os.getenv("DEEPSEEK_KEY")
        or ""
    ).strip()


def _resolve_timeout(default_value: float = 60.0) -> float:
    raw = (os.getenv("DEEPSEEK_TIMEOUT_SEC") or "").strip()
    if not raw:
        return default_value
    try:
        timeout = float(raw)
    except Exception:
        return default_value
    if timeout <= 0:
        return default_value
    return timeout


def _resolve_model_candidates(primary_model: str) -> list[str]:
    configured = (os.getenv("DEEPSEEK_MODEL_FALLBACKS") or "").strip()
    raw_items: list[str] = [primary_model]
    if configured:
        raw_items.extend([item.strip() for item in configured.split(",") if item.strip()])
    else:
        # Keep defaults aligned with current official model IDs.
        # `deepseek-chat` / `deepseek-reasoner` are compatibility aliases and may be deprecated.
        if primary_model != "deepseek-v4-pro":
            raw_items.append("deepseek-v4-pro")
        if primary_model != "deepseek-v4-flash":
            raw_items.append("deepseek-v4-flash")

    models: list[str] = []
    seen: set[str] = set()
    for item in raw_items:
        key = item.strip()
        if not key:
            continue
        lowered = key.lower()
        if lowered in seen:
            continue
        seen.add(lowered)
        models.append(key)
    return models


def _extract_first_json_object(text: str) -> dict[str, Any] | None:
    """Best-effort JSON object extractor for model outputs with extra wrappers."""
    content = (text or "").strip()
    if not content:
        return None

    # Fast path: content itself is JSON.
    try:
        data = json.loads(content)
        return data if isinstance(data, dict) else None
    except Exception:
        pass

    # Try fenced code block.
    if "```" in content:
        pieces = content.split("```")
        for piece in pieces:
            candidate = piece.strip()
            if candidate.lower().startswith("json"):
                candidate = candidate[4:].strip()
            try:
                data = json.loads(candidate)
                if isinstance(data, dict):
                    return data
            except Exception:
                continue

    # Fallback: scan for a balanced JSON object.
    start = content.find("{")
    while start != -1:
        depth = 0
        in_string = False
        escape = False
        for idx in range(start, len(content)):
            ch = content[idx]
            if in_string:
                if escape:
                    escape = False
                elif ch == "\\":
                    escape = True
                elif ch == '"':
                    in_string = False
                continue

            if ch == '"':
                in_string = True
                continue
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    snippet = content[start : idx + 1]
                    try:
                        data = json.loads(snippet)
                        return data if isinstance(data, dict) else None
                    except Exception:
                        break
        start = content.find("{", start + 1)

    return None


class DeepseekClient:
    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        base_url: str | None = None,
        timeout_sec: float | None = None,
    ) -> None:
        load_project_dotenv()
        self.api_key = (api_key or _resolve_api_key()).strip()
        self.model = (model or os.getenv("DEEPSEEK_MODEL", "deepseek-v4-flash")).strip()
        self.model_candidates = _resolve_model_candidates(self.model)
        self.base_url = (
            base_url or os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1/chat/completions")
        ).strip()
        self.timeout_sec = _resolve_timeout() if timeout_sec is None else timeout_sec
        self.enabled = bool(self.api_key)

    def build_messages(
        self,
        system_prompt: str,
        developer_prompt: str,
        user_prompt: str,
        conversation_history: list[dict[str, str]] | None = None,
    ) -> list[dict[str, str]]:
        # DeepSeek follows OpenAI-like roles. `developer` role may not always be supported,
        # so we fold developer constraints into system prompt.
        combined_system = system_prompt.strip()
        if developer_prompt.strip():
            combined_system = (
                f"{combined_system}\n\n"
                "Additional execution constraints:\n"
                f"{developer_prompt.strip()}"
            )

        messages: list[dict[str, str]] = [{"role": "system", "content": combined_system}]

        for message in conversation_history or []:
            role = message.get("role", "")
            content = message.get("content", "")
            if role not in {"user", "assistant"}:
                continue
            if not content.strip():
                continue
            messages.append({"role": role, "content": content.strip()})

        messages.append({"role": "user", "content": user_prompt.strip()})
        return messages

    def chat_completion(
        self,
        messages: list[dict[str, str]],
        temperature: float = 0.2,
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str | dict[str, Any] | None = None,
        response_format: dict[str, Any] | None = None,
        max_tokens: int | None = None,
        reasoning_effort: str | None = None,
        extra_body: dict[str, Any] | None = None,
        model: str | None = None,
        allow_model_fallback: bool = True,
    ) -> dict[str, Any]:
        if not self.enabled:
            return {"ok": False, "error": "DeepSeek API key is missing."}

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        candidates = (
            _resolve_model_candidates(model.strip())
            if isinstance(model, str) and model.strip()
            else list(self.model_candidates)
        )
        if not allow_model_fallback and candidates:
            candidates = [candidates[0]]
        if not candidates:
            candidates = [self.model]

        last_error = "DeepSeek request failed."
        for model_name in candidates:
            payload: dict[str, Any] = {
                "model": model_name,
                "messages": messages,
                "temperature": temperature,
            }
            if tools:
                payload["tools"] = tools
            if tool_choice is not None:
                payload["tool_choice"] = tool_choice
            if response_format is not None:
                payload["response_format"] = response_format
            if max_tokens is not None:
                payload["max_tokens"] = max_tokens
            if reasoning_effort:
                payload["reasoning_effort"] = reasoning_effort
            if isinstance(extra_body, dict):
                payload.update(extra_body)

            try:
                with httpx.Client(timeout=self.timeout_sec) as client:
                    response = client.post(self.base_url, headers=headers, json=payload)
                    response.raise_for_status()
                    data = response.json()
            except httpx.HTTPStatusError as exc:
                status = getattr(exc.response, "status_code", None)
                body = ""
                try:
                    body = exc.response.text[:500]
                except Exception:
                    body = ""
                last_error = f"{model_name}: HTTP {status} {body}".strip()
                # For non-transient client-side errors, switching model usually won't help.
                if isinstance(status, int) and 400 <= status < 500 and status not in {408, 409, 429}:
                    break
                continue
            except (httpx.ReadTimeout, httpx.ConnectTimeout, httpx.TimeoutException) as exc:
                last_error = f"{model_name}: timeout ({exc})"
                continue
            except Exception as exc:
                last_error = f"{model_name}: {exc}"
                continue

            choices = data.get("choices") or []
            if not choices:
                last_error = f"{model_name}: No choices returned by DeepSeek."
                continue

            choice0 = choices[0] if isinstance(choices[0], dict) else {}
            message = choice0.get("message") or {}
            return {
                "ok": True,
                "content": message.get("content", ""),
                "reasoning_content": message.get("reasoning_content", ""),
                "tool_calls": message.get("tool_calls", []),
                "finish_reason": choice0.get("finish_reason"),
                "usage": data.get("usage", {}),
                "model_used": model_name,
                "raw": data,
            }

        return {"ok": False, "error": last_error}

    def json_completion(
        self,
        messages: list[dict[str, str]],
        temperature: float = 0.1,
        max_tokens: int = 1400,
        reasoning_effort: str | None = None,
        extra_body: dict[str, Any] | None = None,
        model: str | None = None,
        allow_model_fallback: bool = True,
    ) -> dict[str, Any]:
        """Run a completion and parse a JSON object from output."""
        result = self.chat_completion(
            messages=messages,
            temperature=temperature,
            response_format={"type": "json_object"},
            max_tokens=max_tokens,
            reasoning_effort=reasoning_effort,
            extra_body=extra_body,
            model=model,
            allow_model_fallback=allow_model_fallback,
        )
        if not result.get("ok"):
            enable_plain_json_retry = str(os.getenv("DEEPSEEK_JSON_PLAIN_RETRY", "0")).strip().lower() in {
                "1",
                "true",
                "yes",
                "on",
            }
            if enable_plain_json_retry:
                retry = self.chat_completion(
                    messages=messages,
                    temperature=temperature,
                    response_format=None,
                    max_tokens=max_tokens,
                    reasoning_effort=reasoning_effort,
                    extra_body=extra_body,
                    model=model,
                    allow_model_fallback=allow_model_fallback,
                )
                if retry.get("ok"):
                    result = retry
                else:
                    return result
            else:
                return result

        raw_text = str(result.get("content", "") or "")
        parsed = _extract_first_json_object(raw_text)
        if parsed is None:
            finish_reason = str(result.get("finish_reason", "") or "").strip().lower()
            if finish_reason == "length":
                error = "Model JSON output is truncated (finish_reason=length). Increase max_tokens."
            else:
                error = "Model output is not valid JSON object."
            return {
                "ok": False,
                "error": error,
                "content": raw_text,
                "finish_reason": result.get("finish_reason"),
                "usage": result.get("usage", {}),
                "raw": result.get("raw"),
            }

        return {
            "ok": True,
            "json": parsed,
            "content": raw_text,
            "finish_reason": result.get("finish_reason"),
            "usage": result.get("usage", {}),
            "raw": result.get("raw"),
        }
