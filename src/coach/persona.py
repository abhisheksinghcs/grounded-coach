"""Customer persona for the sales-practice role-play.

The AI plays a prospective *customer* (default: "Emma") that the seller
practices pitching to. The persona can be **tailored** per session with details
about a real customer (name, role, company, website, industry, competitors).

``build_persona_instructions`` turns a :class:`CustomerProfile` into the model
instructions. ``resolve_persona`` picks the active persona: an explicit
``COACH_INSTRUCTIONS`` override wins; otherwise the tailored Emma persona.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# Defaults taken from the course script's "Emma" customer.
DEFAULT_NAME = "Emma"
DEFAULT_RESPONSIBILITIES = (
    "helping your organization respond to complex business issues that span "
    "several teams, systems, and decisions"
)
DEFAULT_PAIN = (
    "your team usually gets the information they need, but it takes too long to "
    "piece it together in a way that helps them quickly decide what needs "
    "attention first"
)
DEFAULT_WANTS = (
    "to see how AI can help your team keep up with the pace of change while "
    "staying in control of AI-assisted work"
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
    """Tailorable details of the customer the AI role-plays."""

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
    """Compose the model instructions for the role-played customer."""
    name = profile.name or DEFAULT_NAME
    lines = [
        f"You are {name}, an AI-generated customer in a sales-practice "
        "role-play. The person speaking to you is a seller rehearsing a demo of "
        "Copilot Continuum (part of the Microsoft AI stack). Stay fully in "
        "character as the CUSTOMER being pitched to — you are the buyer, not the "
        "seller. Never pitch, sell, or coach.",
        "",
        "Who you are:",
        f"- {_identity_line(profile)}",
        f"- You are responsible for {profile.responsibilities}.",
        f"- Your pain: {profile.pain}.",
        f"- What you want: {profile.wants}.",
    ]
    if profile.competitors:
        lines.append(
            f"- Alternatives or competitors you weigh: {profile.competitors}."
        )
    if profile.notes:
        lines.append(f"- Additional context about you: {profile.notes}.")
    lines += [
        "",
        "How to behave:",
        "- Be realistic, curious, and gently skeptical, like a busy business "
        "leader. Share your context when asked, ask clarifying questions, and "
        "raise genuine concerns (speed to decision, staying in control of AI, "
        "impact across teams).",
        "- Keep replies short and natural, like real speech. Respond only after "
        "the seller finishes; never interrupt.",
        "- Use the provided GROUNDING context as background on Copilot "
        "Continuum and the demo so your reactions are realistic; do not invent "
        "product capabilities. If the seller makes a claim, probe how it "
        "applies to your situation.",
        "- Stay in character at all times.",
    ]
    return "\n".join(lines)


def resolve_persona(settings: Any, profile: CustomerProfile | None = None) -> str:
    """Active persona: an explicit instructions override wins, else tailored."""
    override = (getattr(settings, "coach_instructions", "") or "").strip()
    if override:
        return override
    return build_persona_instructions(profile or CustomerProfile())
