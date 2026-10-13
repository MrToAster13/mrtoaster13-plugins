"""Tests for the cost gate.

The output sizes are assumptions, so the dollar figures are not asserted to the
cent. What is asserted: the gate fires at the right size, each request is priced
at its own Haiku tier, the menu never pairs an effort override with a model that
rejects it, and the chosen models survive to the resume in a record that
load_config keeps clean.
"""

from __future__ import annotations

import json
import sys
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
    assert estimate.supports_effort("opus") is True


def test_downgrades_are_cheaper_and_the_default_is_first(tmp_path):
    projection = estimate.project(make_units(20, tmp_path), SPEC)
    by_key = {o["key"]: o for o in projection["options"]}

    assert projection["options"][0]["key"] == "haiku-write-opus-verify"
    assert by_key["haiku-write-opus-verify"]["share_of_default"] == 1.0
    assert by_key["haiku-both"]["cost_usd"] < by_key["haiku-write-opus-verify"]["cost_usd"]
    assert by_key["haiku-write-opus-verify"]["cost_usd"] < by_key["opus-both"]["cost_usd"]
    # An effort change is not a price change, so it must not claim a discount.
    assert by_key["opus-low-effort"]["cost_usd"] == by_key["opus-both"]["cost_usd"]


def test_the_menu_offers_only_opus_and_haiku(tmp_path):
    """Subagents run on Opus or Haiku only."""
    assert set(estimate.PRICES) == {"opus", "haiku"}
    assert estimate.price_for("opus") == (4.00, 20.00)
    assert estimate.price_for("haiku") == (0.10, 0.50)
    # Haiku 5.5 charges more once a prompt passes 100K tokens.
    assert estimate.price_for("haiku", 100_000) == (0.10, 0.50)
    assert estimate.price_for("haiku", 100_001) == (0.50, 2.50)
    assert estimate.price_for("opus", 900_000) == (4.00, 20.00)
    projection = estimate.project(make_units(3, tmp_path), SPEC)
    for option in projection["options"]:
        for stage in ("worker", "verifier"):
            assert option[stage]["model"] in ("opus", "haiku"), option["key"]


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

    saved = estimate.save_config(cdir, "haiku-write-opus-verify", projection["options"])
    assert saved["worker"] == {"model": "haiku"}

    reloaded = estimate.load_config(cdir)
    assert reloaded["worker"] == {"model": "haiku"}
    assert reloaded["verifier"] == {"model": "opus"}
    assert reloaded["choice"] == "haiku-write-opus-verify"


def test_an_old_sonnet_config_is_remapped_and_flagged(tmp_path):
    """A checkpoint saved before Sonnet was dropped must not resume on Sonnet."""
    cdir = tmp_path / "checkpoint"
    cdir.mkdir()
    (cdir / estimate.CONFIG_NAME).write_text(json.dumps({
        "choice": "sonnet-both",
        "worker": {"model": "sonnet"},
        "verifier": {"model": "sonnet", "effort": "low"},
    }), encoding="utf-8")

    config = estimate.load_config(cdir)
    assert config["worker"] == {"model": "haiku"}
    assert config["verifier"] == {"model": "opus"}
    assert config["remapped"] == [
        "worker: sonnet -> haiku",
        "verifier: sonnet -> opus",
        "verifier: effort low dropped, it came with a model that is not allowed",
        "choice: sonnet-both -> haiku-write-opus-verify",
    ]
    # The old menu key no longer exists, so the record names the one that matches.
    assert config["choice"] == "haiku-write-opus-verify"


