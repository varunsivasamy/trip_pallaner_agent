import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    ollama_url: str
    ollama_model: str
    google_places_key: str
    http_timeout_s: float

    @classmethod
    def from_env(cls) -> "Settings":
        places_key = os.getenv("GOOGLE_PLACES_API_KEY")
        if not places_key:
            raise RuntimeError("GOOGLE_PLACES_API_KEY is required (ratings come from Google Places).")
        return cls(
            ollama_url=os.getenv("OLLAMA_URL", "http://localhost:11434"),
            ollama_model=os.getenv("OLLAMA_MODEL", "llama3.1:8b"),
            google_places_key=places_key,
            http_timeout_s=float(os.getenv("HTTP_TIMEOUT_S", "120")),
        )
