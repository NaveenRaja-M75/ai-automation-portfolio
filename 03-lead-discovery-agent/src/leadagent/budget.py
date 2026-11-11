"""Per-run ceilings.

Every external call goes through here. When a ceiling is hit the run raises
BudgetExceeded, the queue records what was finished, and the process exits
cleanly - the next run resumes from the remaining pending targets.
"""

from __future__ import annotations

from dataclasses import dataclass, field


class BudgetExceeded(RuntimeError):
    pass


@dataclass
class Budget:
    max_discovery_calls: int
    max_enrichment_fetches: int
    max_model_calls: int
    discovery_calls: int = 0
    enrichment_fetches: int = 0
    model_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    notes: list[str] = field(default_factory=list)

    def spend_discovery(self, n: int = 1) -> None:
        if self.discovery_calls + n > self.max_discovery_calls:
            raise BudgetExceeded(
                f"discovery call ceiling reached ({self.max_discovery_calls})"
            )
        self.discovery_calls += n

    def spend_enrichment(self, n: int = 1) -> None:
        if self.enrichment_fetches + n > self.max_enrichment_fetches:
            raise BudgetExceeded(
                f"enrichment fetch ceiling reached ({self.max_enrichment_fetches})"
            )
        self.enrichment_fetches += n

    def spend_model(self, n: int = 1) -> None:
        if self.model_calls + n > self.max_model_calls:
            raise BudgetExceeded(f"model call ceiling reached ({self.max_model_calls})")
        self.model_calls += n

    def record_tokens(self, input_tokens: int, output_tokens: int) -> None:
        self.input_tokens += input_tokens
        self.output_tokens += output_tokens

    def stats(self) -> dict[str, int | list[str]]:
        return {
            "discovery_calls": self.discovery_calls,
            "enrichment_fetches": self.enrichment_fetches,
            "model_calls": self.model_calls,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "notes": self.notes,
        }

    @classmethod
    def from_settings(cls, cfg: dict) -> "Budget":
        b = cfg.get("budget", {})
        return cls(
            max_discovery_calls=int(b.get("max_discovery_calls", 200)),
            max_enrichment_fetches=int(b.get("max_enrichment_fetches", 600)),
            max_model_calls=int(b.get("max_model_calls", 500)),
        )
