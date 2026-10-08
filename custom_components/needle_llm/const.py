"""Constants for the Needle LLM integration."""

DOMAIN = "needle_llm"

BACKEND_NEEDLE = "needle"
BACKEND_HA_PROVIDER = "ha_provider"
SUPPORTED_BACKENDS = frozenset((BACKEND_NEEDLE, BACKEND_HA_PROVIDER))
# Old standalone model-only backend IDs must never run without Needle.
UNSUPPORTED_LEGACY_BACKENDS = frozenset(("openai_compatible", "llama_cpp"))
DEFAULT_BACKEND = BACKEND_NEEDLE

CONF_BACKEND = "backend"
CONF_PROVIDER_MODEL = "provider_model"
CONF_ROUTING_STRATEGY = "routing_strategy"
CONF_MIN_CONFIDENCE = "min_confidence"
CONF_TIMEOUT = "timeout"

DEFAULT_MIN_CONFIDENCE = 0.80
DEFAULT_TIMEOUT = 30
