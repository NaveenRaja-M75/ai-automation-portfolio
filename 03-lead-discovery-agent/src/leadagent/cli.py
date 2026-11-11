"""Command line interface.

    python -m leadagent run                     # prompts for geography
    python -m leadagent run --geo Chennai,Bengaluru --niches dentist,real_estate
    python -m leadagent run --state "Tamil Nadu" --niches dentist
    python -m leadagent export --min-band 3 --tiers A,B
    python -m leadagent status
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from rich.console import Console
from rich.table import Table

from .budget import Budget
from .config import DB_PATH, OUT_DIR, niches as all_niches, settings
from .export import write_csv
from .geo import all_cities, cities_in_state, resolve_city
from .pipeline import collect_leads, run_targets
from .store import Store

console = Console()


def _resolve_cities(args: argparse.Namespace) -> list[str]:
    if args.state:
        found = cities_in_state(args.state)
        if not found:
            console.print(f"[red]No cities known in state {args.state!r}.[/]")
            console.print("[dim]Add rows to data/cities_in.csv to extend coverage.[/]")
            sys.exit(1)
        return [c.name for c in found]

    if args.all_india:
        return [c.name for c in all_cities(max_tier=args.max_tier)]

    if args.geo:
        names = [n.strip() for n in args.geo.split(",") if n.strip()]
        return [resolve_city(n).name for n in names]

    # Interactive - you asked to be asked before every run.
    console.print("[bold]Geography for this run[/]")
    console.print("[dim]city names (comma separated), or a state name, or 'all'[/]")
    raw = console.input("> ").strip()
    if raw.lower() == "all":
        return [c.name for c in all_cities(max_tier=args.max_tier)]
    found = cities_in_state(raw)
    if found:
        return [c.name for c in found]
    return [resolve_city(n.strip()).name for n in raw.split(",") if n.strip()]


def _resolve_niches(args: argparse.Namespace) -> list[str]:
    available = sorted(all_niches())
    if args.niches:
        if args.niches.strip().lower() == "all":
            return available
        return [n.strip() for n in args.niches.split(",") if n.strip()]
    return list(settings()["run"]["default_niches"])


def cmd_run(args: argparse.Namespace) -> None:
    cities = _resolve_cities(args)
    niche_keys = _resolve_niches(args)
    cfg = settings()

    budget = Budget.from_settings(cfg)
    if args.max_model_calls is not None:
        budget.max_model_calls = args.max_model_calls

    table = Table(title="Run plan", show_header=True)
    table.add_column("Setting")
    table.add_column("Value")
    table.add_row("cities", f"{len(cities)}: {', '.join(cities[:6])}"
                  + (" ..." if len(cities) > 6 else ""))
    table.add_row("niches", ", ".join(niche_keys))
    table.add_row("targets", str(len(cities) * len(niche_keys)))
    table.add_row("discovery source", cfg["discovery"]["source"])
    table.add_row("scoring", "off" if args.no_score else cfg["scoring"]["score_model"])
    table.add_row(
        "ceilings",
        f"{budget.max_discovery_calls} discovery / "
        f"{budget.max_enrichment_fetches} fetches / {budget.max_model_calls} model",
    )
    console.print(table)

    if not args.yes:
        if console.input("Proceed? [y/N] ").strip().lower() not in {"y", "yes"}:
            console.print("Cancelled.")
            return

    store = Store(DB_PATH)
    try:
        if args.reset:
            store.reset_targets()
        store.enqueue(cities, niche_keys)

        run_id = store.start_run(
            {"cities": cities, "niches": niche_keys,
             "source": cfg["discovery"]["source"]}
        )
        stats = run_targets(
            store, budget, skip_scoring=args.no_score, limit_per_target=args.limit
        )
        store.finish_run(run_id, {**stats, **budget.stats()})

        console.rule("[bold green]Run complete")
        _print_stats(stats, budget)
        _print_summary(store)
    finally:
        store.close()


def cmd_export(args: argparse.Namespace) -> None:
    store = Store(DB_PATH)
    try:
        leads = collect_leads(
            store,
            niches=[n.strip() for n in args.niches.split(",")] if args.niches else None,
            cities=[c.strip() for c in args.geo.split(",")] if args.geo else None,
            min_band=args.min_band,
            tiers={t.strip().upper() for t in args.tiers.split(",")},
        )
        out = Path(args.out) if args.out else OUT_DIR / "leads.csv"
        count = write_csv(leads, out)
        console.print(f"[green]Wrote {count} leads to {out}[/]")
        if count == 0:
            console.print(
                "[yellow]Nothing matched. Try --min-band 1 --tiers A,B,C.[/]"
            )
    finally:
        store.close()


def cmd_status(args: argparse.Namespace) -> None:
    store = Store(DB_PATH)
    try:
        _print_summary(store)
    finally:
        store.close()


def _print_stats(stats: dict, budget: Budget) -> None:
    table = Table(show_header=False)
    for key, value in stats.items():
        table.add_row(key, str(value))
    for key, value in budget.stats().items():
        if key != "notes":
            table.add_row(key, str(value))
    console.print(table)


def _print_summary(store: Store) -> None:
    summary = store.summary()
    table = Table(title="Database", show_header=False)
    table.add_row("businesses", str(summary["businesses"]))
    table.add_row("enriched", str(summary["enriched"]))
    table.add_row("scored", str(summary["scored"]))
    table.add_row("targets done", str(summary["targets_done"]))
    table.add_row("targets pending", str(summary["targets_pending"]))
    for tier, count in sorted(summary["tiers"].items()):
        label = "prefiltered out" if tier == "X" else f"tier {tier}"
        table.add_row(label, str(count))
    console.print(table)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="leadagent")
    sub = parser.add_subparsers(dest="command", required=True)

    run_p = sub.add_parser("run", help="discover, enrich and score")
    run_p.add_argument("--geo", help="comma separated city names")
    run_p.add_argument("--state", help="every known city in this state")
    run_p.add_argument("--all-india", action="store_true")
    run_p.add_argument("--max-tier", type=int, default=2,
                       help="with --all-india, include cities up to this tier")
    run_p.add_argument("--niches", help="comma separated niche keys, or 'all'")
    run_p.add_argument("--limit", type=int, help="cap businesses per city+niche")
    run_p.add_argument("--max-model-calls", type=int, help="override the model ceiling")
    run_p.add_argument("--no-score", action="store_true",
                       help="discover and enrich only, no model calls")
    run_p.add_argument("--reset", action="store_true", help="re-run finished targets")
    run_p.add_argument("-y", "--yes", action="store_true", help="skip confirmation")
    run_p.set_defaults(func=cmd_run)

    exp_p = sub.add_parser("export", help="write scored leads to CSV")
    exp_p.add_argument("--min-band", type=int, default=1,
                       help="1-5 estimated monthly budget band")
    exp_p.add_argument("--tiers", default="A,B")
    exp_p.add_argument("--niches")
    exp_p.add_argument("--geo")
    exp_p.add_argument("--out")
    exp_p.set_defaults(func=cmd_export)

    status_p = sub.add_parser("status", help="what is in the database")
    status_p.set_defaults(func=cmd_status)

    args = parser.parse_args(argv)
    args.func(args)
