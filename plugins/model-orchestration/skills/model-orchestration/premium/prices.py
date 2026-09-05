#!/usr/bin/env python3
"""The ONE price loader. Every script that prints a dollar figure resolves it here.

Why this file exists (2026-09-02): Google doubled Gemini Flash prices around
2026-09-01 and three scripts kept hardcoded constants from 2026-08-13 —
google_batch.py, google_direct_batch.py, heavy_batch.py all lied 2x low.
The project rule was already written ("No slug, endpoint, price or limit enters
the harness except from models_snapshot.json") — this module is its enforcement.

Guards, in order of severity:
  * REFUSE (SystemExit) when a price block carries `_valid_through` and today is
    past it — promo cliffs (sol-pro 'through 2026-11-21', Google 'through
    2026-12-31') turn a correct price into a 2x lie overnight.
  * WARN loudly when the capture date is older than STALE_DAYS — prices rot;
    the snapshot's own _rule says re-capture after a few weeks.
  * REFUSE when a requested lane/model has no price in the snapshot at all —
    a missing number must never fall back to a remembered one.

Everything returned is ARITHMETIC INPUT, not a meter. The meter, where one
exists (OpenRouter usage.cost), always wins.
"""
from __future__ import annotations

import datetime as _dt
import json
import pathlib
import sys

SNAPSHOT = pathlib.Path(__file__).resolve().parent / "models_snapshot.json"
STALE_DAYS = 30

_cache: dict | None = None


def _load() -> dict:
    global _cache
    if _cache is None:
        if not SNAPSHOT.exists():
            raise SystemExit(f"REFUSING: {SNAPSHOT} not found — no price may come "
                             f"from anywhere else.")
        _cache = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    return _cache


def _date_of(s: str | None) -> _dt.date | None:
    """Capture fields are prose that STARTS with YYYY-MM-DD."""
    if not s:
        return None
    try:
        return _dt.date.fromisoformat(str(s)[:10])
    except ValueError:
        return None


def _guard(name: str, captured: str | None, valid_through: str | None,
           allow_stale: bool = False) -> None:
    today = _dt.date.today()
    vt = _date_of(valid_through)
    if vt and today > vt:
        if not allow_stale:
            raise SystemExit(
                f"REFUSING: price block '{name}' was valid through {vt} and today "
                f"is {today}. The vendor announced a change past that date — "
                f"re-capture models_snapshot.json before spending, or pass "
                f"allow_stale=True after deciding on the record.")
        print(f"WARNING: '{name}' past its valid-through {vt} — proceeding on "
              f"explicit override.", file=sys.stderr)
    cap = _date_of(captured)
    if cap and (today - cap).days > STALE_DAYS:
        print(f"WARNING: price block '{name}' captured {cap} "
              f"({(today - cap).days} d ago, floor {STALE_DAYS} d) — re-capture "
              f"before a large run.", file=sys.stderr)


class Pricing:
    """Per-lane rates plus the arithmetic in one place.

    cost() returns dollars for ONE item. Tiered models (gemini-3.1-pro-preview:
    <=200k vs >200k prompt) switch rates per item, which is why cost lives here
    and not in each caller's loop.
    """

    def __init__(self, name: str, in_per_m: float, out_per_m: float,
                 cached_in_per_m: float = 0.0,
                 tier_threshold_tokens: int = 0,
                 in_per_m_hi: float = 0.0, out_per_m_hi: float = 0.0,
                 meter: str = "arithmetic against models_snapshot.json",
                 discount: bool = True, discount_reason: str = ""):
        self.name = name
        self.in_per_m = in_per_m
        self.out_per_m = out_per_m
        self.cached_in_per_m = cached_in_per_m
        self.tier_threshold_tokens = tier_threshold_tokens
        self.in_per_m_hi = in_per_m_hi or in_per_m
        self.out_per_m_hi = out_per_m_hi or out_per_m
        self.meter = meter
        self.discount = discount
        self.discount_reason = discount_reason

    def rates_for(self, prompt_tokens: int) -> tuple[float, float]:
        if self.tier_threshold_tokens and prompt_tokens > self.tier_threshold_tokens:
            return self.in_per_m_hi, self.out_per_m_hi
        return self.in_per_m, self.out_per_m

    def cost(self, prompt_tokens: int, output_tokens_incl_thinking: int,
             cached_tokens: int = 0) -> float:
        rin, rout = self.rates_for(prompt_tokens)
        uncached = max(0, prompt_tokens - cached_tokens)
        return (uncached * rin
                + cached_tokens * self.cached_in_per_m
                + output_tokens_incl_thinking * rout) / 1e6

    def describe(self) -> str:
        tier = (f"; >{self.tier_threshold_tokens:,} tok: "
                f"{self.in_per_m_hi}/{self.out_per_m_hi}"
                if self.tier_threshold_tokens else "")
        return (f"{self.name}: in ${self.in_per_m}/M out ${self.out_per_m}/M"
                f"{tier}; cached ${self.cached_in_per_m}/M  [{self.meter}]")


