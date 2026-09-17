"""Tests for the cost gate.

The projection itself is an assumption and not worth asserting precisely. What
is worth asserting: the gate fires at the right size, the menu never pairs an
effort override with a model that rejects it, and the chosen models survive to
the resume.
"""

from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import estimate  # noqa: E402
import state  # noqa: E402

SPEC = """# Spec

## Worker

Tailor a resume for {jd} starting from {resume}. One page, no Summary.

## Verifier

Check {id} against the master resume. Find a reason to reject it.
"""


def make_units(count, tmp_path):
    units = []
    for i in range(count):
        jd = tmp_path / "jd-{:02d}.txt".format(i)
        jd.write_text("x" * 4000, encoding="utf-8")  # ~1000 tokens of posting
        units.append({"id": "unit-{:02d}".format(i), "jd": str(jd)})
    return units


# --- the gate ------------------------------------------------------------

def test_small_batch_does_not_interrupt(tmp_path):
    projection = estimate.project(make_units(3, tmp_path), SPEC)
    assert projection["ask"] is False


def test_large_batch_stops_to_ask(tmp_path):
    projection = estimate.project(make_units(80, tmp_path), SPEC)
    assert projection["ask"] is True
    assert projection["projected_output_typical"] > projection["ask_above"]


def test_threshold_is_overridable(tmp_path):
    units = make_units(10, tmp_path)
    assert estimate.project(units, SPEC)["ask"] is False
    assert estimate.project(units, SPEC, ask_above=1000)["ask"] is True


def test_the_gate_trips_at_fifty_three_units(tmp_path):
    """Pins the exact boundary, not a bracket around it.

    The old version asserted 50 false and 55 true, which is true of any boundary
    in 51..55 and let the documented figure drift twice: 15, then 55, both wrong.
    The arithmetic is 1500 + 400 = 1900 output tokens per unit, and
    100000 / 1900 = 52.6, so 53 is the first unit count over the gate.
    """
    assert estimate.project(make_units(52, tmp_path), SPEC)["ask"] is False
    assert estimate.project(make_units(53, tmp_path), SPEC)["ask"] is True


def test_worst_case_scales_with_attempts(tmp_path):
    units = make_units(10, tmp_path)
    p2 = estimate.project(units, SPEC, attempts=2)
    p3 = estimate.project(units, SPEC, attempts=3)

    assert p2["projected_output_worst"] == p2["projected_output_typical"] * 2
    assert p3["projected_output_worst"] == p3["projected_output_typical"] * 3
    # Retries do not change the typical case, only the ceiling.
    assert p2["projected_output_typical"] == p3["projected_output_typical"]


# --- the menu ------------------------------------------------------------

def test_menu_never_puts_effort_on_a_model_that_rejects_it(tmp_path):
    """Haiku 4.5 errors on the effort parameter. The menu must not offer it."""
    projection = estimate.project(make_units(5, tmp_path), SPEC)
    for option in projection["options"]:
        for stage in ("worker", "verifier"):
            cfg = option[stage]
            if cfg.get("effort"):
                assert estimate.supports_effort(cfg["model"]), option["key"]


def test_a_bad_pairing_is_rejected_loudly():
    assert estimate.supports_effort("haiku") is False
    assert estimate.supports_effort("sonnet") is True
    assert estimate.supports_effort("opus") is True


def test_downgrades_are_cheaper_and_the_default_is_first(tmp_path):
    projection = estimate.project(make_units(20, tmp_path), SPEC)
    by_key = {o["key"]: o for o in projection["options"]}

    assert projection["options"][0]["key"] == "sonnet-both"
    assert by_key["sonnet-both"]["share_of_default"] == 1.0
    assert by_key["haiku-both"]["cost_usd"] < by_key["haiku-write-sonnet-verify"]["cost_usd"]
    assert by_key["haiku-write-sonnet-verify"]["cost_usd"] < by_key["sonnet-both"]["cost_usd"]
    # An effort change is not a price change, so it must not claim a discount.
    assert by_key["sonnet-low-effort"]["cost_usd"] == by_key["sonnet-both"]["cost_usd"]


def test_sonnet_intro_pricing_expires(tmp_path):
    during = estimate.price_for("sonnet", on=date(2026, 7, 23))
    after = estimate.price_for("sonnet", on=date(2026, 9, 1))

    assert during == (2.00, 10.00)
    assert after == (3.00, 15.00)
    assert estimate.price_for("haiku", on=date(2026, 9, 1)) == (1.00, 5.00)


def test_bigger_payloads_cost_more(tmp_path):
    """Input is measured from the payload files, not assumed."""
    small = make_units(5, tmp_path)
    big_dir = tmp_path / "big"
    big_dir.mkdir()
    big = []
    for i in range(5):
        jd = big_dir / "jd-{}.txt".format(i)
        jd.write_text("x" * 400_000, encoding="utf-8")
        big.append({"id": "unit-{}".format(i), "jd": str(jd)})

    assert (estimate.project(big, SPEC)["projected_input"]
            > estimate.project(small, SPEC)["projected_input"] * 10)