@pytest.mark.parametrize("stage, expected, note", [
    ({"model": "Opus"}, {"model": "opus"}, "worker: Opus -> opus"),
    ({"model": "claude-opus-5-5"}, {"model": "opus"}, "worker: claude-opus-5-5 -> opus"),
    ("opus", {"model": "opus"}, None),
    ({"model": ["opus"]}, {"model": "haiku"}, 'worker: ["opus"] -> haiku'),
    ({"model": None}, {"model": "haiku"}, "worker: null -> haiku"),
    ({}, {"model": "haiku"}, "worker: null -> haiku"),
    (None, {"model": "haiku"}, "worker: null -> haiku"),
    ({"model": "haiku", "effort": "high"}, {"model": "haiku"},
     "worker: effort high dropped, haiku takes no effort override"),
])
def test_a_hand_edited_worker_is_normalised_not_crashed_on(tmp_path, stage, expected, note):
    cdir = tmp_path / "checkpoint"
    cdir.mkdir()
    (cdir / estimate.CONFIG_NAME).write_text(json.dumps({
        "choice": "opus-both", "worker": stage, "verifier": {"model": "opus"},
    }), encoding="utf-8")

    config = estimate.load_config(cdir)
    assert config["worker"] == expected
    assert config["verifier"] == {"model": "opus"}
    worker_notes = [n for n in config.get("remapped", []) if n.startswith("worker:")]
    assert worker_notes == ([note] if note else [])


def test_a_stale_remap_note_in_the_file_is_not_carried_forward(tmp_path):
    cdir = tmp_path / "checkpoint"
    cdir.mkdir()
    (cdir / estimate.CONFIG_NAME).write_text(json.dumps({
        "choice": "opus-both", "worker": {"model": "opus"}, "verifier": {"model": "opus"},
        "remapped": ["worker: sonnet -> haiku"],
    }), encoding="utf-8")
    assert "remapped" not in estimate.load_config(cdir)


def test_a_current_config_carries_no_remap_note(tmp_path):
    projection = estimate.project(make_units(3, tmp_path), SPEC)
    cdir = tmp_path / "checkpoint"
    estimate.save_config(cdir, "opus-low-effort", projection["options"])
    config = estimate.load_config(cdir)
    assert "remapped" not in config
    assert config["verifier"] == {"model": "opus", "effort": "low"}


def test_a_mangled_config_reads_as_no_choice(tmp_path):
    cdir = tmp_path / "checkpoint"
    cdir.mkdir()
    (cdir / estimate.CONFIG_NAME).write_text('{"choice": "haiku-b', encoding="utf-8")

    assert estimate.load_config(cdir) is None  # falls back to the Haiku-writer, Opus-verifier default


def test_an_unknown_choice_is_rejected(tmp_path):
    projection = estimate.project(make_units(3, tmp_path), SPEC)
    with pytest.raises(ValueError, match="unknown choice"):
        estimate.save_config(tmp_path / "ck", "gpt-both", projection["options"])


def test_config_does_not_collide_with_unit_records(tmp_path):
    """The config lives in the checkpoint dir; the reducer must ignore it."""
    units = make_units(3, tmp_path)
    cdir = tmp_path / "checkpoint"
    projection = estimate.project(units, SPEC)
    estimate.save_config(cdir, "opus-both", projection["options"])
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
    assert "Haiku writer, Opus verifier" in body
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


# --- model names are matched on whole words ------------------------------

@pytest.mark.parametrize("raw, expected", [
    ("opus", "opus"),
    (" HAIKU ", "haiku"),
    ("Opus 5.5", "opus"),
    ("opus[1m]", "opus"),
    ("claude-opus-5-5", "opus"),
    ("claude-opus-4-6", "opus"),
    ("us.anthropic.claude-opus-5-5", "opus"),
    ("anthropic.claude-haiku-5-5-v1:0", "haiku"),
    ("claude-opusx", "claude-opusx"),
    ("haikus", "haikus"),
    ("sonnet-opus", "sonnet-opus"),
    ("claude-sonnet-5-5", "claude-sonnet-5-5"),
    ("", ""),
    (5, None),
    (None, None),
])
def test_normalize_model_matches_whole_words_only(raw, expected):
    assert estimate.normalize_model(raw) == expected


def write_config(cdir, payload):
    cdir.mkdir(exist_ok=True)
    (cdir / estimate.CONFIG_NAME).write_text(json.dumps(payload), encoding="utf-8")


