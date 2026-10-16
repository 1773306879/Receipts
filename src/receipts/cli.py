"""The `receipts` command you type in the terminal."""

import argparse
import sys

from rich.console import Console
from rich.table import Table

from receipts.metrics import METRICS, lookup
from receipts.sec import SecError

DISCLAIMER = "Educational tool. Not investment advice. Figures come from SEC filings; check the linked source."

console = Console()


def readable_money(value):
    """391035000000 -> "$391.04 billion"."""
    for size, word in ((1e12, "trillion"), (1e9, "billion"), (1e6, "million")):
        if abs(value) >= size:
            return f"${value / size:,.2f} {word}"
    return f"${value:,.0f}"


def show_receipt(receipt):
    console.print(
        f"\n[bold]{receipt['name']} ({receipt['ticker']})[/bold] - "
        f"{receipt['label']}, fiscal year {receipt['fiscal_year']}\n"
    )
    table = Table(show_header=False, box=None, padding=(0, 2))
    table.add_column(style="dim")
    table.add_column()
    table.add_row("Official value", f"[bold green]${receipt['value']:,}[/bold green]  ({readable_money(receipt['value'])})")
    period = f"{receipt['period_start']} to {receipt['period_end']} ({receipt['days']} days)"
    if receipt["year_name_source"] == "estimated":
        period += f"\n[yellow]Estimated to be fiscal {receipt['fiscal_year']} from how the company names its other years[/yellow]"
    table.add_row("Period", period)
    table.add_row("Source", f"Form {receipt['form']} filed {receipt['filed']}, accession number {receipt['accession_number']}")
    original = receipt["originally_reported"]
    if original:
        table.add_row(
            "[yellow]Restated[/yellow]",
            f"[yellow]First reported as ${original['value']:,} ({readable_money(original['value'])}) "
            f"in the filing of {original['filed']}. Later reports changed it to the value above.[/yellow]",
        )
    table.add_row("SEC tag", receipt["tag"])
    table.add_row("Meaning", receipt["definition"])
    console.print(table)
    # Printed on their own, never wrapped or shortened, so the links always stay clickable.
    console.print(f"\nOpen the filing: {receipt['link']}", soft_wrap=True)
    if original:
        console.print(f"Original filing: {original['link']}", soft_wrap=True)
    console.print(f"\n[dim]{DISCLAIMER}[/dim]\n")


def main(argv=None):
    parser = argparse.ArgumentParser(prog="receipts", description="Fact-check stock claims against official SEC filings.")
    commands = parser.add_subparsers(dest="command", required=True)
    lookup_command = commands.add_parser("lookup", help="Show one official number from a company's annual report.")
    lookup_command.add_argument("ticker", help="Stock ticker, for example AAPL")
    lookup_command.add_argument("metric", choices=sorted(METRICS), help="What to look up")
    lookup_command.add_argument("year", type=int, help="Fiscal year, for example 2024")
    args = parser.parse_args(argv)

    try:
        receipt = lookup(args.ticker, args.metric, args.year)
    except SecError as error:
        console.print(f"[red]{error}[/red]")
        return 1
    show_receipt(receipt)
    return 0


if __name__ == "__main__":
    sys.exit(main())
