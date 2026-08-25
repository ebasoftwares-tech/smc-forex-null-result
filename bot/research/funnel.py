"""The Phase 9 funnel (SPEC 11.7) -- the project's design decision point.

Every engine built so far feeds one number: how many MSS events the design produces.
`STATE.md` sets the gate at **>= 300 across the universe and >= 120 on the three
development symbols** over the in-sample period, and SPEC 11.7 states the consequence
plainly: *"A funnel that converts 2% of sweeps into MSS will not produce a testable
sample in five years, and that is a design finding to surface in Phase 9, before the
entry engine is built."*

Three things this module is careful about, because each one can make the headline
number wrong in a way that would not be obvious afterwards.

**Stacked levels are one opportunity, not several.**  SPEC 9.4 already says so for
sweeps, and it matters more here: three stacked levels swept by one bar produce three
candidates that break the same reference on the same bar, and counting them
individually would inflate the number the whole design decision rests on -- while
tripling correlated risk rather than sample size.  Every count is therefore reported
both per sweep and **deduplicated per cluster**, and the cluster count is the one that
answers the gate.

**A candidate whose window runs past the end of the data has not failed.**  It is
right-censored, and folding it into the denominator understates the conversion rate.
``NO_WINDOW`` is separated out for that reason.

**Conversion rates travel; counts do not.**  A count from one synthetic year on one
symbol says nothing about a five-year, ten-symbol run except through a rate, and the
rate is only meaningful if the population it is measured over is stated.  ``project()``
does that arithmetic explicitly rather than leaving it to be done in the head of
whoever reads the report -- and it is scaling a rate measured on a random walk, which
is why it reports feasibility of the *sample size* and never of the edge.
"""

from __future__ import annotations

import statistics
from collections import Counter
from dataclasses import dataclass, field
from typing import Iterable, Sequence

from bot.config.schema import AppConfig
from bot.core.bars import BarSeries, from_epoch_s
from bot.core.liquidity import LiquidityBook
from bot.core.mss import Clause, MssResult, Outcome, ReferenceMode, SetupCandidate, analyse_mss
from bot.core.structure import StructureResult
from bot.core.sweeps import SweepCluster, SweepEvent, SweepResult

#: The funnel counts **levels** for its first two stages and **events** for the rest,
#: and the two are not nested: one level can trigger several sweep events over its life
#: (a rejected poke, then a real one), so ``sweeps_triggered`` legitimately exceeds
#: ``levels_swept_or_tested``.  Presenting all seven as one descending chain would show
#: a rise in the middle and invite the reader to look for the bug -- there isn't one,
#: the unit changes.  Split here so that the monotonicity that *is* meaningful can be
#: asserted, and the join between them reported as the fan-out it is.
LEVEL_STAGES = ("levels_created", "levels_swept_or_tested")
EVENT_STAGES = (
    "sweeps_triggered",
    "sweeps_confirmed",
    "reference_found",
    "choch",
    "mss",
)
STAGES = LEVEL_STAGES + EVENT_STAGES


@dataclass
class SymbolYear:
    """One symbol, one year, one reference mode.  The unit everything else pools over."""

    symbol: str
    year: int
    mode: ReferenceMode
    bars: int
    levels_created: int
    levels_swept_or_tested: int
    sweeps_triggered: int
    sweeps_confirmed: int
    result: MssResult
    clusters: Sequence[SweepCluster] = ()

    # ------------------------------------------------------------------ per cluster

    def _cluster_of(self) -> dict[str, str]:
        """Sweep id -> cluster id.  A sweep in no cluster is its own opportunity."""
        out: dict[str, str] = {}
        for cl in self.clusters:
            for e in cl.events:
                out[e.id] = cl.id
        return out

    def deduplicated(self) -> list[SetupCandidate]:
        """One candidate per opportunity (SPEC 9.4).

        Where a cluster produced several candidates the best outcome wins -- an MSS
        over a CHoCH, a CHoCH over an invalidation -- because the question the cluster
        asks is "did this opportunity convert", not "did every level in it convert".
        """
        rank = {
            Outcome.MSS_CONFIRMED: 0,
            Outcome.CHOCH_NOT_MSS: 1,
            Outcome.NEW_EXTREME: 2,
            Outcome.OPPOSING_SWEEP: 3,
            Outcome.CHOCH_TIMEOUT: 4,
            Outcome.REFERENCE_TOO_FAR: 5,
            Outcome.NO_CHOCH_REFERENCE: 6,
            Outcome.NO_WINDOW: 7,
        }
        cid = self._cluster_of()
        best: dict[str, SetupCandidate] = {}
        for c in self.result.candidates:
            key = cid.get(c.sweep.id, c.sweep.id)
            cur = best.get(key)
            if cur is None or rank[c.outcome] < rank[cur.outcome]:
                best[key] = c
        return list(best.values())

    # ---------------------------------------------------------------------- stages

    def stages(self, *, per_cluster: bool = False) -> dict[str, int]:
        cands = self.deduplicated() if per_cluster else self.result.candidates
        return {
            "levels_created": self.levels_created,
            "levels_swept_or_tested": self.levels_swept_or_tested,
            "sweeps_triggered": self.sweeps_triggered,
            "sweeps_confirmed": len(cands),
            "reference_found": sum(1 for c in cands if c.reference_found),
            "choch": sum(1 for c in cands if c.is_choch),
            "mss": sum(1 for c in cands if c.is_mss),
        }


