from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    llama_server_url: str = "http://localhost:8080/v1/chat/completions"
    llama_model_name: str = "ggml-org/gemma-4-E2B-it-GGUF:Q8_0"
    translation_concurrency: int = 1
    llm_timeout_seconds: float = 300.0
    llm_temperature: float = 0.3
    llm_max_retries: int = 3
    source_lang: str = "English"
    target_lang: str = "Brazilian Portuguese (pt-BR)"

    class Config:
        env_prefix = "TRADUZ_"


settings = Settings()
