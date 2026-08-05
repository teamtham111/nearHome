"""External provider adapter interfaces and factory."""

from app.adapters.base import AdapterError
from app.adapters.factory import (
    get_geocoding_adapter,
    get_llm_adapter,
    get_places_adapter,
    get_routes_adapter,
    get_transactions_adapter,
)

__all__ = [
    "AdapterError",
    "get_geocoding_adapter",
    "get_llm_adapter",
    "get_places_adapter",
    "get_routes_adapter",
    "get_transactions_adapter",
]