@pytest.mark.parametrize("raw, model", [
    ("claude-opusx", "haiku"),
    ("us.anthropic.claude-opus-5-5", "opus"),
    ("Opus 5.5", "opus"),
])
def test_load_config_rejects_a_near_miss_and_maps_a_provider_id(tmp_path, raw, model):
    cdir = tmp_path / "checkpoint"
    write_config(cdir, {"choice": "x", "worker": {"model": raw}, "verifier": {"model": "opus"}})
    config = estimate.load_config(cdir)
    assert config["worker"] == {"model": model}
    assert "worker: {} -> {}".format(raw, model) in config["remapped"]


# --- effort is validated -------------------------------------------------

@pytest.mark.parametrize("effort, kept, note", [
    ("high", "high", None),
    ("LOW", "low", "verifier: effort LOW -> low"),
    (" max ", "max", "verifier: effort  max  -> max"),
    ("xhigh", "xhigh", None),
    ("turbo", None, "verifier: effort turbo dropped, it is not one of low, medium, high, xhigh, max"),
    ("", None, "verifier: effort  dropped, it is not one of low, medium, high, xhigh, max"),
    (3, None, "verifier: effort 3 dropped, it is not one of low, medium, high, xhigh, max"),
    (True, None, "verifier: effort true dropped, it is not one of low, medium, high, xhigh, max"),
    (None, None, None),
])
def test_an_invalid_effort_is_dropped_and_reported(tmp_path, effort, kept, note):
    cdir = tmp_path / "checkpoint"
    write_config(cdir, {"choice": "custom", "worker": {"model": "haiku"},
                        "verifier": {"model": "opus", "effort": effort}})
    config = estimate.load_config(cdir)
    expected = {"model": "opus"}
    if kept:
        expected["effort"] = kept
    assert config["verifier"] == expected
    verifier_notes = [n for n in config.get("remapped", []) if n.startswith("verifier:")]
    assert verifier_notes == ([note] if note else [])


# --- Haiku's price tier is set per request -------------------------------

def test_each_unit_is_priced_on_its_own_prompt(tmp_path):
    """One unit over 100K tokens pays the long tier even when the average does not.

    Pricing per unit makes the projection additive: a batch costs what its
    units cost one at a time. Pricing on the average prompt broke that.
    """
    big = tmp_path / "big.txt"
    big.write_text("x" * 600_000, encoding="utf-8")  # 150K tokens
    units = [{"id": "big", "jd": str(big)}] + make_units(3, tmp_path)

    def haiku_both(batch):
        options = estimate.project(batch, SPEC)["options"]
        return next(o["cost_usd"] for o in options if o["key"] == "haiku-both")

    alone = sum(haiku_both([unit]) for unit in units)
    # Each figure is rounded to the cent, so allow half a cent per figure summed.
    assert haiku_both(units) == pytest.approx(alone, abs=0.005 * (len(units) + 1))
    # The big unit alone is priced at $0.50 per million input tokens, both stages.
    assert haiku_both(units[:1]) >= round(2 * 150_000 * 0.50 / 1e6, 2)


def test_an_empty_batch_costs_nothing(tmp_path):
    projection = estimate.project([], SPEC)
    assert all(o["cost_usd"] == 0 for o in projection["options"])
    assert all(o["share_of_default"] == 1.0 for o in projection["options"])


# --- choice always matches the stages ------------------------------------

@pytest.mark.parametrize("choice, worker, verifier, expected", [
    ("haiku-both", {"model": "opus"}, {"model": "opus"}, "opus-both"),
    (None, {"model": "opus"}, {"model": "opus"}, "opus-both"),
    ("opus-both", {"model": "opus", "effort": "high"}, {"model": "opus"}, "custom"),
    ("custom", {"model": "opus", "effort": "low"}, {"model": "opus", "effort": "low"},
     "opus-low-effort"),
])
def test_a_stale_choice_is_corrected_on_its_own(tmp_path, choice, worker, verifier, expected):
    cdir = tmp_path / "checkpoint"
    payload = {"worker": worker, "verifier": verifier}
    if choice is not None:
        payload["choice"] = choice
    write_config(cdir, payload)
    config = estimate.load_config(cdir)
    assert config["choice"] == expected
    shown = choice if choice is not None else "null"
    assert config["remapped"] == ["choice: {} -> {}".format(shown, expected)]