@dataclass
class Funnel:
    """Pooled across symbols and years, for one reference mode."""

    mode: ReferenceMode
    runs: list[SymbolYear] = field(default_factory=list)

    # ----------------------------------------------------------------------- basics

    @property
    def candidates(self) -> list[SetupCandidate]:
        return [c for r in self.runs for c in r.result.candidates]

    @property
    def deduplicated(self) -> list[SetupCandidate]:
        return [c for r in self.runs for c in r.deduplicated()]

    def stages(self, *, per_cluster: bool = False) -> dict[str, int]:
        out = dict.fromkeys(STAGES, 0)
        for r in self.runs:
            for k, v in r.stages(per_cluster=per_cluster).items():
                out[k] += v
        return out

    def symbol_years(self) -> int:
        return len(self.runs)

    def mss_count(self, *, per_cluster: bool = True) -> int:
        pool = self.deduplicated if per_cluster else self.candidates
        return sum(1 for c in pool if c.is_mss)

    # ------------------------------------------------------------------ conversion

    def decided(self, *, per_cluster: bool = True) -> list[SetupCandidate]:
        """Candidates whose window closed inside the data.

        A right-censored candidate is not evidence of anything, and leaving it in the
        denominator understates every rate below.
        """
        pool = self.deduplicated if per_cluster else self.candidates
        return [c for c in pool if c.outcome is not Outcome.NO_WINDOW]

    def conversion(self, *, per_cluster: bool = True) -> dict[str, float]:
        d = self.decided(per_cluster=per_cluster)
        n = len(d) or 1
        ch = sum(1 for c in d if c.is_choch)
        return {
            "sweep_to_reference": sum(1 for c in d if c.reference_found) / n,
            "sweep_to_choch": ch / n,
            "sweep_to_mss": sum(1 for c in d if c.is_mss) / n,
            "choch_to_mss": (sum(1 for c in d if c.is_mss) / ch) if ch else 0.0,
        }

    def outcomes(self, *, per_cluster: bool = True) -> dict[str, int]:
        pool = self.decided(per_cluster=per_cluster)
        c = Counter(x.outcome.value for x in pool)
        return dict(c.most_common())

    def clause_failures(self, *, per_cluster: bool = True) -> dict[str, int]:
        """Which SPEC 11.5 clause stops a CHoCH becoming an MSS.

        Counted independently, so they overlap and do not sum to the CHoCH-not-MSS
        total -- the same convention as the Phase 8 rejection table.  The question is
        which clause binds, not how the failures partition.
        """
        c: Counter[str] = Counter()
        for x in self.decided(per_cluster=per_cluster):
            if x.is_choch and not x.is_mss:
                c.update(cl.value for cl in x.failed_clauses)
        return dict(c.most_common())

    def sole_cause(self, clause: Clause, *, per_cluster: bool = True) -> int:
        """CHoCHs that failed on this clause **and nothing else**.

        The marginal cost of a clause, as opposed to how often it happens to be true
        alongside others -- which is what an independent count measures.
        """
        return sum(
            1
            for x in self.decided(per_cluster=per_cluster)
            if x.is_choch and x.failed_clauses == (clause,)
        )

    # ------------------------------------------------------------------ breakdowns

    def _mss(self, *, per_cluster: bool = True) -> list[SetupCandidate]:
        return [c for c in self.decided(per_cluster=per_cluster) if c.is_mss]

    def by(self, attr: str, *, per_cluster: bool = True) -> dict[str, tuple[int, int, float]]:
        """``key -> (decided sweeps, MSS, conversion)`` for a sweep attribute."""
        dec: Counter[str] = Counter()
        mss: Counter[str] = Counter()
        for c in self.decided(per_cluster=per_cluster):
            v = getattr(c.sweep, attr)
            k = v.value if hasattr(v, "value") else str(v)
            dec[k] += 1
            if c.is_mss:
                mss[k] += 1
        return {
            k: (dec[k], mss[k], mss[k] / dec[k] if dec[k] else 0.0)
            for k in sorted(dec, key=lambda x: -dec[x])
        }

    def by_session(self, series_by_key: dict[tuple[str, int], BarSeries]) -> dict[int, tuple[int, int, float]]:
        """Conversion by the H4 bar's UTC open hour.

        Under D-001 the H4 grid is fixed at 00/04/08/12/16/20 UTC year-round, so the
        open hour *is* the session slot and needs no lookup -- exact and reproducible
        rather than approximately right, the same convention as the sweep study.
        """
        dec: Counter[int] = Counter()
        mss: Counter[int] = Counter()
        for r in self.runs:
            s = series_by_key[(r.symbol, r.year)]
            for c in r.deduplicated():
                if c.outcome is Outcome.NO_WINDOW:
                    continue
                hour = from_epoch_s(s.open_time[c.sweep.confirm_bar]).hour
                dec[hour] += 1
                if c.is_mss:
                    mss[hour] += 1
        return {
            h: (dec[h], mss[h], mss[h] / dec[h] if dec[h] else 0.0) for h in sorted(dec)
        }

    def bars_to_mss(self, *, per_cluster: bool = True) -> list[int]:
        return [
            c.bars_sweep_to_choch
            for c in self._mss(per_cluster=per_cluster)
            if c.bars_sweep_to_choch is not None
        ]

    def bars_to_mss_histogram(self, cfg: AppConfig, *, per_cluster: bool = True) -> dict[int, int]:
        """MSS count at each sweep-to-break distance, over the whole window.

        Reported because the summary statistic SPEC 11.7 asks for -- the median --
        cannot distinguish "the window is generous" from "the window is irrelevant",
        and those imply different things about a TUNABLE parameter.
        """
        c = Counter(self.bars_to_mss(per_cluster=per_cluster))
        return {b: c.get(b, 0) for b in range(0, cfg.choch.max_bars_after_sweep + 1)}

    def window_edge_share(self, cfg: AppConfig, *, per_cluster: bool = True) -> float:
        """SPEC 11.7: *"If the median is at the window edge, the window is doing the
        work rather than the structure."*"""
        b = self.bars_to_mss(per_cluster=per_cluster)
        if not b:
            return 0.0
        return sum(1 for x in b if x >= cfg.choch.max_bars_after_sweep - 1) / len(b)

    def per_month(self) -> dict[str, tuple[int, int, int]]:
        """``YYYY-MM -> (confirmed sweeps, CHoCH, MSS)``, deduplicated per cluster."""
        out: dict[str, list[int]] = {}
        for r in self.runs:
            for c in r.deduplicated():
                if c.outcome is Outcome.NO_WINDOW:
                    continue
                at = c.sweep.at
                k = f"{at.year:04d}-{at.month:02d}"
                row = out.setdefault(k, [0, 0, 0])
                row[0] += 1
                row[1] += int(c.is_choch)
                row[2] += int(c.is_mss)
        return {k: tuple(v) for k, v in sorted(out.items())}

    def spec_6_6_leg_clause_cost(self, *, per_cluster: bool = True) -> tuple[int, int]:
        """``(MSS, of which fail SPEC 6.6's "level beyond the leg extreme")``.

        SPEC 11.5 calls itself complete and omits this clause; SPEC 6.6 states it.
        Adopting 11.5 as operative is a choice, so the cost of the other reading is
        reported as a number rather than argued about.  See D-009.
        """
        m = self._mss(per_cluster=per_cluster)
        return len(m), sum(1 for c in m if c.level_beyond_leg is False)

    # ----------------------------------------------------------------- projection

    def project(self, *, symbols: int, years: float) -> dict[str, float]:
        """Scale the measured per-symbol-year MSS rate to a stated universe.

        This is arithmetic on a rate measured against a random walk.  It answers "is
        the sample size feasible", which is what the Phase 9 gate asks, and it cannot
        answer anything about edge -- see the report's own disclaimer.
        """
        n = self.symbol_years() or 1
        per = self.mss_count(per_cluster=True) / n
        return {
            "mss_per_symbol_year": per,
            "universe": per * symbols * years,
            "development_set": per * 3 * years,
        }


# ------------------------------------------------------------------ construction


def build(
    *,
    symbol: str,
    year: int,
    cfg: AppConfig,
    h4: BarSeries,
    book: LiquidityBook,
    sweeps: SweepResult,
    structure: StructureResult,
    fvgs: Sequence,
    mode: ReferenceMode,
) -> SymbolYear:
    confirmed = sweeps.confirmed()
    return SymbolYear(
        symbol=symbol,
        year=year,
        mode=mode,
        bars=h4.n,
        levels_created=len(book.levels),
        levels_swept_or_tested=len({e.level_id for e in sweeps.events}),
        sweeps_triggered=len(sweeps.events),
        sweeps_confirmed=len(confirmed),
        result=analyse_mss(
            h4, cfg, confirmed, swings=structure.swings, fvgs=fvgs, reference_mode=mode
        ),
        clusters=sweeps.clusters,
    )


def pool(runs: Iterable[SymbolYear], mode: ReferenceMode) -> Funnel:
    return Funnel(mode, [r for r in runs if r.mode is mode])


def median_or_none(values: Sequence[float]) -> float | None:
    return statistics.median(values) if values else None
