"""Project what a batch will cost, and remember the answer for the resume.

The estimate is rough on purpose and labelled as such. Input size is measured
exactly (spec text plus each unit's payload files); output size is a per-task
assumption the caller can override. Do not present these numbers as measured.

Prices are USD per million tokens, from the Claude model catalogue. A model
downgrade does not reduce the token count, it reduces the price per token, so
the menu is priced in dollars rather than tokens.

CLI:
    python estimate.py project --units units.json --spec spec.md [--attempts 2]
        [--ask-above 100000] [--worker-out 1500] [--verifier-out 400] [--text]
    python estimate.py save-config --dir checkpoint --choice sonnet-both
    python estimate.py load-config --dir checkpoint
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

import state

CONFIG_NAME = "batch-config.json"

# USD per million tokens. Sonnet 5 has introductory pricing through the date
# below, after which it reverts to the standard rate.
SONNET_INTRO_ENDS = date(2026, 8, 31)
PRICES = {
    "opus": {"id": "claude-opus-4-8", "in": 5.00, "out": 25.00, "effort": True},
    "sonnet": {"id": "claude-sonnet-5", "in": 3.00, "out": 15.00, "effort": True,
               "intro": {"in": 2.00, "out": 10.00}},
    # Haiku 4.5 rejects the effort parameter. Never pair it with an effort override.
    "haiku": {"id": "claude-haiku-4-5", "in": 1.00, "out": 5.00, "effort": False},
}

DEFAULT_WORKER_OUT = 1500
DEFAULT_VERIFIER_OUT = 400
DEFAULT_ASK_ABOVE = 100_000
CHARS_PER_TOKEN = 4


def price_for(alias, on=None):
    row = PRICES[alias]
    intro = row.get("intro")
    if intro and (on or date.today()) <= SONNET_INTRO_ENDS:
        return intro["in"], intro["out"]
    return row["in"], row["out"]


def supports_effort(alias):
    return PRICES[alias]["effort"]


def _tokens(text):
    return len(text) // CHARS_PER_TOKEN


def _payload_tokens(unit):
    """Measure a unit's payload: file contents where a value is a real path."""
    total = 0
    for key, value in unit.items():
        if key == "id" or not isinstance(value, str):
            continue
        path = Path(value)
        try:
            if path.is_file():
                total += path.stat().st_size // CHARS_PER_TOKEN
                continue
        except OSError:
            pass
        total += _tokens(value)
    return total


def split_spec(spec_text):
    """Split a spec into its ## Worker and ## Verifier sections."""
    sections, current = {}, None
    for line in spec_text.splitlines():
        stripped = line.strip().lower()
        if stripped in ("## worker", "## verifier"):
            current = stripped.split()[-1]
            sections[current] = []
        elif current:
            sections[current].append(line)
    missing = [name for name in ("worker", "verifier") if name not in sections]
    if missing:
        raise ValueError("spec is missing section(s): {}".format(
            ", ".join("## " + m.title() for m in missing)))
    return {k: "\n".join(v).strip() for k, v in sections.items()}


def project(units, spec_text, attempts=2, worker_out=DEFAULT_WORKER_OUT,
            verifier_out=DEFAULT_VERIFIER_OUT, ask_above=DEFAULT_ASK_ABOVE, on=None):
    sections = split_spec(spec_text)
    spec_in = {k: _tokens(v) for k, v in sections.items()}
    payload = sum(_payload_tokens(u) for u in units)
    n = len(units)

    # The verifier reads the artifact the worker produced, so the worker's
    # output shows up again as verifier input.
    worker_in = n * spec_in["worker"] + payload
    verifier_in = n * spec_in["verifier"] + payload + n * worker_out

    per_unit_out = worker_out + verifier_out
    typical_out = n * per_unit_out
    worst_out = typical_out * attempts

    def cost(worker_model, verifier_model):
        w_in, w_out = price_for(worker_model, on)
        v_in, v_out = price_for(verifier_model, on)
        return (
            worker_in / 1e6 * w_in + (n * worker_out) / 1e6 * w_out
            + verifier_in / 1e6 * v_in + (n * verifier_out) / 1e6 * v_out
        )

    options = [
        {"key": "sonnet-both", "label": "Sonnet writer, Sonnet verifier",
         "worker": {"model": "sonnet"}, "verifier": {"model": "sonnet"},
         "note": "the default, and your standing subagent rule"},
        {"key": "haiku-write-sonnet-verify", "label": "Haiku writer, Sonnet verifier",
         "worker": {"model": "haiku"}, "verifier": {"model": "sonnet"},
         "note": "verification keeps its judgment; a weak writer gets caught"},
        {"key": "haiku-both", "label": "Haiku writer, Haiku verifier",
         "worker": {"model": "haiku"}, "verifier": {"model": "haiku"},
         "note": "cheapest, and the weakest catch on invented facts"},
        {"key": "sonnet-low-effort", "label": "Sonnet both, low reasoning effort",
         "worker": {"model": "sonnet", "effort": "low"},
         "verifier": {"model": "sonnet", "effort": "low"},
         "note": "same price per token, fewer tokens by an amount this has not measured"},
    ]
    baseline = cost("sonnet", "sonnet")
    for option in options:
        option["cost_usd"] = round(cost(option["worker"]["model"],
                                        option["verifier"]["model"]), 2)
        option["share_of_default"] = round(option["cost_usd"] / baseline, 2) if baseline else 1.0
        # An effort override on a model that rejects the parameter is a bug, not
        # a saving. Catch it here rather than at agent-launch time.
        for stage in ("worker", "verifier"):
            cfg = option[stage]
            if cfg.get("effort") and not supports_effort(cfg["model"]):
                raise ValueError("{} does not accept an effort override".format(cfg["model"]))

    return {
        "units": n,
        "agents": n * 2,
        "attempts": attempts,
        "projected_output_typical": typical_out,
        "projected_output_worst": worst_out,
        "projected_input": worker_in + verifier_in,
        "ask": typical_out > ask_above,
        "ask_above": ask_above,
        "options": options,
        "basis": "rough: input measured from spec and payload files, output assumed "
                 "at {} tokens per artifact and {} per verdict".format(worker_out, verifier_out),
    }


