import json
from openai import OpenAI
from app.core.config import get_settings


settings = get_settings()
client = OpenAI(api_key=settings.openai_api_key)


async def call_llm(
    system_prompt: str,
    user_prompt: str,
    json_output: bool = True,
    max_tokens: int = 4096,
) -> dict | str:
    """Call OpenAI with a system + user prompt. Returns parsed JSON or raw text."""
    kwargs = {
        "model": settings.llm_model,
        "max_tokens": max_tokens,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
    }

    if json_output:
        kwargs["response_format"] = {"type": "json_object"}

    response = client.chat.completions.create(**kwargs)
    text = response.choices[0].message.content

    if json_output:
        cleaned = text.strip()
        if cleaned.startswith("```json"):
            cleaned = cleaned[7:]
        if cleaned.startswith("```"):
            cleaned = cleaned[3:]
        if cleaned.endswith("```"):
            cleaned = cleaned[:-3]
        return json.loads(cleaned.strip())

    return text
