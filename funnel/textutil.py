import re
from html.parser import HTMLParser


class _Text(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts, self.skip = [], 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self.skip += 1
        elif tag in ("br", "p", "div", "tr", "li"):
            self.parts.append("\n")
        elif tag == "a":
            href = dict(attrs).get("href")
            if href and href.startswith("http"):
                self.parts.append(f" {href} ")  # keep link targets: they often hold the company URL

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self.skip = max(0, self.skip - 1)

    def handle_data(self, data):
        if not self.skip:
            self.parts.append(data)


def html_to_text(html: str) -> str:
    p = _Text()
    p.feed(html or "")
    return re.sub(r"\n{3,}", "\n\n", re.sub(r"[ \t]+", " ", "".join(p.parts))).strip()
