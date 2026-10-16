# Receipts

**Paste a stock claim you saw online, and an AI agent checks each fact against the company's official SEC filings, with a receipt (the exact filing and a link) for every number.**

> Status: work in progress, being built step by step ([the plan](docs/PLAN.md)). Step 1 of 6 is done: looking up official numbers.

## What it does (when finished)

You see a post like *"Company X grew revenue 40% last year, has zero debt, and will be a $5 trillion company by 2028."*
Receipts splits it into separate claims and marks each one:

| Verdict | Meaning |
|---|---|
| **SUPPORTED** | Matches what the company officially reported |
| **CONTRADICTED** | Doesn't match what the company officially reported |
| **CAN'T VERIFY** | The data needed isn't available, so we don't guess |
| **NOT CHECKABLE** | An opinion or a prediction. No filing can confirm the future |

Every verdict comes with a link to the filing it is based on.

## What it does NOT do

- It does **not** say what to buy or sell, give price targets, or predict prices.
- It does **not** use the AI's memory for numbers. Every number comes from the SEC's official data.

## Try it

You need [uv](https://docs.astral.sh/uv/) (a Python project tool).

```bash
cp .env.example .env        # then put your name and email in .env (the SEC requires it)
uv run receipts lookup AAPL revenue 2024
```

This prints Apple's official revenue for fiscal 2024, the dates it covers, which filing it came from, and a link to that filing.

## How it gets the numbers

[SEC EDGAR](https://www.sec.gov/edgar) is the US government's free database of company filings. Public companies must file an annual report (a "10-K") there, and the SEC publishes the numbers in those reports as data that programs can read. Receipts downloads that data, saves a copy on disk so it doesn't ask twice, and picks the right number using [written rules](src/receipts/metrics.py) that are each covered by a test.

That data is messier than it looks. Some things the rules handle:

- **Companies name fiscal years differently.** Walmart calls the year that ended in January 2025 "fiscal 2025"; Home Depot calls the year that ended in February 2025 "fiscal 2024". Receipts follows each company's own naming, and the SEC's year labels in older reports are often off by one, so only recent reports count.
- **Numbers get restated.** Dollar Tree first reported $30.6B in revenue for fiscal 2023, and later $16.8B after selling Family Dollar. Receipts shows both, with links.
- **Labels get misused.** One company uses the "Revenues" label for an empty table cell. If labels disagree, Receipts refuses rather than guesses.

When the data doesn't give one clear answer, Receipts says **CAN'T VERIFY** and explains why. A wrong number is worse than no number.

## Limits so far

- Annual numbers from US companies' 10-K reports only. Foreign companies that file a 20-F aren't supported yet.
- For banks, insurers and real-estate trusts, revenue is only checked when the company reports a single total.
- Only revenue so far. More numbers come in Step 2.

## Built with AI help

This project is being built together with Claude (an AI assistant), step by step, so that I understand and can explain every part.

---

*Educational tool. Not investment advice. Figures come from SEC filings and may contain errors; always check the linked source.*
