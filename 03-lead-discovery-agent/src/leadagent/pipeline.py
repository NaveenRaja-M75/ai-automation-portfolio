"""Discover -> enrich -> score, target by target, checkpointing as it goes."""

from __future__ import annotations

from rich.console import Console
from rich.progress import BarColumn, Progress, TextColumn, TimeElapsedColumn

from .budget import Budget, BudgetExceeded
from .config import niche as get_niche, settings
from .discover import get_source
from .enrich import enrich
from .geo import resolve_city
from .models import Business, Lead, SiteSignals
from .score import score_business
from .store import Store

console = Console()


def run_targets(
    store: Store,
    budget: Budget,
    skip_scoring: bool = False,
    limit_per_target: int | None = None,
) -> dict[str, int]:
    cfg = settings()
    source = get_source(cfg["discovery"]["source"])
    cap = limit_per_target or int(cfg["run"]["max_businesses_per_city_niche"])

    stats = {"discovered": 0, "new": 0, "enriched": 0, "scored": 0, "prefiltered": 0}
    targets = store.pending_targets()
    if not targets:
        console.print("[yellow]No pending targets. Use --reset to re-run finished ones.[/]")
        return stats

    for city_name, niche_key in targets:
        console.rule(f"[bold]{city_name} · {niche_key}")
        try:
            city = resolve_city(city_name)
            niche_spec = get_niche(niche_key)

            found = source(city, niche_spec, budget)[:cap]
            stats["discovered"] += len(found)
            console.print(f"  discovered [bold]{len(found)}[/] businesses")

            new_count = sum(1 for biz in found if store.upsert_business(biz))
            stats["new"] += new_count
            console.print(f"  [bold]{new_count}[/] not seen before")

            _process(store, budget, found, niche_spec, stats, skip_scoring)
            store.mark_target(city_name, niche_key, "done", found=len(found))

        except BudgetExceeded as exc:
            store.mark_target(city_name, niche_key, "pending", error=str(exc))
            console.print(f"[yellow]Budget ceiling hit: {exc}[/]")
            console.print("[yellow]Stopping clean. Re-run to resume from here.[/]")
            budget.notes.append(f"stopped at {city_name}/{niche_key}: {exc}")
            break
        except Exception as exc:  # keep the queue moving past one bad target
            store.mark_target(city_name, niche_key, "failed", error=repr(exc))
            console.print(f"[red]Target failed: {exc!r}[/]")

    return stats


def _process(
    store: Store,
    budget: Budget,
    found: list[Business],
    niche_spec: dict,
    stats: dict[str, int],
    skip_scoring: bool,
) -> None:
    with Progress(
        TextColumn("  [progress.description]{task.description}"),
        BarColumn(),
        TextColumn("{task.completed}/{task.total}"),
        TimeElapsedColumn(),
        console=console,
    ) as progress:
        task = progress.add_task("enrich + score", total=len(found))

        for biz in found:
            key = biz.key()

            site = store.get_site(key)
            if site is None:
                site = enrich(biz, budget)
                store.put_site(key, site)
                if site.fetched:
                    stats["enriched"] += 1

            if not skip_scoring and store.get_score(key) is None:
                score = score_business(biz, site, niche_spec, budget)
                if score is not None:
                    store.put_score(key, score)
                    if score.tier == "X":
                        stats["prefiltered"] += 1
                    else:
                        stats["scored"] += 1

            progress.advance(task)


def collect_leads(
    store: Store,
    niches: list[str] | None = None,
    cities: list[str] | None = None,
    min_band: int = 1,
    tiers: set[str] | None = None,
) -> list[Lead]:
    tiers = tiers or {"A", "B", "C"}
    leads: list[Lead] = []

    for niche_key in niches or [None]:  # type: ignore[list-item]
        for city in cities or [None]:  # type: ignore[list-item]
            for biz in store.businesses(niche=niche_key, city=city):
                key = biz.key()
                score = store.get_score(key)
                if score is None or score.tier not in tiers:
                    continue
                if score.budget_band < min_band:
                    continue
                site = store.get_site(key) or SiteSignals()
                leads.append(Lead(business=biz, site=site, score=score))

    return leads
