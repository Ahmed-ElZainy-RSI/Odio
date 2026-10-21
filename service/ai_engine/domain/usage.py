"""Paid-call usage and what it costs (spec 025).

Nothing in the system measured cost: the README's "$38 / 36 books" is a projection, and
the provider budget counts calls for rate limiting, not money. Every paid call now leaves
a :class:`UsageRecord` with the units the provider bills on, and a :class:`PriceTable`
(configuration, not code) turns records into dollars.

Rules that keep the numbers honest:
  - a provider-reported cost (OpenRouter returns one) wins over any computation;
  - a cache hit costs 0 and is still counted, so savings are visible;
  - a price that is not in the table is *unknown*, never zero -- a report says how many
    calls it could not price instead of quietly under-reporting;
  - prices carry effective dates, because they change (gemini-3.6-flash doubles on
    2027-01-01).

Stdlib only, like the rest of ``domain/``. A verbatim copy lives at
``src/pipeline/usage_model.py`` for the CLI pipeline; a test keeps the two identical.
"""

from __future__ import annotations

import tomllib
from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass, field
from datetime import UTC, date, datetime
from typing import Any

#: Units a price can be quoted in, and the record field each one multiplies.
PER_UNIT = {
    "input_per_1m_tokens": ("input_tokens", 1_000_000),
    "output_per_1m_tokens": ("output_tokens", 1_000_000),
    "per_1m_chars": ("chars", 1_000_000),
    "per_audio_minute": ("audio_ms", 60_000),
    "per_page": ("pages", 1),
    "per_image": ("images", 1),
}


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


@dataclass(frozen=True, slots=True)
class UsageRecord:
    provider: str
    model: str
    operation: str  # ocr | tts | vlm-arbiter | context-detector | verifier | ...
    at: str = field(default_factory=utc_now)
    stage: str | None = None
    page: int | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    chars: int = 0
    audio_ms: int = 0
    pages: int = 0
    images: int = 0
    reported_cost_usd: float | None = None
    cached: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {k: v for k, v in asdict(self).items() if v not in (None, 0, False)} | {
            "provider": self.provider,
            "model": self.model,
            "operation": self.operation,
            "at": self.at,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> UsageRecord:
        known = {f for f in cls.__dataclass_fields__}  # type: ignore[attr-defined]
        return cls(**{k: v for k, v in data.items() if k in known})


@dataclass(frozen=True, slots=True)
class Price:
    provider: str
    model: str
    rates: Mapping[str, float]
    valid_from: date | None = None
    valid_until: date | None = None  # inclusive
    source: str = ""

    def applies_on(self, day: date) -> bool:
        if self.valid_from is not None and day < self.valid_from:
            return False
        return not (self.valid_until is not None and day > self.valid_until)

    def cost(self, record: UsageRecord) -> float:
        return sum(
            getattr(record, PER_UNIT[name][0]) / PER_UNIT[name][1] * rate
            for name, rate in self.rates.items()
        )


@dataclass(frozen=True, slots=True)
class PriceTable:
    prices: tuple[Price, ...] = ()
    as_of: str = ""

    @classmethod
    def from_toml(cls, text: str) -> PriceTable:
        data = tomllib.loads(text)
        prices = []
        for entry in data.get("price", []):
            rates = {k: float(v) for k, v in entry.items() if k in PER_UNIT}
            unknown = (
                set(entry)
                - set(PER_UNIT)
                - {
                    "provider",
                    "model",
                    "valid_from",
                    "valid_until",
                    "source",
                    "note",
                }
            )
            if unknown:
                raise ValueError(f"unknown price fields {sorted(unknown)} for {entry.get('model')}")
            prices.append(
                Price(
                    provider=entry["provider"],
                    model=entry["model"],
                    rates=rates,
                    valid_from=entry.get("valid_from"),
                    valid_until=entry.get("valid_until"),
                    source=entry.get("source", ""),
                )
            )
        return cls(prices=tuple(prices), as_of=str(data.get("as_of", "")))

    def lookup(self, record: UsageRecord) -> Price | None:
        day = datetime.fromisoformat(record.at).date()
        for price in self.prices:
            if (
                price.provider == record.provider
                and price.model == record.model
                and price.applies_on(day)
            ):
                return price
        return None


def cost_of(record: UsageRecord, prices: PriceTable) -> float | None:
    """Dollars for one record, or None when it cannot be priced."""
    if record.cached:
        return 0.0
    if record.reported_cost_usd is not None:
        return record.reported_cost_usd
    price = prices.lookup(record)
    return None if price is None else price.cost(record)


@dataclass(slots=True)
class CostLine:
    calls: int = 0
    cached: int = 0
    unpriced: int = 0
    usd: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    chars: int = 0
    audio_ms: int = 0
    pages: int = 0


def summarize(
    records: Iterable[UsageRecord], prices: PriceTable, *, by: tuple[str, ...] = ("operation",)
) -> dict[tuple[Any, ...], CostLine]:
    """Aggregate records by the given fields (e.g. ("page",) or ("provider", "model"))."""
    lines: dict[tuple[Any, ...], CostLine] = {}
    for record in records:
        line = lines.setdefault(tuple(getattr(record, f) for f in by), CostLine())
        line.calls += 1
        line.cached += record.cached
        for unit in ("input_tokens", "output_tokens", "chars", "audio_ms", "pages"):
            setattr(line, unit, getattr(line, unit) + getattr(record, unit))
        usd = cost_of(record, prices)
        if usd is None:
            line.unpriced += 1
        else:
            line.usd += usd
    return lines
