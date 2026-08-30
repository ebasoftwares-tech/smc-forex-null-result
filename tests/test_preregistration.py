"""`BACKTEST_PROTOCOL.md` section 1 -- the pre-registration, and the numbers in it.

A pre-registration is worthless if it can drift from the configuration that runs. These
tests exist so that it cannot: the declared TUNABLE grid is parsed back out of the schema
field descriptions and compared, and `M` is recomputed rather than trusted.

**`M` is the one number here that changes every significance claim in the project.** It
scales the Deflated Sharpe Ratio and section 5.6's expected-maximum-Sharpe-under-the-null,
so a wrong `M` silently mis-corrects everything. `PARAMETERS.md` section 2 states it three
mutually inconsistent ways -- see `preregistration.py`'s docstring -- which is precisely
why it is computed here.
"""

from __future__ import annotations

import math

import pytest

from bot.config.schema import AppConfig
from bot.research import preregistration as P


def _schema_tunables() -> dict[str, str]:
    """Every field whose description declares it TUNABLE, dotted-path keyed."""
    out: dict[str, str] = {}

    def walk(model, prefix=""):
        for name, field in model.model_fields.items():
            desc = field.description or ""
            if "TUNABLE" in desc:
                out[prefix + name] = desc
            annotation = field.annotation
            if hasattr(annotation, "model_fields"):
                walk(annotation, prefix + name + ".")

    walk(AppConfig)
    return out


# --------------------------------------------------------------- the grid holds


def test_the_declared_grid_matches_the_schema_that_actually_runs():
    """The check `PARAMETERS.md` could not pass.

    Its grid for `disp.min_leg_atr` has five values; the schema declares six, because the
    schema includes `0 (off)`. Two documents disagreeing about a grid is how `M` ends up
    wrong, and `M` discounts every result the project will ever report.
    """
    schema = _schema_tunables()
    for path, values in P.TUNABLE_GRID.items():
        assert path in schema, f"{path} is gridded here but not TUNABLE in the schema"
        parsed = P.parse_schema_grid(schema[path])
        assert parsed is not None, f"{path} has no brace grid in the schema"
        assert len(parsed) == len(values), (
            f"{path}: schema declares {len(parsed)} values {parsed}, "
            f"the pre-registration grids {len(values)} {values}"
        )
        # Compare numerically, so "0 (off)" matches 0.0 and "1.0" matches 1.
        for got, want in zip(parsed, values):
            assert float(got.split()[0]) == pytest.approx(float(want)), (path, got, want)


def test_every_schema_tunable_is_either_gridded_or_excluded_with_a_reason():
    """No TUNABLE may be silently left out of `M`. A parameter that is optimisable and
    uncounted is the exact failure the correction exists to prevent."""
    schema = _schema_tunables()
    for path in schema:
        assert path in P.TUNABLE_GRID or path in P.EXCLUDED, (
            f"{path} is TUNABLE in the schema and neither gridded nor excluded"
        )
    for path, reason in P.EXCLUDED.items():
        assert path in schema, f"{path} is excluded from a grid it was never in"
        assert len(reason) > 40, f"{path} is excluded without a real reason"


def test_the_grid_invents_nothing_the_schema_does_not_declare():
    schema = _schema_tunables()
    assert set(P.TUNABLE_GRID) | set(P.EXCLUDED) == set(schema)


def test_the_deferred_bias_parameter_is_genuinely_absent_from_the_schema():
    """`PARAMETERS.md` counts `bias.min_score` as the eighth TUNABLE. There is no `bias`
    section in the schema at all, so `M` today excludes it -- and saying so in advance is
    what stops the grid widening quietly when Phases 2-4 land."""
    schema = _schema_tunables()
    for path in P.DEFERRED:
        assert path not in schema
    assert "bias.min_score" in P.DEFERRED


# ------------------------------------------------------------------ M is computed


