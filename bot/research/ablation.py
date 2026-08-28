"""`BACKTEST_PROTOCOL.md` section 6.5 -- the ablation matrix.

> *"One component toggled at a time against the baseline, each reported as a delta with a
> block-bootstrap CI ... A component whose delta CI spans zero is reported as 'no
> measurable effect' and its default stands. It is not kept because it looked slightly
> positive, and not removed because it looked slightly negative -- both are noise-chasing
> in opposite directions."*

Section 6.5 names **nineteen** components. The first output of building this is an
accounting of how many of them can actually be toggled, because **five cannot** -- and one
of the five is a counterfactual the protocol singles out by name:

| Status | Meaning | Count |
|---|---|---|
| `PAIRED` | Same `Market`, only `run()` differs. Compared setup by setup | 21 variants |
| `UNPAIRED` | The toggle changes the pipeline, so the Market is rebuilt and the setup populations differ | 13 variants |
| `BLOCKED` | The component is specified but its engine is unbuilt (Phases 2-4) | 2 rows |
| `ABSENT` | The component is named in 6.5 and **exists nowhere in the codebase** | 3 rows |

The `ABSENT` rows are the finding. `session filter` and `killzone filter` have no
implementation at all -- `SessionWindowConfig.role` admits `"killzone"`, two killzone
windows are defined in `defaults.yaml` with `enabled: false`, and the only code that reads
`role` is `liquidity_session_names`, which selects *liquidity sources*. Nothing anywhere
filters an entry by session. And `liq.tier_confirmation_tf` -- whose `{'3': 'H1'}` value
section 6.5 calls **"the D-002 counterfactual"** -- is declared, documented as ABLATION,
and read by no module in the project.

---

**Paired and unpaired are not the same measurement, and the difference is large.**

A `PAIRED` ablation changes what the engine does with a setup; the setup stream is
identical, so the delta is computed per setup and its variance is the variance of a
*difference*, which is far smaller than the variance of either arm. An `UNPAIRED` ablation
changes which setups exist, so the comparison is between two populations and the delta
confounds *"this component changed outcomes"* with *"this component changed what we
traded"*. Both are reported, and the `kind` column says which is which, because reading an
unpaired delta as though it were paired is how a filter that merely reduced sample size
gets recorded as one that improved expectancy.

---

**Every row is reported in gross and net R, because D-016 section 1 applies here with
more force than it did to the falsification suite.**

R is a ratio and several of these toggles move its denominator directly: `sl.model` is
*defined* as where the stop goes, `disp.max_leg_bars` moves the S1/S2 anchor,
`choch.reference_mode` picks a nearer reference in `micro`, and `entry.model` moves the
entry price that S4 measures from. An arm with a tighter stop pays a fixed spread over a
smaller denominator and loses more R to costs -- which in net R is indistinguishable from
a worse component. The `median SL` and `cost` columns are what separate the two, and
``cost_explains_it`` names the rows where the two currencies disagree.

---

**On section 6.5's decision rule.** *"A component whose delta CI spans zero is reported as
'no measurable effect' and its default stands"* is a rule about **what to do**, and it is
conservative in the right direction -- do not move a default on noise. It is honoured
literally here. What is added is the three-way verdict from D-010: a CI spanning zero can
mean *"we looked and there is nothing"* (`EQUIVALENT`) or *"we could not see"*
(`UNDERPOWERED`), the default stands either way, and only the first is a finding. Reporting
just "spans zero" would file both under the same sentence.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping, Sequence

import numpy as np

from bot.research.falsification import EQUIVALENCE_MARGIN_R, Arm

#: Section 5.3: *"Stationary block bootstrap (mean block length ~ 20 trading days)."*
BLOCK_SPAN_TRADING_DAYS = 20.0


class Kind(str, Enum):
    PAIRED = "PAIRED"
    UNPAIRED = "UNPAIRED"
    BLOCKED = "BLOCKED"
    ABSENT = "ABSENT"


@dataclass(frozen=True)
class AblationSpec:
    """One toggle, and what section 6.5 called it."""

    row: str
    name: str
    kind: Kind
    overrides: Mapping[str, Any] | None = None
    #: Set when the toggle changes the setup stream and the Market must be rebuilt.
    rebuilds_market: bool = False
    note: str = ""


def _sweep(field: str, value: Any) -> Mapping[str, Any]:
    return {"sweep": {field: value}}


#: Section 6.5's nineteen components, in its own order, with every one accounted for.
MATRIX: tuple[AblationSpec, ...] = (
    # ---------------------------------------------------------------- 1-2: bias
    AblationSpec(
        "MTF gate", "bias.gate_mode", Kind.BLOCKED,
        note="Phases 2-4 unbuilt. `mss.py` takes the gate as an injected predicate "
             "defaulting to always-pass, which IS `gate_mode = none` -- so the baseline "
             "already runs the control arm and there is no other arm to run. Every count "
             "in the project is an upper bound for this reason.",
    ),
    AblationSpec(
        "counter-monthly rule", "bias.counter_monthly", Kind.BLOCKED,
        note="Same. There is no `bias` section in the schema at all; "
             "`liq.rank_bias_weight` and `risk`'s counter-bias field are wired and inert.",
    ),
    # ---------------------------------------------------------- 3: sweep filters
    AblationSpec("each sweep filter", "sweep.max_penetration_atr = 0.5", Kind.UNPAIRED,
                 _sweep("max_penetration_atr", 0.5), True),
    AblationSpec("each sweep filter", "sweep.max_penetration_atr = 2.0", Kind.UNPAIRED,
                 _sweep("max_penetration_atr", 2.0), True,
                 note="Admits 66 more confirmed sweeps and 3 more setups over the three "
                      "report years; above 2.0 nothing changes at all. Not evidence the "
                      "cap is idle -- at the default it rejects 460 sweeps as "
                      "OVER_PENETRATION, and those rejections reappear as "
                      "ACCEPTED_THROUGH as the cap rises (156 -> 700 -> 1015), which is "
                      "the near-substitution SPEC 9.2 warns about. 'This filter does "
                      "nothing' and 'this filter changes nothing' are different "
                      "statements, and only the second is true here."),
    AblationSpec("each sweep filter", "sweep.min_wick_ratio = 0.3", Kind.UNPAIRED,
                 _sweep("min_wick_ratio", 0.3), True),
    AblationSpec("each sweep filter", "sweep.min_close_position = 0.5", Kind.UNPAIRED,
                 _sweep("min_close_position", 0.5), True),
    AblationSpec("each sweep filter", "sweep.max_confirmation_bars = 1", Kind.UNPAIRED,
                 _sweep("max_confirmation_bars", 1), True),
    AblationSpec("each sweep filter", "sweep.max_confirmation_bars = 5", Kind.UNPAIRED,
                 _sweep("max_confirmation_bars", 5), True),
    # ------------------------------------------------ 4: displacement requirement
    AblationSpec("displacement requirement", "disp.mode = bar", Kind.UNPAIRED,
                 {"disp": {"mode": "bar"}}, True),
    AblationSpec("displacement requirement", "disp.min_leg_atr = 0 (off)", Kind.UNPAIRED,
                 {"disp": {"min_leg_atr": 0.0}}, True,
                 note="The nearest expressible 'requirement off'. The clause cannot be "
                      "removed outright -- `Market.setups` filters on "
                      "`displacement.confirmed` -- so this relaxes its magnitude term "
                      "while `require_fvg` and `min_body_ratio` still bind."),
    AblationSpec("displacement requirement", "disp.max_leg_bars = 5", Kind.UNPAIRED,
                 {"disp": {"max_leg_bars": 5}}, True),
    # ----------------------------------------------------- 5: FVG requirement
    AblationSpec("FVG requirement", "disp.require_fvg = False", Kind.UNPAIRED,
                 {"disp": {"require_fvg": False}}, True),
    # ------------------------------------------------ 6-7: session and killzone
    AblationSpec(
        "session filter", "(none)", Kind.ABSENT,
        note="No execution-side session filter exists. `SessionWindowConfig.role` is read "
             "only by `liquidity_session_names`, which picks liquidity SOURCES. No module "
             "gates an entry on the session it fires in.",
    ),
    AblationSpec(
        "killzone filter", "(none)", Kind.ABSENT,
        note="`role: killzone` is an accepted value and LONDON_KZ / NY_KZ are defined in "
             "`defaults.yaml` with `enabled: false`, but nothing reads them. Enabling them "
             "would only add two more session windows, not a filter.",
    ),
    # ------------------------------------------------------------ 8-11: management
    AblationSpec("break-even", "manage.be_trigger_r = 1.0", Kind.PAIRED,
                 {"manage": {"be_trigger_r": 1.0}}),
    AblationSpec("break-even", "manage.be_trigger_r = 1.5", Kind.PAIRED,
                 {"manage": {"be_trigger_r": 1.5}},
                 note="No trade reaches 1.5R before closing, so the trigger never fires."),
    AblationSpec("trailing", "manage.trail_mode = structure", Kind.PAIRED,
                 {"manage": {"trail_mode": "structure"}},
                 note="Never engages: trailing starts at `trail_start_r` and no trade "
                      "gets there."),
    AblationSpec("trailing", "manage.trail_mode = atr", Kind.PAIRED,
                 {"manage": {"trail_mode": "atr"}}),
    AblationSpec("time stop", "exit.max_bars_in_trade = 15", Kind.PAIRED,
                 {"exit": {"max_bars_in_trade": 15}},
                 note="No trade in the fixture lives long enough for any horizon to bind, "
                      "so 15, 30, 60 and off are all the same run."),
    AblationSpec("time stop", "exit.max_bars_in_trade = 60", Kind.PAIRED,
                 {"exit": {"max_bars_in_trade": 60}},
                 note="See the 15-bar row: the horizon never binds at any value."),
    AblationSpec("time stop", "exit.max_bars_in_trade = 9999 (off)", Kind.PAIRED,
                 {"exit": {"max_bars_in_trade": 9999}},
                 note="Section 6.5 lists 'off' as a value; the field is `ge=1` so off is "
                      "expressed as a horizon longer than the fixture."),
    AblationSpec("weekend exit", "exit.close_before_weekend = False", Kind.PAIRED,
                 {"exit": {"close_before_weekend": False}}),
    # ------------------------------------------------------------ 12-15: models
    AblationSpec("each entry model", "entry.model = A", Kind.PAIRED,
                 {"entry": {"model": "A"}}),
    AblationSpec("each entry model", "entry.model = B", Kind.PAIRED,
                 {"entry": {"model": "B"}}),
    AblationSpec("each entry model", "entry.model = D", Kind.PAIRED,
                 {"entry": {"model": "D"}}),
    AblationSpec("each entry model", "entry.model = E", Kind.PAIRED,
                 {"entry": {"model": "E"}}),
    AblationSpec("each SL model", "sl.model = S2", Kind.PAIRED,
                 {"sl": {"model": "structural_swing"}}),
    AblationSpec("each SL model", "sl.model = S3", Kind.PAIRED,
                 {"sl": {"model": "order_block"}}),
    AblationSpec("each SL model", "sl.model = S4", Kind.PAIRED,
                 {"sl": {"model": "atr"}}),
    AblationSpec("each TP model", "tp.model = T2", Kind.PAIRED,
                 {"tp": {"model": "opposing_liquidity"}}),
    AblationSpec("each TP model", "tp.model = T3", Kind.PAIRED,
                 {"tp": {"model": "partial_ladder"}},
                 note="D-014 item 4: T3's `tp_1` is the 1R rung against `tp.min_rr` = 1.5, "
                      "so section 17.2 rejects it on every setup. Expected to arm nothing."),
    AblationSpec("each TP model", "tp.model = T4", Kind.PAIRED,
                 {"tp": {"model": "structure_trail"}},
                 note="D-014: T4 is exempt from the RR gate (no `tp_1` to measure), so it "
                      "accepts setups T1-T3 reject. Its population is not the others'."),
    AblationSpec("each OB definition", "ob.definition = B", Kind.PAIRED,
                 {"ob": {"definition": "last_down_close_before_break"}},
                 note="Inert at the shipped defaults: entry model C reads an FVG and stop "
                      "S1 reads the sweep extreme, so neither consumes an order block. "
                      "Observable only at entry D or SL S3. See D-017."),
    AblationSpec("each OB definition", "ob.definition = C", Kind.PAIRED,
                 {"ob": {"definition": "extreme_origin"}},
                 note="Inert at the shipped defaults: entry model C reads an FVG and stop "
                      "S1 reads the sweep extreme, so neither consumes an order block. "
                      "Observable only at entry D or SL S3. See D-017."),
    AblationSpec("each OB definition", "ob.definition = D", Kind.PAIRED,
                 {"ob": {"definition": "breaker"}},
                 note="Inert at the shipped defaults: entry model C reads an FVG and stop "
                      "S1 reads the sweep extreme, so neither consumes an order block. "
                      "Observable only at entry D or SL S3. See D-017."),
    # ------------------------------------------------------- 16: reference mode
    AblationSpec("reference_mode", "choch.reference_mode = micro", Kind.UNPAIRED,
                 {"choch": {"reference_mode": "micro"}}, True,
                 note="SPEC 11.1 calls major and micro two separately pre-registered "
                      "STRATEGIES, not a knob. Reported here because 6.5 lists it, but a "
                      "delta between two pre-registered variants is a comparison, not an "
                      "ablation of a component."),
    # -------------------------------------------- 17: the D-002 counterfactual
    AblationSpec(
        "tier -> confirmation-TF map (D-002)", "liq.tier_confirmation_tf = {'3': 'H1'}",
        Kind.ABSENT,
        note="**Section 6.5 names this one explicitly as 'the D-002 counterfactual'.** "
             "`liq.tier_confirmation_tf` is declared in the schema, documented as "
             "ABLATION, and read by no module: `analyse_sweeps` steps the H4 series for "
             "every tier by construction. Running it needs a per-tier confirmation "
             "timeframe in the liquidity and sweep engines, which does not exist.",
    ),
    # -------------------------------------------- 18-19: the D-001 counterfactual
    AblationSpec("day_boundary (D-001)", "tf.day_boundary_tz = America/New_York",
                 Kind.UNPAIRED, {"tf": {"day_boundary_tz": "America/New_York"}}, True,
                 note="Changes the H4 grid itself, so the two arms do not share a bar "
                      "series -- the least paired comparison in the matrix."),
    AblationSpec("tf.sunday_handling", "tf.sunday_handling = standalone_incomplete",
                 Kind.UNPAIRED, {"tf": {"sunday_handling": "standalone_incomplete"}}, True),
)

#: Section 6.5's own enumeration, for the coverage table.
PROTOCOL_ROWS: tuple[str, ...] = (
    "MTF gate", "counter-monthly rule", "each sweep filter",
    "displacement requirement", "FVG requirement", "session filter", "killzone filter",
    "break-even", "trailing", "time stop", "weekend exit", "each entry model",
    "each SL model", "each TP model", "each OB definition", "reference_mode",
    "tier -> confirmation-TF map (D-002)", "day_boundary (D-001)", "tf.sunday_handling",
)


# ------------------------------------------------------------------ block length


def block_length(
    n_obs: int, trading_days: float, span_days: float = BLOCK_SPAN_TRADING_DAYS
) -> int:
    """Mean block length, in observations, spanning ``span_days`` trading days.

    Section 5.3 specifies the block in **calendar terms** -- *"mean block length ~ 20
    trading days"* -- not in observations, and the two are not interchangeable here. An
    arm producing four times as many setups over the same calendar needs four times the
    block length to cover the same twenty days, so a single fixed count would resample one
    arm over a materially different horizon from the other and quietly make the tighter
    arm's CI the narrower one for a reason unrelated to its variance.
    """
    if n_obs <= 0 or trading_days <= 0:
        return 1
    return max(1, int(round(span_days * (n_obs / trading_days))))


def trading_days_in(close_time: np.ndarray) -> float:
    """Distinct UTC dates covered by a bar series -- the calendar the block spans."""
    if close_time.size == 0:
        return 0.0
    return float(len(np.unique((np.asarray(close_time) // 86400).astype(np.int64))))


# ------------------------------------------------------------------- the result


@dataclass(frozen=True)
class AblationResult:
    spec: AblationSpec
    n_base: int
    n_var: int
    base_e: float
    var_e: float
    delta: float
    ci_low: float
    ci_high: float
    verdict: str
    base_e_gross: float
    var_e_gross: float
    gross_delta: float
    gross_ci_low: float
    gross_ci_high: float
    gross_verdict: str
    base_sl_atr: float
    var_sl_atr: float
    base_filled: int
    var_filled: int
    block: int
    p_value: float
    mde: float
    #: Setups whose net R the toggle actually changed.  Zero means the toggle never
    #: engaged -- see ``inert``.
    n_changed: int = -1

    @property
    def inert(self) -> bool:
        """The toggle changed **no trade at all** on this fixture.

        **This is not "no measurable effect" and section 6.5's rule must not be applied to
        it.** A delta of exactly 0.0000 with a CI of [0, 0] reports that a break-even at
        1.5R, or a fifteen-bar time stop, never once triggered -- not that it triggered and
        did not matter. The two produce identical numbers and opposite conclusions: the
        first says the fixture never reached the rule, the second says the rule is
        decoration. Filing them under one sentence is the same error D-014 recorded for
        unreachable defaults and D-016 for guards no test reaches, arriving this time in
        the *output* rather than in the code.

        An inert row is reported as INERT and carries no verdict.
        """
        return self.n_changed == 0

    @property
    def measurable(self) -> bool:
        """Section 6.5's rule: a CI spanning zero is 'no measurable effect'."""
        if self.inert or self.variant_dead:
            return False
        return bool(
            np.isfinite(self.ci_low)
            and np.isfinite(self.ci_high)
            and (self.ci_low > 0.0 or self.ci_high < 0.0)
        )

    @property
    def measurable_gross(self) -> bool:
        if self.inert or self.variant_dead:
            return False
        return bool(
            np.isfinite(self.gross_ci_low)
            and np.isfinite(self.gross_ci_high)
            and (self.gross_ci_low > 0.0 or self.gross_ci_high < 0.0)
        )

    @property
    def thin(self) -> bool:
        """Below section 5.1's reportability floor of 30 trades in the arm.

        *"Any subgroup finding: 100 minimum (30-99 suggestive; <30 not reportable)."*
        A thin row still gets a delta and a CI, because the paired design computes both
        over every setup rather than every trade -- but the number is carried by a handful
        of fills and should be read as an observation, not a measurement.
        """
        return 0 < self.var_filled < 30

    @property
    def variant_dead(self) -> bool:
        """The variant arms **nothing**, so there is no comparison to report.

        Its "delta" is then just the baseline's own expectancy with zero subtracted from
        it, which reads as a small effect and is not one. Two of the four TP models are
        in this state at the shipped defaults -- T3 for the reason D-014 item 4 gives, and
        T2 because no opposing level ever reaches `tp.min_target_rank`.
        """
        return self.base_filled > 0 and self.var_filled == 0

    @property
    def reported_verdict(self) -> str:
        """What the matrix prints.

        The two degenerate states outrank any statistical verdict, because both produce a
        number that looks like a measurement of the component and is not one: INERT is
        "the toggle never fired", NO_TRADES is "the variant never traded".
        """
        if self.inert:
            return "INERT"
        if self.variant_dead:
            return "NO_TRADES"
        return self.verdict

    @property
    def cost_explains_it(self) -> bool:
        """Measurable in net R and not in gross R -- a delta won on stop width.

        D-016 section 1, and it bites harder here: `sl.model` *is* the stop, so the whole
        SL block is at risk of reporting the cost of a geometry as the value of a rule.
        """
        return self.measurable and not self.measurable_gross


