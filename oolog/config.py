from pydantic_settings import BaseSettings, SettingsConfigDict


class OOSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="OPENOBSERVE_")

    url: str
    org: str
    user: str
    password: str
