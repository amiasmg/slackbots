"""Deterministic URL handling (replaces the GPT 'fix doubled URL' + Formatter steps)."""
import re

SLACK_LINK = re.compile(r"<(https?://[^|>\s]+)(?:\|[^>]*)?>")
BARE_URL = re.compile(r"https?://[^\s<>|]+")
IGNORED_HOSTS = ("slack.com", "affinity.co", "pitchbook.com", "docs.google.com")


def domain_of(url: str) -> str:
    host = re.sub(r"^\w+://", "", url).split("/")[0].split("?")[0].split(":")[0].lower()
    return host[4:] if host.startswith("www.") else host


def extract_company_urls(text: str) -> list:
    """Return unique [{'url', 'domain'}] found in a Slack message, https-normalised."""
    found = SLACK_LINK.findall(text) or BARE_URL.findall(text)
    seen, out = set(), []
    for raw in found:
        url = raw.rstrip(".,;)")
        domain = domain_of(url)
        if not domain or "." not in domain or domain.endswith(IGNORED_HOSTS) or domain in seen:
            continue
        seen.add(domain)
        out.append({"url": re.sub(r"^http://", "https://", url), "domain": domain})
    return out