def _missing(what: str):
    raise SystemExit(f"REFUSING: {what} is not in models_snapshot.json. A price "
                     f"that is not in the snapshot does not exist — capture it "
                     f"with a dated read before spending.")


# --------------------------------------------------------------------------
# lane resolvers
# --------------------------------------------------------------------------
def google_batch(model: str, allow_stale: bool = False) -> Pricing:
    snap = _load()
    m = snap.get("google_direct", {}).get("models", {}).get(model) or _missing(
        f"google_direct model '{model}'")
    p = m.get("price_per_1m_usd") or _missing(f"price block for '{model}'")
    _guard(f"google_direct/{model}", p.get("_captured") or snap.get("_captured"),
           p.get("_valid_through"), allow_stale)
    if "input_batch" in p:  # flat (flash)
        return Pricing(f"google-batch/{model}", p["input_batch"],
                       p["output_batch_incl_thinking"],
                       cached_in_per_m=p.get("cached_input", 0.0),
                       meter="arithmetic; Google returns NO cost field")
    if "input_batch_le_200k" in p:  # tiered (pro-preview)
        return Pricing(f"google-batch/{model}", p["input_batch_le_200k"],
                       p["output_batch_incl_thinking_le_200k"],
                       cached_in_per_m=p.get("cached_input_le_200k", 0.0),
                       tier_threshold_tokens=200_000,
                       in_per_m_hi=p["input_batch_gt_200k"],
                       out_per_m_hi=p["output_batch_gt_200k"],
                       meter="arithmetic; Google returns NO cost field")
    _missing(f"batch rows in price block for '{model}'")


def google_flex(model: str, allow_stale: bool = False) -> Pricing:
    snap = _load()
    m = snap.get("google_direct", {}).get("models", {}).get(model) or _missing(
        f"google_direct model '{model}'")
    p = m.get("price_per_1m_usd") or _missing(f"price block for '{model}'")
    _guard(f"google_direct/{model}", p.get("_captured") or snap.get("_captured"),
           p.get("_valid_through"), allow_stale)
    if "input_flex" in p:
        return Pricing(f"google-flex/{model}", p["input_flex"],
                       p["output_flex_incl_thinking"],
                       cached_in_per_m=p.get("cached_input", 0.0),
                       meter="arithmetic; Google returns NO cost field")
    if "input_batch_le_200k" in p:  # flex documented identical to batch for pro
        return Pricing(f"google-flex/{model}", p["input_batch_le_200k"],
                       p["output_batch_incl_thinking_le_200k"],
                       cached_in_per_m=p.get("cached_input_le_200k", 0.0),
                       tier_threshold_tokens=200_000,
                       in_per_m_hi=p["input_batch_gt_200k"],
                       out_per_m_hi=p["output_batch_gt_200k"],
                       meter="arithmetic; flex rows documented identical to batch")
    _missing(f"flex rows in price block for '{model}'")