def test_M_is_the_full_cartesian_product_with_no_undocumented_removals():
    """`PARAMETERS.md` declares 6,912 "after removing dominated combinations" and states
    no rule for which are dominated, so the number cannot be recomputed by anyone. The
    full product can be, and it corrects harder, which is the only safe direction for a
    number whose job is to discount our own best result."""
    assert P.M == math.prod(len(v) for v in P.TUNABLE_GRID.values())
    assert P.M == 9_600
    assert P.M != 6_912, "the unreproducible figure must not creep back in"
    assert P.M != 8_000, "8,000 drops disp.min_leg_atr's sixth grid point"


def test_M_grows_by_exactly_the_bias_grid_when_the_bias_engine_lands():
    assert P.M_WITH_BIAS == P.M * 5


def test_risk_percent_is_excluded_because_it_cannot_move_the_primary_metric(cfg):
    """Not a convention -- a consequence. SPEC 18.1's purity invariant means position size
    scales PnL and cannot change R, and R-expectancy is the primary metric.

    Asserted against the real sizing function rather than quoted from the spec.
    """
    from bot.core.risk import position_size
    from bot.core.stops import symbol_spec

    spec = symbol_spec(cfg, "EURUSD")
    sized = {
        pct: position_size(
            10_000.0, pct, 0.0030,
            spec=spec, value_per_unit=spec.contract_size,
            min_realised_fraction=cfg.risk.min_realised_fraction,
        )
        for pct in (0.10, 0.35, 0.50)
    }
    lots = [s.lots for s in sized.values()]
    assert len(set(lots)) == len(lots), "risk percent must scale position size"

    # Every one risks at most its own nominal, so R -- (PnL / risk_amount) -- is invariant
    # to the choice: doubling risk_pct doubles both the numerator and the denominator.
    for pct, s in sized.items():
        assert s.realised_risk <= 10_000.0 * pct / 100.0 + 1e-9
    # Lots rise monotonically with the percent, and each realises very nearly its own
    # nominal risk -- so `risk_amount` scales with `risk_pct` and R = PnL / risk_amount
    # does not move. The ratio is not exactly 5x because lots are floored to the 0.01
    # grid (D-014), which is a rounding effect on the equity curve, not on R.
    assert sized[0.10].lots < sized[0.35].lots < sized[0.50].lots
    for pct, s in sized.items():
        nominal = 10_000.0 * pct / 100.0
        assert s.realised_risk / nominal >= cfg.risk.min_realised_fraction


# ------------------------------------------------------- splits and acceptance


def test_the_split_is_a_rule_rather_than_dates(cfg):
    """Item 4 of section 1 is the only one that cannot be a literal today, because no data
    has been acquired. A rule is as binding as a date and can be committed now."""
    assert P.SPLITS.in_sample_years == 4.0
    assert P.SPLITS.out_of_sample_years == 2.0
    assert P.SPLITS.holdout == "remainder"
    assert "chronological" in P.SPLITS.ordering
    assert len(P.SPLITS.required_regimes) == 3


def test_the_symbol_split_partitions_the_configured_universe(cfg):
    """Section 2.1's cross-sectional out-of-sample. If a symbol were in neither list it
    would be silently untested; in both, it would be development data called validation."""
    dev, val = set(P.DEV_SYMBOLS), set(P.VALIDATION_SYMBOLS)
    assert not (dev & val)
    assert dev | val == set(cfg.symbols)
    assert len(dev) == 3 and len(val) == 7


def test_the_go_no_go_table_carries_section_10_1s_eleven_rows():
    assert len(P.GO_NO_GO) == 11
    names = {c.name for c in P.GO_NO_GO}
    for required in ("Expectancy", "Falsification suite", "Deflated Sharpe", "Plateau"):
        assert required in names


def test_the_deflated_sharpe_row_carries_the_computed_M():
    """The row that would otherwise quote a stale number from a document."""
    row = next(c for c in P.GO_NO_GO if c.name == "Deflated Sharpe")
    assert str(P.M) in row.threshold