# --- the spec ------------------------------------------------------------

def test_spec_splits_into_worker_and_verifier():
    sections = estimate.split_spec(SPEC)
    assert "One page, no Summary." in sections["worker"]
    assert "Find a reason to reject it." in sections["verifier"]
    assert "Find a reason" not in sections["worker"]


@pytest.mark.parametrize("bad", [
    "## Worker\n\nonly a worker",
    "## Verifier\n\nonly a verifier",
    "# Spec\n\nno sections at all",
])
def test_a_spec_missing_a_section_fails_loudly(bad):
    with pytest.raises(ValueError, match="missing section"):
        estimate.split_spec(bad)


# --- the choice survives the resume --------------------------------------

def test_chosen_models_persist_to_the_resume(tmp_path):
    units = make_units(5, tmp_path)
    projection = estimate.project(units, SPEC)
    cdir = tmp_path / "checkpoint"

    assert estimate.load_config(cdir) is None  # nothing chosen yet

    saved = estimate.save_config(cdir, "haiku-write-sonnet-verify", projection["options"])
    assert saved["worker"] == {"model": "haiku"}

    reloaded = estimate.load_config(cdir)
    assert reloaded["worker"] == {"model": "haiku"}
    assert reloaded["verifier"] == {"model": "sonnet"}
    assert reloaded["choice"] == "haiku-write-sonnet-verify"


def test_a_mangled_config_reads_as_no_choice(tmp_path):
    cdir = tmp_path / "checkpoint"
    cdir.mkdir()
    (cdir / estimate.CONFIG_NAME).write_text('{"choice": "haiku-b', encoding="utf-8")

    assert estimate.load_config(cdir) is None  # falls back to the Sonnet default


def test_an_unknown_choice_is_rejected(tmp_path):
    projection = estimate.project(make_units(3, tmp_path), SPEC)
    with pytest.raises(ValueError, match="unknown choice"):
        estimate.save_config(tmp_path / "ck", "gpt-both", projection["options"])


def test_config_does_not_collide_with_unit_records(tmp_path):
    """The config lives in the checkpoint dir; the reducer must ignore it."""
    units = make_units(3, tmp_path)
    cdir = tmp_path / "checkpoint"
    projection = estimate.project(units, SPEC)
    estimate.save_config(cdir, "sonnet-both", projection["options"])
    for unit in units:
        state.record(cdir, unit["id"], "verified")

    summary = state.report(cdir, units)
    assert summary["complete"] is True
    assert summary["total"] == 3


# --- the CLI the skill calls ---------------------------------------------

def test_cli_project_renders_both_shapes(tmp_path):
    import subprocess

    # 80 units, not 40: at the default assumption of ~1900 output tokens per
    # unit, the 100k gate trips at roughly 53 units.
    units_file = tmp_path / "units.json"
    units_file.write_text(json.dumps(make_units(80, tmp_path)), encoding="utf-8")
    spec_file = tmp_path / "spec.md"
    spec_file.write_text(SPEC, encoding="utf-8")
    script = Path(__file__).resolve().parents[1] / "scripts" / "estimate.py"

    as_json = subprocess.run(
        [sys.executable, str(script), "project", "--units", str(units_file),
         "--spec", str(spec_file)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    assert as_json.returncode == 0
    parsed = json.loads(as_json.stdout.decode())
    assert parsed["ask"] is True
    assert len(parsed["options"]) == 4

    as_text = subprocess.run(
        [sys.executable, str(script), "project", "--units", str(units_file),
         "--spec", str(spec_file), "--text"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    body = as_text.stdout.decode()
    assert "80 units, 160 agents" in body
    assert "Sonnet writer, Sonnet verifier" in body
    assert "Estimate is rough" in body  # never presented as measured


def test_choice_accepts_the_number_the_menu_printed(tmp_path):
    """The --text menu numbers its options, so a number has to be a valid answer."""
    projection = estimate.project(make_units(3, tmp_path), SPEC)
    options = projection["options"]

    by_number = estimate.save_config(str(tmp_path), "2", options)
    assert by_number["choice"] == options[1]["key"]
    # What lands on disk is the slug, never the number.
    assert estimate.load_config(str(tmp_path))["choice"] == options[1]["key"]


def test_choice_still_accepts_the_slug(tmp_path):
    projection = estimate.project(make_units(3, tmp_path), SPEC)
    options = projection["options"]
    saved = estimate.save_config(str(tmp_path), options[0]["key"], options)
    assert saved["choice"] == options[0]["key"]


def test_an_out_of_range_choice_reports_the_slugs(tmp_path):
    projection = estimate.project(make_units(3, tmp_path), SPEC)
    with pytest.raises(ValueError) as exc:
        estimate.save_config(str(tmp_path), "99", projection["options"])
    assert projection["options"][0]["key"] in str(exc.value)


def test_the_menu_prints_the_slug_next_to_each_number(tmp_path):
    projection = estimate.project(make_units(3, tmp_path), SPEC)
    body = estimate.render(projection)
    for option in projection["options"]:
        assert "--choice {}".format(option["key"]) in body
