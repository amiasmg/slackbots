import os
from dataclasses import dataclass

DEFAULT_CHANNEL = "C03BS548340"  # top-of-funnel-pipeline


@dataclass(frozen=True)
class Config:
    slack_bot_token: str = ""
    slack_app_token: str = ""
    channel: str = DEFAULT_CHANNEL
    model: str = "claude-sonnet-5-5"
    web_search_tool: str = "web_search_20250305"
    affinity_api_key: str = ""
    affinity_list_id: int = 0
    stage_field: str = "Stage"
    watchlist_stage: str = "Watchlist"
    owners_field: str = "Owners"
    pitchbook_api_key: str = ""
    specter_api_key: str = ""
    cache_dir: str = ""          # empty = no caching (tests); load_config defaults to .cache
    cache_ttl_hours: int = 72
    # Email intake: Microsoft Graph (preferred) and/or a webhook (e.g. from Zapier)
    ms_tenant_id: str = ""
    ms_client_id: str = ""
    ms_client_secret: str = ""
    mailbox: str = "qed.topfunnel@qedinvestors.com"
    mail_poll_seconds: int = 60
    webhook_port: int = 0        # 0 = webhook off
    webhook_secret: str = ""
    dry_run: bool = False

    @property
    def graph_enabled(self) -> bool:
        return bool(self.ms_tenant_id and self.ms_client_id and self.ms_client_secret)


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
        web_search_tool=env("FUNNEL_WEB_SEARCH_TOOL", "web_search_20250305"),
        affinity_api_key=env("AFFINITY_API_KEY", ""),
        affinity_list_id=int(env("AFFINITY_LIST_ID", "0") or 0),
        stage_field=env("AFFINITY_STAGE_FIELD", "Stage"),
        watchlist_stage=env("AFFINITY_WATCHLIST_STAGE", "Watchlist"),
        owners_field=env("AFFINITY_OWNERS_FIELD", "Owners"),
        pitchbook_api_key=env("PITCHBOOK_API_KEY", ""),
        specter_api_key=env("SPECTER_API_KEY", ""),
        cache_dir=env("FUNNEL_CACHE_DIR", ".cache"),
        cache_ttl_hours=int(env("FUNNEL_CACHE_TTL_HOURS", "72") or 72),
        ms_tenant_id=env("MS_TENANT_ID", ""),
        ms_client_id=env("MS_CLIENT_ID", ""),
        ms_client_secret=env("MS_CLIENT_SECRET", ""),
        mailbox=env("FUNNEL_MAILBOX", "qed.topfunnel@qedinvestors.com"),
        mail_poll_seconds=int(env("FUNNEL_MAIL_POLL_SECONDS", "60") or 60),
        webhook_port=int(env("FUNNEL_WEBHOOK_PORT", "0") or 0),
        webhook_secret=env("FUNNEL_WEBHOOK_SECRET", ""),
        dry_run=env("FUNNEL_DRY_RUN", "").lower() in ("1", "true", "yes"),
    )
