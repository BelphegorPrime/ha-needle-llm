"""Constants for the Needle LLM integration."""

DOMAIN = "needle_llm"

BACKEND_NEEDLE = "needle"
BACKEND_OPENAI_COMPATIBLE = "openai_compatible"
BACKEND_HA_PROVIDER = "ha_provider"

CONF_PROVIDER_MODEL = "provider_model"
CONF_ROUTING_STRATEGY = "routing_strategy"
LEGACY_BACKEND_LLAMA_CPP = "llama_cpp"
DEFAULT_BACKEND = BACKEND_NEEDLE

CONF_BACKEND = "backend"
CONF_MODEL = "model"
CONF_MIN_CONFIDENCE = "min_confidence"
CONF_TIMEOUT = "timeout"

DEFAULT_MIN_CONFIDENCE = 0.80
DEFAULT_TIMEOUT = 30


def normalize_backend(backend: str) -> str:
    """Migrate the initial llama.cpp-specific name to the generic backend."""
    if backend == LEGACY_BACKEND_LLAMA_CPP:
        return BACKEND_OPENAI_COMPATIBLE
    return backend