# --- corrections are written back ----------------------------------------

def test_a_remap_is_written_back_so_the_next_load_is_clean(tmp_path):
    cdir = tmp_path / "checkpoint"
    write_config(cdir, {"choice": "sonnet-both", "worker": {"model": "sonnet"},
                        "verifier": {"model": "Opus", "effort": "HIGH"}})
    first = estimate.load_config(cdir)
    assert first["remapped"]

    on_disk = json.loads((cdir / estimate.CONFIG_NAME).read_text(encoding="utf-8"))
    assert on_disk == {"choice": "custom", "worker": {"model": "haiku"},
                       "verifier": {"model": "opus", "effort": "high"}}

    second = estimate.load_config(cdir)
    assert "remapped" not in second
    assert {k: second[k] for k in on_disk} == on_disk


def test_a_failed_write_back_is_reported_and_the_models_still_load(tmp_path, monkeypatch):
    cdir = tmp_path / "checkpoint"
    write_config(cdir, {"choice": "sonnet-both", "worker": {"model": "sonnet"},
                        "verifier": {"model": "opus"}})

    def refuse(path, payload):
        raise OSError("read-only checkpoint")

    monkeypatch.setattr(estimate.state, "_atomic_write_json", refuse)
    config = estimate.load_config(cdir)
    assert config["worker"] == {"model": "haiku"}
    assert config["remapped"][-1] == "not written back: read-only checkpoint"
    # The file is untouched, so the next load reports the same remap again.
    raw = json.loads((cdir / estimate.CONFIG_NAME).read_text(encoding="utf-8"))
    assert raw["worker"] == {"model": "sonnet"}


def test_a_failed_write_back_leaves_no_temp_file(tmp_path, monkeypatch):
    cdir = tmp_path / "checkpoint"
    write_config(cdir, {"choice": "sonnet-both", "worker": {"model": "sonnet"},
                        "verifier": {"model": "opus"}})

    def deny(src, dst):
        raise PermissionError("denied")

    monkeypatch.setattr(state.os, "replace", deny)
    config = estimate.load_config(cdir)
    assert config["remapped"][-1] == "not written back: denied"
    assert sorted(p.name for p in cdir.iterdir()) == [estimate.CONFIG_NAME]


def test_a_failed_write_after_only_a_tidy_is_still_reported(tmp_path, monkeypatch):
    cdir = tmp_path / "checkpoint"
    write_config(cdir, {"choice": "haiku-write-opus-verify", "worker": "haiku",
                        "verifier": {"model": "opus"}})

    def deny(src, dst):
        raise PermissionError("denied")

    monkeypatch.setattr(state.os, "replace", deny)
    config = estimate.load_config(cdir)
    assert config["worker"] == {"model": "haiku"}
    assert config["remapped"] == ["not written back: denied"]


@pytest.mark.parametrize("raw", ["\u00b2", "\u0662", "\uff11"])
def test_a_non_ascii_digit_is_an_unknown_choice(tmp_path, raw):
    options = estimate.project(make_units(1, tmp_path), SPEC)["options"]
    with pytest.raises(ValueError, match="unknown choice"):
        estimate.resolve_choice(raw, options)


def test_a_clean_record_is_not_rewritten(tmp_path, monkeypatch):
    projection = estimate.project(make_units(3, tmp_path), SPEC)
    cdir = tmp_path / "checkpoint"
    estimate.save_config(cdir, "opus-low-effort", projection["options"])

    def refuse(path, payload):
        raise AssertionError("a clean record was rewritten")

    monkeypatch.setattr(estimate.state, "_atomic_write_json", refuse)
    assert "remapped" not in estimate.load_config(cdir)


