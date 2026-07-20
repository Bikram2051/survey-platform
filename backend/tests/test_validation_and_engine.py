"""Unit tests: validation contract and estimation math. No database."""
import math

import pytest

from apps.estimation.engine import RakingError, kish_neff, rake, weighted_proportions
from apps.surveys.validation import validate_answers, validate_schema

SCHEMA = {
    "default_language": "ne",
    "languages": ["en", "ne"],
    "questions": [
        {
            "id": "q_consent",
            "type": "select_one",
            "required": True,
            "label": {"en": "Do you consent?", "ne": "के तपाईं सहमत हुनुहुन्छ?"},
            "choices": [
                {"value": "yes", "label": {"en": "Yes", "ne": "हो"}},
                {"value": "no", "label": {"en": "No", "ne": "होइन"}},
            ],
        },
        {
            "id": "q_age_group",
            "type": "select_one",
            "required": True,
            "label": {"en": "Age group", "ne": "उमेर समूह"},
            "choices": [
                {"value": "18_29", "label": {"en": "18-29", "ne": "१८-२९"}},
                {"value": "30_plus", "label": {"en": "30+", "ne": "३०+"}},
            ],
        },
        {
            "id": "q_income",
            "type": "integer",
            "label": {"en": "Monthly income", "ne": "मासिक आम्दानी"},
            "relevant": {"field": "q_consent", "eq": "yes"},
        },
    ],
}


def test_schema_valid():
    assert validate_schema(SCHEMA) == []


def test_schema_catches_structural_errors():
    bad = {
        "default_language": "fr",  # not in languages
        "languages": ["en"],
        "questions": [
            {"id": "a", "type": "select_one", "label": {"en": "x"}},  # no choices
            {"id": "a", "type": "text", "label": {"en": "dup"}},  # duplicate id
            {"id": "b", "type": "text", "label": {"en": "fwd"},
             "relevant": {"field": "zzz", "eq": 1}},  # forward/unknown ref
        ],
    }
    errors = validate_schema(bad)
    assert any("default_language" in e for e in errors)
    assert any("choices required" in e for e in errors)
    assert any("duplicate question id" in e for e in errors)
    assert any("EARLIER question" in e for e in errors)


def test_answers_happy_path():
    ans = {"q_consent": "yes", "q_age_group": "18_29", "q_income": 42000}
    assert validate_answers(SCHEMA, ans) == []


def test_answers_reject_irrelevant_required_badtype_badchoice_unknown():
    errors = validate_answers(
        SCHEMA,
        {"q_consent": "no", "q_income": 1, "q_age_group": "child", "q_bogus": 1},
    )
    joined = " | ".join(errors)
    assert "q_income: answered but not relevant" in joined
    assert "not a valid choice" in joined
    assert "q_bogus: unknown question" in joined

    errors2 = validate_answers(SCHEMA, {"q_consent": "yes", "q_age_group": "18_29", "q_income": "lots"})
    assert any("must be an integer" in e for e in errors2)

    errors3 = validate_answers(SCHEMA, {})
    assert "q_consent: required" in errors3


def test_kish_neff_equal_weights_is_n():
    assert kish_neff([2.0, 2.0, 2.0, 2.0]) == pytest.approx(4.0)


def test_weighted_proportions_hand_computed():
    # yes: w 3, no: w 1 -> p_yes 0.75; n_eff = (4^2)/(9+1) = 1.6
    r = weighted_proportions(["yes", "no"], [3.0, 1.0])
    assert r["estimates"]["yes"]["proportion"] == pytest.approx(0.75)
    assert r["n_eff"] == pytest.approx(1.6)
    assert r["deff"] == pytest.approx(2 / 1.6)
    se = math.sqrt(0.75 * 0.25 / 1.6)
    assert r["estimates"]["yes"]["se"] == pytest.approx(se)


def test_rake_recovers_margins():
    rows = [
        {"age": "young", "region": "hill"},
        {"age": "young", "region": "terai"},
        {"age": "old", "region": "hill"},
        {"age": "old", "region": "terai"},
    ]
    margins = {
        "age": {"young": 0.7, "old": 0.3},
        "region": {"hill": 0.55, "terai": 0.45},
    }
    weights, diag = rake(rows, margins, [1.0, 1.0, 1.0, 1.0])
    assert diag["converged"], diag
    total = sum(weights)
    young = sum(w for r, w in zip(rows, weights) if r["age"] == "young")
    hill = sum(w for r, w in zip(rows, weights) if r["region"] == "hill")
    assert young / total == pytest.approx(0.7, abs=1e-5)
    assert hill / total == pytest.approx(0.55, abs=1e-5)
    assert total == pytest.approx(4.0, rel=1e-6)  # raking preserves total weight


def test_rake_impossible_cell_fails_loudly():
    rows = [{"age": "young"}, {"age": "young"}]
    with pytest.raises(RakingError):
        rake(rows, {"age": {"young": 0.6, "old": 0.4}}, [1.0, 1.0])


def test_rake_rejects_bad_margins():
    rows = [{"age": "young"}, {"age": "old"}]
    with pytest.raises(RakingError):
        rake(rows, {"age": {"young": 0.6, "old": 0.6}}, [1.0, 1.0])
