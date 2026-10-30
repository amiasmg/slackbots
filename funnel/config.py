import os
from dataclasses import dataclass

DEFAULT_CHANNEL = "C03BS548340"  # top-of-funnel-pipeline


@dataclass(frozen=True)
class Config:
    slack_bot_token: str = ""
    slack_app_token: str = ""
    channel: str = DEFAULT_CHANNEL
    model: str = "claude-sonnet-5-5"
    affinity_api_key: str = ""
    affinity_list_id: int = 0
    pitchbook_api_key: str = ""
    specter_api_key: str = ""
    dry_run: bool = False


def load_config() -> Config:
    try:
        from dotenv import load_dotenv

        load_dotenv()
    except ImportError:
        pass
    env = os.environ.get
    return Config(
        slack_bot_token=env("SLACK_BOT_TOKEN", ""),
        slack_app_token=env("SLACK_APP_TOKEN", ""),
        channel=env("TOP_OF_FUNNEL_CHANNEL", DEFAULT_CHANNEL),
        model=env("FUNNEL_MODEL", "claude-sonnet-5-5"),
        affinity_api_key=env("AFFINITY_API_KEY", ""),
        affinity_list_id=int(env("AFFINITY_LIST_ID", "0") or 0),
        pitchbook_api_key=env("PITCHBOOK_API_KEY", ""),
        specter_api_key=env("SPECTER_API_KEY", ""),
        dry_run=env("FUNNEL_DRY_RUN", "").lower() in ("1", "true", "yes"),
    )
