"""Central config loader — reads .env and config/settings.yaml."""

import os
from pathlib import Path

import yaml
from dotenv import load_dotenv

# Load .env from project root
PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")


def load_settings() -> dict:
    """Load and return the settings.yaml as a dict."""
    settings_path = PROJECT_ROOT / "config" / "settings.yaml"
    with open(settings_path, "r") as f:
        return yaml.safe_load(f)


# Convenience accessors
SETTINGS = load_settings()

# API keys
PINECONE_API_KEY = os.getenv("PINECONE_API_KEY")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
GITHUB_REPO = os.getenv("GITHUB_REPO")

# Provider: "groq" | "openai" | "anthropic"
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "openai")


def get_model_for_agent(agent_name: str) -> str:
    """Get the model ID for an agent based on current provider."""
    models_key = f"models_{LLM_PROVIDER}"
    models = SETTINGS.get(models_key, {})
    fallbacks = {
        "groq": "llama-3.3-70b-versatile",
        "openai": "gpt-4o",
        "anthropic": "claude-sonnet-4-6",
    }
    return models.get(agent_name, fallbacks.get(LLM_PROVIDER, "gpt-4o"))