# --------------------------------------------------------------------- evaluate


def paired_deltas(base: Arm, variant: Arm, *, gross: bool = False) -> np.ndarray:
    """``base - variant`` per setup, for two arms over the **same** setup stream.

    Index alignment is the contract, not an accident: both arms are built from the same
    ``Market``, whose ``setups`` property returns the same list in the same order, and the
    per-setup arrays are built by walking it. Asserted rather than assumed, because a
    silent length mismatch would compare setup *i* of one arm with setup *i* of another.
    """
    a = base.per_setup_gross if gross else base.per_setup
    b = variant.per_setup_gross if gross else variant.per_setup
    if a.size != b.size:
        raise ValueError(
            f"paired ablation over different setup counts: {a.size} vs {b.size} -- "
            "the toggle changed the setup stream and the comparison is not paired"
        )
    return a - b


def evaluate(
    spec: AblationSpec,
    base: Arm,
    variant: Arm,
    *,
    trading_days: float,
    rng: np.random.Generator,
    n_boot: int = 10_000,
    n_perm: int = 2_000,
    margin: float = EQUIVALENCE_MARGIN_R,
) -> AblationResult:
    """One row of the matrix, in both currencies, with a block-bootstrap CI."""
    from bot.research import stats

    paired = spec.kind is Kind.PAIRED
    blk = block_length(base.per_setup.size, trading_days)

    # How many setups the toggle actually moved.  For an unpaired arm the setup streams
    # differ, so "changed nothing" can only mean the two populations are identical.
    if base.per_setup.size == variant.per_setup.size:
        n_changed = int(np.count_nonzero(base.per_setup != variant.per_setup))
    else:
        n_changed = -1

    if paired:
        d = paired_deltas(base, variant)
        dg = paired_deltas(base, variant, gross=True)
        lo, hi = stats.bootstrap_ci(d, n_boot, rng, block=blk)
        glo, ghi = stats.bootstrap_ci(dg, n_boot, rng, block=blk)
        delta, gross_delta = float(d.mean()), float(dg.mean())
        p = stats.paired_permutation_p(d, n_perm, rng)
        mde = stats.paired_mde(d)
    else:
        a, b = base.per_setup, variant.per_setup
        ga, gb = base.per_setup_gross, variant.per_setup_gross
        blk_v = block_length(b.size, trading_days)
        lo, hi = stats.bootstrap_diff_ci(a, b, n_boot, rng, block_a=blk, block_b=blk_v)
        glo, ghi = stats.bootstrap_diff_ci(
            ga, gb, n_boot, rng, block_a=blk, block_b=blk_v
        )
        delta = float(a.mean() - b.mean()) if a.size and b.size else float("nan")
        gross_delta = float(ga.mean() - gb.mean()) if ga.size and gb.size else float("nan")
        p = stats.permutation_p(a, b, n_perm, rng)
        mde = stats.minimum_detectable_effect(a, b)

    return AblationResult(
        spec=spec,
        n_base=base.per_setup.size,
        n_var=variant.per_setup.size,
        base_e=base.expectancy_per_setup,
        var_e=variant.expectancy_per_setup,
        delta=delta,
        ci_low=lo,
        ci_high=hi,
        verdict=stats.verdict_for(
            lo, hi, margin, base.per_setup.size, variant.per_setup.size
        ).value,
        base_e_gross=base.expectancy_per_setup_gross,
        var_e_gross=variant.expectancy_per_setup_gross,
        gross_delta=gross_delta,
        gross_ci_low=glo,
        gross_ci_high=ghi,
        gross_verdict=stats.verdict_for(
            glo, ghi, margin, base.per_setup.size, variant.per_setup.size
        ).value,
        base_sl_atr=base.median_sl_atr,
        var_sl_atr=variant.median_sl_atr,
        base_filled=base.filled,
        var_filled=variant.filled,
        block=blk,
        p_value=p,
        mde=mde,
        n_changed=n_changed,
    )


def coverage() -> dict[str, list[AblationSpec]]:
    """Section 6.5's rows, each with the specs that cover it."""
    out: dict[str, list[AblationSpec]] = {r: [] for r in PROTOCOL_ROWS}
    for s in MATRIX:
        out.setdefault(s.row, []).append(s)
    return out


def runnable(specs: Sequence[AblationSpec] = MATRIX) -> list[AblationSpec]:
    return [s for s in specs if s.kind in (Kind.PAIRED, Kind.UNPAIRED)]