def render(projection):
    lines = [
        "Batch: {} units, {} agents, up to {} attempts each.".format(
            projection["units"], projection["agents"], projection["attempts"]),
        "Rough projection: ~{:,} output tokens at Sonnet ({:,} if every unit retries).".format(
            projection["projected_output_typical"], projection["projected_output_worst"]),
        "",
    ]
    # Print the slug next to the number. Both are accepted by --choice, but the
    # slug is what lands in the config file, so showing it keeps the menu and the
    # saved record speaking the same language.
    for i, option in enumerate(projection["options"], 1):
        lines.append("  {}. {:<34} ${:>7.2f}  {}".format(
            i, option["label"], option["cost_usd"], option["note"]))
        lines.append("     --choice {}".format(option["key"]))
    lines.append("")
    lines.append("Estimate is {}.".format(projection["basis"]))
    return "\n".join(lines)


def resolve_choice(choice, projection_options):
    """Accept either the slug or the number the menu printed.

    The --text menu numbers its options 1..N while the config only ever spoke
    slugs, so anyone reading the on-screen menu and passing `--choice 2` got
    `unknown choice: 2`. The menu is the interface people actually see, so it has
    to be a valid answer. Slugs still win, and an out-of-range number reports the
    slugs rather than the numbers, since those are what the file records.
    """
    choice = str(choice).strip()
    for option in projection_options:
        if option["key"] == choice:
            return option
    if choice.isdigit():
        index = int(choice) - 1
        if 0 <= index < len(projection_options):
            return projection_options[index]
    raise ValueError("unknown choice: {} (expected one of: {})".format(
        choice, ", ".join(o["key"] for o in projection_options)))


def save_config(checkpoint_dir, choice, projection_options):
    option = resolve_choice(choice, projection_options)
    choice = option["key"]
    payload = {"choice": choice, "worker": option["worker"], "verifier": option["verifier"]}
    path = Path(checkpoint_dir) / CONFIG_NAME
    state._atomic_write_json(path, payload)
    return payload


def load_config(checkpoint_dir):
    """Return the models a prior run committed to, or None if there is no record."""
    path = Path(checkpoint_dir) / CONFIG_NAME
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or "worker" not in data or "verifier" not in data:
        return None
    return data


def _main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command")
    sub.required = True

    p_proj = sub.add_parser("project", help="project cost and print the menu")
    p_proj.add_argument("--units", required=True)
    p_proj.add_argument("--spec", required=True)
    p_proj.add_argument("--attempts", type=int, default=2)
    p_proj.add_argument("--worker-out", type=int, default=DEFAULT_WORKER_OUT)
    p_proj.add_argument("--verifier-out", type=int, default=DEFAULT_VERIFIER_OUT)
    p_proj.add_argument("--ask-above", type=int, default=DEFAULT_ASK_ABOVE)
    p_proj.add_argument("--text", action="store_true", help="human-readable instead of JSON")

    p_save = sub.add_parser("save-config", help="record the chosen models for resume")
    p_save.add_argument("--dir", required=True)
    p_save.add_argument("--choice", required=True)
    p_save.add_argument("--units", required=True)
    p_save.add_argument("--spec", required=True)

    p_load = sub.add_parser("load-config", help="print the models a prior run chose")
    p_load.add_argument("--dir", required=True)

    args = parser.parse_args(argv)

    if args.command in ("project", "save-config"):
        units = state.load_units(args.units)
        spec_text = Path(args.spec).read_text(encoding="utf-8")

    if args.command == "project":
        projection = project(units, spec_text, args.attempts, args.worker_out,
                             args.verifier_out, args.ask_above)
        print(render(projection) if args.text else json.dumps(projection, indent=2))
        return 0
    if args.command == "save-config":
        projection = project(units, spec_text)
        print(json.dumps(save_config(args.dir, args.choice, projection["options"]), indent=2))
        return 0
    if args.command == "load-config":
        config = load_config(args.dir)
        if config is None:
            print("none")
            return 1
        print(json.dumps(config, indent=2))
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(_main())