def test_extra_stage_keys_are_reported_and_written_back(tmp_path):
    cdir = tmp_path / "checkpoint"
    write_config(cdir, {"choice": "haiku-write-opus-verify",
                        "worker": {"model": "haiku", "maxTurns": 5, "label": "w"},
                        "verifier": {"model": "opus"}})
    config = estimate.load_config(cdir)
    assert config["worker"] == {"model": "haiku"}
    assert config["remapped"] == ["worker: key label dropped", "worker: key maxTurns dropped"]
    on_disk = json.loads((cdir / estimate.CONFIG_NAME).read_text(encoding="utf-8"))
    assert on_disk["worker"] == {"model": "haiku"}
    assert "remapped" not in estimate.load_config(cdir)


@pytest.mark.parametrize("extra", [
    {"remapped": ["worker: sonnet -> haiku"]},
    {"worker": {"model": "haiku", "effort": None}},
    {"worker": "haiku"},
])
def test_a_tidy_with_nothing_to_report_is_still_written_back(tmp_path, extra):
    clean = {"choice": "haiku-write-opus-verify", "worker": {"model": "haiku"},
             "verifier": {"model": "opus"}}
    cdir = tmp_path / "checkpoint"
    write_config(cdir, dict(clean, **extra))
    assert "remapped" not in estimate.load_config(cdir)
    on_disk = json.loads((cdir / estimate.CONFIG_NAME).read_text(encoding="utf-8"))
    assert on_disk == clean


def test_an_effort_with_no_model_gives_the_reason_batch_js_gives(tmp_path):
    cdir = tmp_path / "checkpoint"
    write_config(cdir, {"choice": "haiku-write-opus-verify", "worker": {"model": "haiku"},
                        "verifier": {"effort": "low"}})
    config = estimate.load_config(cdir)
    assert config["verifier"] == {"model": "opus"}
    assert config["remapped"] == ["verifier: null -> opus",
                                  "verifier: effort low dropped, no model was named"]


def test_a_config_saved_with_a_byte_order_mark_still_loads(tmp_path):
    cdir = tmp_path / "checkpoint"
    cdir.mkdir()
    body = json.dumps({"choice": "opus-both", "worker": {"model": "opus"},
                       "verifier": {"model": "opus"}})
    (cdir / estimate.CONFIG_NAME).write_bytes(b"\xef\xbb\xbf" + body.encode("utf-8"))
    config = estimate.load_config(cdir)
    assert config["choice"] == "opus-both"
    assert "remapped" not in config


def test_a_config_read_retries_while_windows_reports_the_file_in_use(tmp_path, monkeypatch):
    cdir = tmp_path / "checkpoint"
    cdir.mkdir()
    (cdir / estimate.CONFIG_NAME).write_text(json.dumps(
        {"choice": "opus-both", "worker": {"model": "opus"}, "verifier": {"model": "opus"}}),
        encoding="utf-8")
    real_open, calls = open, []

    def busy_once(path, *args, **kwargs):
        calls.append(path)
        if len(calls) == 1:
            raise PermissionError("in use")
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(state, "open", busy_once, raising=False)
    assert estimate.load_config(cdir)["choice"] == "opus-both"
    assert len(calls) == 2


def test_a_config_nested_too_deep_reads_as_no_choice(tmp_path):
    cdir = tmp_path / "checkpoint"
    cdir.mkdir()
    depth = 100_000
    (cdir / estimate.CONFIG_NAME).write_text(
        '{"worker": ' + "[" * depth + "]" * depth + ', "verifier": {"model": "opus"}}',
        encoding="utf-8")
    assert estimate.load_config(cdir) is None


# --- the projection's edges ----------------------------------------------

