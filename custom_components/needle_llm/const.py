"""Constants for the Needle LLM integration."""

DOMAIN = "needle_llm"

BACKEND_NEEDLE = "needle"
BACKEND_LLAMA_CPP = "llama_cpp"
DEFAULT_BACKEND = BACKEND_NEEDLE

CONF_BACKEND = "backend"
CONF_MODEL = "model"
CONF_MIN_CONFIDENCE = "min_confidence"
CONF_TIMEOUT = "timeout"

DEFAULT_MIN_CONFIDENCE = 0.80
DEFAULT_TIMEOUT = 30