def or_batch(slug: str, allow_stale: bool = False) -> Pricing:
    """`slug` may be the base slug or the `:batch` variant; both resolve."""
    snap = _load()
    models = snap.get("openrouter", {}).get("models", {})
    base = slug[:-len(":batch")] if slug.endswith(":batch") else slug
    m = models.get(base) or models.get(slug) or _missing(f"openrouter model '{slug}'")
    bv = m.get("batch_variant")
    if not bv:
        return Pricing(f"or-batch/{slug}", m.get("prompt_per_1m", 0),
                       m.get("completion_per_1m", 0),
                       meter="OR usage.cost is the REAL meter — arithmetic is a preview",
                       discount=False,
                       discount_reason=f"no batch_variant in snapshot for {base} — "
                                       f"НЕТ СКИДКИ on this lane per the snapshot")
    _guard(f"openrouter/{bv.get('slug', slug)}", m.get("_captured"),
           bv.get("_valid_through"), allow_stale)
    return Pricing(f"or-batch/{bv.get('slug', slug)}", bv["prompt_per_1m"],
                   bv["completion_per_1m"],
                   cached_in_per_m=bv.get("input_cache_read_per_1m", 0.0),
                   meter="OR usage.cost is the REAL meter — arithmetic is a preview")


def openai_direct(model: str, tier: str = "flex", allow_stale: bool = False) -> Pricing:
    """flex and batch are both documented at 50% of standard on this lane."""
    snap = _load()
    m = snap.get("openai_direct", {}).get("models", {}).get(model) or _missing(
        f"openai_direct model '{model}'")
    _guard(f"openai_direct/{model}", snap.get("openai_direct", {}).get("_captured"),
           None, allow_stale)
    mult = 0.5 if tier in ("flex", "batch") else 1.0
    return Pricing(
        f"openai-{tier}/{model}",
        m["prompt_per_1m"] * mult, m["completion_per_1m"] * mult,
        cached_in_per_m=m.get("cached_input_per_1m", 0.0),
        meter="arithmetic; OpenAI returns token counts only — reconcile against "
              "the next-day billing dashboard",
        discount=(mult == 0.5),
        discount_reason="" if mult == 0.5 else "standard tier requested — no discount")


def endpoint(path: str) -> str:
    """Endpoints come from the snapshot too. path like 'openai_direct.batch_tier.create'."""
    node: object = _load()
    for k in path.split("."):
        if not isinstance(node, dict) or k not in node:
            _missing(f"endpoint '{path}'")
        node = node[k]
    return str(node)


# --------------------------------------------------------------------------
if __name__ == "__main__":
    # Self-test: resolve every lane the premium panel names. Any REFUSE here is
    # a real finding about the snapshot, not about this script.
    rows = [
        ("or_batch sol-pro", lambda: or_batch("openai/gpt-5.6-sol-pro:batch")),
        ("or_batch flash", lambda: or_batch("google/gemini-3.7-flash:batch")),
        ("openai flex 5.4", lambda: openai_direct("gpt-5.4", "flex")),
        ("openai flex 5.5", lambda: openai_direct("gpt-5.5", "flex")),
        ("openai batch 5.5", lambda: openai_direct("gpt-5.5", "batch")),
        ("google batch flash", lambda: google_batch("gemini-3.7-flash")),
        ("google batch pro", lambda: google_batch("gemini-3.1-pro-preview")),
        ("google flex pro", lambda: google_flex("gemini-3.1-pro-preview")),
    ]
    for label, fn in rows:
        try:
            p = fn()
            tag = "" if p.discount else "  🔴 НЕТ СКИДКИ: " + p.discount_reason
            print(f"{label:<22} {p.describe()}{tag}")
        except SystemExit as e:
            print(f"{label:<22} REFUSED: {e}")
    # tier arithmetic spot-check: pro-preview 250k prompt must price at the hi tier
    pro = google_batch("gemini-3.1-pro-preview")
    lo = pro.cost(150_000, 10_000)
    hi = pro.cost(250_000, 10_000)
    print(f"\npro tier check: 150k+10k = ${lo:.4f} (expect 0.2100), "
          f"250k+10k = ${hi:.4f} (expect 0.5900)")