def test_the_writer_output_alone_can_tip_the_verifier_into_the_long_tier(tmp_path):
    payload = tmp_path / "payload.txt"
    payload.write_text("x" * 360_000, encoding="utf-8")  # 90K tokens
    units = [{"id": "u", "jd": str(payload)}]
    spec_v = estimate._tokens(estimate.split_spec(SPEC)["verifier"])
    at_line = 100_000 - spec_v - 90_000  # verifier prompt of exactly 100K tokens

    def haiku_both(worker_out):
        options = estimate.project(units, SPEC, worker_out=worker_out)["options"]
        return next(o["cost_usd"] for o in options if o["key"] == "haiku-both")

    # One more output token puts about 100K verifier input tokens at $0.50
    # instead of $0.10 per million, about four cents.
    assert haiku_both(at_line + 1) - haiku_both(at_line) >= 0.03


def test_the_default_option_is_always_the_whole_default(tmp_path):
    projection = estimate.project(make_units(1, tmp_path), SPEC)
    default = next(o for o in projection["options"] if o["key"] == "haiku-write-opus-verify")
    assert default["share_of_default"] == 1.0


@pytest.mark.parametrize("flag, value", [
    ("--worker-out", "-1"),
    ("--verifier-out", "-5"),
    ("--ask-above", "-1"),
    ("--attempts", "0"),
    ("--worker-out", "1" + "0" * 400),
    ("--verifier-out", str(10 ** 9 + 1)),
], ids=["worker-negative", "verifier-negative", "ask-below-zero", "attempts-zero",
        "worker-huge", "verifier-over-cap"])
def test_the_cli_rejects_negative_or_huge_sizes(tmp_path, flag, value):
    units_file = tmp_path / "units.json"
    units_file.write_text(json.dumps(make_units(1, tmp_path)), encoding="utf-8")
    spec_file = tmp_path / "spec.md"
    spec_file.write_text(SPEC, encoding="utf-8")
    with pytest.raises(SystemExit) as exc:
        estimate._main(["project", "--units", str(units_file), "--spec", str(spec_file),
                        flag, value])
    assert exc.value.code == 2


def test_the_largest_allowed_output_still_prices_to_a_finite_cost(tmp_path, capsys):
    units_file = tmp_path / "units.json"
    units_file.write_text(json.dumps(make_units(3, tmp_path)), encoding="utf-8")
    spec_file = tmp_path / "spec.md"
    spec_file.write_text(SPEC, encoding="utf-8")
    cap = str(estimate.MAX_OUT_TOKENS)
    assert estimate._main(["project", "--units", str(units_file), "--spec", str(spec_file),
                           "--worker-out", cap, "--verifier-out", cap]) == 0
    # json.loads accepts Infinity, so check the text, not just that it parses.
    out = capsys.readouterr().out
    assert "Infinity" not in out and "NaN" not in out
    assert all(o["cost_usd"] > 0 for o in json.loads(out)["options"])


def test_a_spec_saved_with_a_byte_order_mark_still_splits(tmp_path, capsys):
    units_file = tmp_path / "units.json"
    units_file.write_text(json.dumps(make_units(1, tmp_path)), encoding="utf-8")
    spec_file = tmp_path / "spec.md"
    # Starting at "## Worker", so a kept mark would hide that heading.
    body = SPEC[SPEC.index("## Worker"):]
    spec_file.write_bytes(b"\xef\xbb\xbf" + body.encode("utf-8"))
    assert estimate._main(["project", "--units", str(units_file),
                           "--spec", str(spec_file)]) == 0
    assert json.loads(capsys.readouterr().out)["units"] == 1


# --- the provider caveat -------------------------------------------------

def test_the_provider_caveat_names_every_provider_and_labels_the_guesses():
    skill = (Path(__file__).resolve().parents[1] / "SKILL.md").read_text(encoding="utf-8")
    scale = skill.split("## Scale", 1)[1].split("\n## ", 1)[0]
    for word in ("Bedrock", "Vertex", "Foundry", "mantle", "anthropic_aws",
                 "anthropic_google_cloud", "gateway", "Haiku 4.5",
                 "Opus 4.6", "Opus 4.7", "unverified"):
        assert word in scale, word
