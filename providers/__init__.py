from __future__ import annotations
from typing import Dict, Callable
from .reducto_provider import ReductoProvider

REGISTRY: Dict[str, Callable[..., object]] = {
    "reducto": lambda **kw: ReductoProvider(api_key=kw["reducto_api_key"]),
    # add more later:
    # "openai": lambda **kw: OpenAIProvider(api_key=kw["openai_api_key"]),
    # "anthropic": lambda **kw: AnthropicProvider(api_key=kw["anthropic_api_key"]),
}

def get_provider(name: str, **kwargs):
    try:
        ctor = REGISTRY[name]
    except KeyError:
        raise ValueError(f"Unknown provider: {name}")
    return ctor(**kwargs)
