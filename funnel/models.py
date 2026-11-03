from dataclasses import dataclass, field


@dataclass
class Attachment:
    filename: str
    content_type: str
    data: bytes

    @property
    def is_pdf(self) -> bool:
        return (self.content_type == "application/pdf" or self.filename.lower().endswith(".pdf")
                or self.data[:4] == b"%PDF")


@dataclass
class Inbound:
    """One request to process, whatever channel it arrived on (Slack, Graph mail, webhook)."""
    source: str            # "slack" | "email"
    text: str
    sender: str = ""       # "Name <email>" of the team member, used to pick an Affinity owner
    subject: str = ""
    attachments: list = field(default_factory=list)
