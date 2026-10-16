"""The `receipts` command you type in the terminal."""

import argparse
import sys

from rich.console import Console
from rich.markup import escape
from rich.table import Table

from receipts.metrics import METRICS, CantVerify, lookup
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
        f"\n[bold]{escape(receipt['name'])} ({escape(receipt['ticker'])})[/bold] - "
        f"{receipt['label']}, fiscal year {receipt['fiscal_year']}\n"
    )
    table = Table(show_header=False, box=None, padding=(0, 2))
    table.add_column(style="dim")
    table.add_column()
    table.add_row("Official value", f"[bold green]${receipt['value']:,}[/bold green]  ({readable_money(receipt['value'])})")
    table.add_row("Period", f"{receipt['period_start']} to {receipt['period_end']} ({receipt['days']} days)")
    naming = receipt["naming"]
    if naming["reports"]:
        table.add_row(
            "Year name",
            f"The company calls this fiscal {receipt['fiscal_year']}. That follows how it names its years "
            f"in {naming['agreeing']} of its {naming['reports']} most recent annual reports.",
        )
    else:
        table.add_row(
            "Year name",
            f"Fiscal {receipt['fiscal_year']}, named after the calendar year it ends in (the usual style; "
            "the SEC data has no year labels for this company to check against).",
        )
    table.add_row("Source", f"Form {receipt['form']} filed {receipt['filed']}, accession number {receipt['accession_number']}")
    original = receipt["originally_reported"]
    if original:
        table.add_row(
            "[yellow]Changed[/yellow]",
            f"[yellow]An earlier report (filed {original['filed']}) gave ${original['value']:,} "
            f"({readable_money(original['value'])}). Later reports changed it to the value above.[/yellow]",
        )
    for other in receipt["also_reported"]:
        table.add_row(
            "Also reported",
            f"The same report also gives ${other['value']:,} ({readable_money(other['value'])}) under the tag "
            f"{other['tag']}. Some people quote that number instead.",
        )
    table.add_row("SEC tag", receipt["tag"])
    table.add_row("Meaning", receipt["definition"])
    console.print(table)
    # Printed on their own, never wrapped or shortened, so the links always stay clickable.
    console.print(f"\nOpen the filing: {receipt['link']}", soft_wrap=True)
    if original:
        console.print(f"Earlier filing: {original['link']}", soft_wrap=True)
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
    except CantVerify as reason:
        console.print(f"[yellow]CAN'T VERIFY:[/yellow] {escape(str(reason))}")
        return 2
    except SecError as error:
        console.print(f"[red]{escape(str(error))}[/red]")
        return 1
    show_receipt(receipt)
    return 0


if __name__ == "__main__":
    sys.exit(main())
