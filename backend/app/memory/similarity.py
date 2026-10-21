"""Rule-based similarity between investigations. Deterministic point scoring with a stated reason for every
point; this is pattern matching on stored facts, not a learned model, and it never confirms anything."""

from dataclasses import dataclass, field

POINTS = {
    "confirmed_rule": 3,  # both have a confirmed finding from the same rule
    "suspected_rule": 1,  # both have a suspected finding from the same rule
    "metric": 2,  # a metric definition name appears in both
    "source": 2,  # an input source (registered query or identical uploaded file) appears in both
    "definition_difference": 1,  # the same Metric DNA field differs in both
}


@dataclass
class Profile:
    investigation_id: str
    title: str
    confirmed_rules: set[str] = field(default_factory=set)
    suspected_rules: set[str] = field(default_factory=set)
    metrics: set[str] = field(default_factory=set)
    sources: set[str] = field(default_factory=set)
    dna_differences: set[str] = field(default_factory=set)


@dataclass
class Match:
    investigation_id: str
    title: str
    score: int
    reasons: list[str]


def score(a: Profile, b: Profile) -> Match:
    reasons = []
    total = 0
    for name, attr, label in (
        ("confirmed_rule", "confirmed_rules", "both have a confirmed finding from rule"),
        ("suspected_rule", "suspected_rules", "both have a suspected finding from rule"),
        ("metric", "metrics", "both use metric definition"),
        ("source", "sources", "both read source"),
        ("definition_difference", "dna_differences", "in both, the definitions differ in"),
    ):
        for item in sorted(getattr(a, attr) & getattr(b, attr)):
            total += POINTS[name]
            reasons.append(f"{label} {item} (+{POINTS[name]})")
    return Match(b.investigation_id, b.title, total, reasons)


def rank(target: Profile, others: list[Profile], limit: int = 10) -> list[Match]:
    matches = [score(target, o) for o in others if o.investigation_id != target.investigation_id]
    matches = [m for m in matches if m.score > 0]
    return sorted(matches, key=lambda m: (-m.score, m.title, m.investigation_id))[:limit]
