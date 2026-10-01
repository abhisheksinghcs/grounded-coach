"""Presenter persona for the live demo Q&A.

A human colleague drives the on-screen demo; this app is the **spoken expert
presenter** that answers questions from the audience — a prospective customer
("Emma" by default), which can be **tailored** per session (name, role, company,
website, industry, competitors) so answers fit that audience.

``build_persona_instructions`` turns a :class:`CustomerProfile` (the customer
being presented to) into the presenter's instructions. ``resolve_persona`` picks
the active persona: an explicit ``COACH_INSTRUCTIONS`` override wins; otherwise
the presenter persona tailored to the audience.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

# Defaults describe the audience ("Emma") from the course script.
DEFAULT_NAME = "Emma"
DEFAULT_RESPONSIBILITIES = (
    "responding to complex business issues that span several teams, systems, "
    "and decisions"
)
DEFAULT_PAIN = (
    "their team usually gets the information they need, but it takes too long to "
    "piece it together in a way that helps them quickly decide what needs "
    "attention first"
)
DEFAULT_WANTS = (
    "to understand how AI can help their team keep up with the pace of change "
    "while staying in control of AI-assisted work"
)

_MAX_FIELD = 500


def _clean(value: Any, default: str = "") -> str:
    if not isinstance(value, str):
        return default
    value = value.strip()
    if not value:
        return default
    return value[:_MAX_FIELD]


@dataclass(slots=True)
class CustomerProfile:
    """Tailorable details of the customer the presenter is addressing."""

    name: str = DEFAULT_NAME
    role: str = ""
    company: str = ""
    website: str = ""
    industry: str = ""
    competitors: str = ""
    responsibilities: str = DEFAULT_RESPONSIBILITIES
    pain: str = DEFAULT_PAIN
    wants: str = DEFAULT_WANTS
    notes: str = ""

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "CustomerProfile":
        data = data or {}
        return cls(
            name=_clean(data.get("name"), DEFAULT_NAME) or DEFAULT_NAME,
            role=_clean(data.get("role")),
            company=_clean(data.get("company")),
            website=_clean(data.get("website")),
            industry=_clean(data.get("industry")),
            competitors=_clean(data.get("competitors")),
            responsibilities=_clean(
                data.get("responsibilities"), DEFAULT_RESPONSIBILITIES
            ),
            pain=_clean(data.get("pain"), DEFAULT_PAIN),
            wants=_clean(data.get("wants"), DEFAULT_WANTS),
            notes=_clean(data.get("notes")),
        )

    def is_tailored(self) -> bool:
        return any(
            [self.role, self.company, self.website, self.industry,
             self.competitors, self.notes]
        ) or self.name != DEFAULT_NAME


def _identity_line(p: CustomerProfile) -> str:
    ident = p.name
    if p.role:
        ident += f", a {p.role}"
    if p.company:
        ident += f" at {p.company}"
        if p.website:
            ident += f" ({p.website})"
    if p.industry:
        ident += f", in the {p.industry} industry"
    return ident + "."


def build_persona_instructions(profile: CustomerProfile) -> str:
    """Compose the presenter instructions for answering the audience's questions."""
    lines = [
        "You are the expert presenter and spokesperson for a live demo of "
        "Copilot Continuum (part of the Microsoft AI stack), told through the "
        "Caldova scenario. A colleague is driving the on-screen demo; YOU do "
        "the speaking. Your job is to answer the audience's questions clearly, "
        "confidently, and persuasively.",
        "",
        f"You are presenting to a prospective customer: {_identity_line(profile)}",
        f"- They are responsible for {profile.responsibilities}.",
        f"- Their pain: {profile.pain}.",
        f"- What they want: {profile.wants}.",
    ]
    if profile.competitors:
        lines.append(
            f"- Alternatives or competitors they weigh: {profile.competitors}."
        )
    if profile.notes:
        lines.append(f"- Additional context about them: {profile.notes}.")
    lines += [
        "",
        "How to answer:",
        "- Ground every claim, capability, figure, and demo step ONLY in the "
        "provided GROUNDING context; do not invent anything not supported there.",
        "- Be concise and natural, like a confident expert fielding live Q&A — "
        "short spoken answers, not monologues.",
        "- Connect answers to the customer's situation and the value of the "
        "Microsoft AI stack; be persuasive but always honest.",
        "- If you lack grounding for a question, say so briefly and offer what "
        "you can, or suggest where to follow up — do not guess.",
        "- Do not claim to see the screen; speak to the demo flow and content "
        "from the grounding. Answer only after the speaker finishes; never "
        "interrupt.",
    ]
    return "\n".join(lines)


def resolve_persona(settings: Any, profile: CustomerProfile | None = None) -> str:
    """Active persona: an explicit instructions override wins, else tailored."""
    override = (getattr(settings, "coach_instructions", "") or "").strip()
    if override:
        return override
    return build_persona_instructions(profile or CustomerProfile())