def test_the_falsification_row_requires_both_currencies():
    """This pre-registration's decision on D-016 section 1, pinned so it cannot be
    softened after a result. Net R alone can be cleared on stop width -- proved on a
    random walk -- and gross R alone ignores that a strategy has to pay its costs."""
    row = next(c for c in P.GO_NO_GO if c.name == "Falsification suite")
    assert "both" in row.threshold.lower()
    assert "gross" in row.threshold and "net" in row.threshold


def test_the_primary_metric_and_threshold_agree_with_the_protocol():
    assert P.PRIMARY_THRESHOLD_R == 0.10
    assert "expectancy in R" in P.PRIMARY_METRIC
    assert "1.5" in P.PRIMARY_METRIC, "section 10.1 evaluates at cost.multiplier = 1.5"
    expectancy = next(c for c in P.GO_NO_GO if c.name == "Expectancy")
    assert "+0.10 R" in expectancy.threshold


def test_the_equivalence_margin_is_shared_with_the_studies_that_use_it():
    """The falsification suite and the ablation matrix both declare 0.10 R, and it is
    section 10.1's own go/no-go threshold. Three places, one number, by construction."""
    from bot.research.falsification import EQUIVALENCE_MARGIN_R

    assert EQUIVALENCE_MARGIN_R == P.PRIMARY_THRESHOLD_R


# --------------------------------------- the document may not drift from the code
#
# Section 5.1 of the document criticises `PARAMETERS.md` for stating `M` in prose that the
# schema contradicts. Writing a second document that can do the same thing would reproduce
# the exact failure. These parse the committed document and compare it to the code.


def _prereg_text() -> str:
    from pathlib import Path

    import bot.config.schema as schema

    root = Path(schema.__file__).resolve().parents[2]
    return (root / "docs" / "PRE_REGISTRATION.md").read_text(encoding="utf-8")


def test_the_document_states_the_M_the_code_computes():
    """The one number that scales every significance claim in the project."""
    import re

    text = _prereg_text()
    stated = {int(m.replace(",", "")) for m in re.findall(r"`?M`? = ([\d,]+)", text)}
    assert P.M in stated, f"document states M as {stated}, code computes {P.M}"
    assert f"{P.M:,}" in text
    assert f"{P.M_WITH_BIAS:,}" in text, (
        "the document must state what M becomes when the bias engine lands"
    )
    # And the superseded figures appear only where they are being corrected.
    assert "6,912" in text and "8,000" in text, (
        "the document must record the numbers it supersedes, or the correction is invisible"
    )


def test_the_document_grids_exactly_what_the_code_grids():
    """Every gridded parameter is named in the document, and nothing else is."""
    text = _prereg_text()
    for path in P.TUNABLE_GRID:
        assert f"`{path}`" in text, f"{path} is gridded in code but absent from the document"
    for path in P.EXCLUDED:
        assert f"`{path}`" in text
    for path in P.DEFERRED:
        assert f"`{path}`" in text


def test_the_document_carries_the_split_rule_not_invented_dates():
    """Item 4 is a rule until data is acquired. A literal in-sample year here would mean
    the pre-registration had been completed after seeing the sample."""
    text = _prereg_text()
    assert "earliest 4 years" in text
    assert "next 2 years" in text
    for symbol in P.DEV_SYMBOLS + P.VALIDATION_SYMBOLS:
        assert symbol in text


def test_the_document_defines_inconclusive_as_its_own_verdict():
    """Section 10.1 is binary and would otherwise absorb 'we could not look' into FAIL."""
    text = _prereg_text()
    assert "INCONCLUSIVE" in text
    assert str(P.MIN_OOS_TRADES) in text
    assert "minimum detectable effect" in text


def test_the_document_records_what_it_cannot_evaluate():
    """`STATE.md` section 10: never quietly skipped. Each of these is a real gap the
    pre-registration commits to reporting rather than discovering later."""
    text = _prereg_text()
    for gap in ("H6", "tier_confirmation_tf", "killzone", "T2", "projection"):
        assert gap in text, f"the document does not record the {gap} gap"
