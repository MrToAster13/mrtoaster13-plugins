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
    python estimate.py save-config --dir checkpoint --units units.json --spec spec.md
        --choice haiku-write-opus-verify
    python estimate.py load-config --dir checkpoint
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import state

CONFIG_NAME = "batch-config.json"

# USD per million tokens. Subagents run on Opus or Haiku only, so those are the
# only two models the menu prices. Each row prices the model the alias resolves
# to on the Claude API. Other providers serve older models under the same
# aliases; the Scale section of batch-run/SKILL.md lists them.
PRICES = {
    "opus": {"id": "claude-opus-5-5", "in": 4.00, "out": 20.00, "effort": True},
    # Haiku 5.5 charges the higher pair when a request's prompt is over
    # long_above tokens. Off the Claude API (Bedrock, Vertex, Foundry and the
    # other providers Claude Code knows) the haiku alias resolves to Haiku 4.5
    # instead ($1 / $5), which has no effort parameter, so haiku is never paired
    # with an effort override.
    "haiku": {"id": "claude-haiku-5-5", "in": 0.10, "out": 0.50,
              "long_above": 100_000, "in_long": 0.50, "out_long": 2.50,
              "effort": False},
}

# What a stage falls back to when a saved config names a model that is no
# longer allowed, such as a checkpoint written before Sonnet was dropped.
STAGE_DEFAULTS = {"worker": "haiku", "verifier": "opus"}

# Model family words. A saved name maps to an alias only when exactly one of
# these appears in it as a whole word, so "us.anthropic.claude-opus-5-5" is
# opus, "claude-opusx" is nothing, and "sonnet-opus" is nothing.
FAMILIES = ("opus", "haiku", "sonnet", "fable", "mythos")

# The effort levels Claude Code accepts. Anything else is dropped on load.
EFFORT_LEVELS = ("low", "medium", "high", "xhigh", "max")

MENU = [
    {"key": "haiku-write-opus-verify", "label": "Haiku writer, Opus verifier",
     "worker": {"model": "haiku"}, "verifier": {"model": "opus"},
     "note": "the default: verification keeps its judgment; a weak writer gets caught"},
    {"key": "opus-both", "label": "Opus writer, Opus verifier",
     "worker": {"model": "opus"}, "verifier": {"model": "opus"},
     "note": "strongest writer too, at the highest price"},
    {"key": "haiku-both", "label": "Haiku writer, Haiku verifier",
     "worker": {"model": "haiku"}, "verifier": {"model": "haiku"},
     "note": "cheapest, and the weakest catch on invented facts"},
    {"key": "opus-low-effort", "label": "Opus both, low reasoning effort",
     "worker": {"model": "opus", "effort": "low"},
     "verifier": {"model": "opus", "effort": "low"},
     "note": "same price per token, fewer tokens by an amount this has not measured"},
]

DEFAULT_WORKER_OUT = 1500
DEFAULT_VERIFIER_OUT = 400
DEFAULT_ASK_ABOVE = 100_000
CHARS_PER_TOKEN = 4


def price_for(alias, prompt_tokens=0):
    """Input and output price for one request with a prompt of this size."""
    row = PRICES[alias]
    if "long_above" in row and prompt_tokens > row["long_above"]:
        return row["in_long"], row["out_long"]
    return row["in"], row["out"]


def normalize_model(value):
    """Map a saved model name to a menu alias, or return it lowercased.

    The name is split into runs of letters. If exactly one run is a family
    word and that family is one the menu prices, the name means that alias:
    "Opus 5.5", "opus[1m]", "claude-opus-5-5" and
    "us.anthropic.claude-opus-5-5" are all opus. "claude-opusx" has no family
    word and "sonnet-opus" has two, so both come back as is and the caller
    remaps them. Anything that is not a string comes back as None.
    """
    if not isinstance(value, str):
        return None
    name = value.strip().lower()
    families = {word for word in re.findall(r"[a-z]+", name) if word in FAMILIES}
    if len(families) == 1:
        family = families.pop()
        if family in PRICES:
            return family
    return name


def normalize_effort(value):
    """Return a valid effort level, lowercased, or None.

    Only strings count. The Agent tool also takes an integer effort, but the
    menu never writes one, so a number in the file is a hand edit and is
    dropped.
    """
    if not isinstance(value, str):
        return None
    level = value.strip().lower()
    return level if level in EFFORT_LEVELS else None


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
            verifier_out=DEFAULT_VERIFIER_OUT, ask_above=DEFAULT_ASK_ABOVE):
    sections = split_spec(spec_text)
    spec_in = {k: _tokens(v) for k, v in sections.items()}
    unit_payloads = [_payload_tokens(u) for u in units]
    payload = sum(unit_payloads)
    n = len(units)

    # The verifier reads the artifact the worker produced, so the worker's
    # output shows up again as verifier input.
    worker_in = n * spec_in["worker"] + payload
    verifier_in = n * spec_in["verifier"] + payload + n * worker_out

    per_unit_out = worker_out + verifier_out
    typical_out = n * per_unit_out
    worst_out = typical_out * attempts

    def cost(worker_model, verifier_model):
        # Haiku sets its price tier per request, so price each unit's two
        # requests on their own prompt size. An average would let one huge
        # unit hide among small ones.
        total = 0.0
        for unit_payload in unit_payloads:
            w_prompt = spec_in["worker"] + unit_payload
            v_prompt = spec_in["verifier"] + unit_payload + worker_out
            w_in, w_out = price_for(worker_model, w_prompt)
            v_in, v_out = price_for(verifier_model, v_prompt)
            total += (w_prompt * w_in + worker_out * w_out
                      + v_prompt * v_in + verifier_out * v_out) / 1e6
        return total

    options = json.loads(json.dumps(MENU))
    baseline = cost("haiku", "opus")
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
        "Rough projection: ~{:,} output tokens ({:,} if every unit retries).".format(
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


def _shown(value):
    return value if isinstance(value, str) else json.dumps(value)


def _menu_key(worker, verifier):
    for option in MENU:
        if option["worker"] == worker and option["verifier"] == verifier:
            return option["key"]
    return "custom"


def load_config(checkpoint_dir):
    """Return the models a prior run committed to, or None if there is no record.

    load_config checks the record on every load and corrects what is wrong:

    - A stage whose model is not one the menu prices (an old Sonnet
      checkpoint, a hand edit) moves to that stage's default, and any effort
      on that stage goes with the old model.
    - A spelling such as "Opus 5.5" or "us.anthropic.claude-opus-5-5"
      becomes its alias.
    - An effort that is not a valid level, and any effort on Haiku, is
      dropped. A valid level in the wrong case is lowercased.
    - "choice" is set to the menu option the stages match, or "custom".
    - Keys other than model and effort inside a stage are dropped.

    Each reported change is listed under "remapped" so the caller can tell
    the user, and the corrected record is written back, without "remapped",
    so the next resume starts clean. If the write fails, the last entry of
    "remapped" says so and the corrected models are still returned.
    """
    path = Path(checkpoint_dir) / CONFIG_NAME
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or "worker" not in data or "verifier" not in data:
        return None
    data.pop("remapped", None)
    remapped = []
    for stage, default in STAGE_DEFAULTS.items():
        cfg = data[stage]
        raw = cfg.get("model") if isinstance(cfg, dict) else cfg
        effort = cfg.get("effort") if isinstance(cfg, dict) else None
        model = normalize_model(raw)
        if model in PRICES:
            new = {"model": model}
            if model != raw:
                remapped.append("{}: {} -> {}".format(stage, _shown(raw), model))
        else:
            new = {"model": default}
            remapped.append("{}: {} -> {}".format(stage, _shown(raw), default))
        if effort is not None:
            level = normalize_effort(effort)
            if model not in PRICES:
                remapped.append("{}: effort {} dropped with the old model".format(
                    stage, _shown(effort)))
            elif level is None:
                remapped.append("{}: effort {} dropped, not one of {}".format(
                    stage, _shown(effort), ", ".join(EFFORT_LEVELS)))
            elif not supports_effort(model):
                remapped.append("{}: effort {} dropped, {} takes no effort override".format(
                    stage, level, model))
            else:
                new["effort"] = level
                if level != effort:
                    remapped.append("{}: effort {} -> {}".format(stage, effort, level))
        data[stage] = new
    choice = _menu_key(data["worker"], data["verifier"])
    if data.get("choice") != choice:
        remapped.append("choice: {} -> {}".format(_shown(data.get("choice")), choice))
        data["choice"] = choice
    if remapped:
        try:
            state._atomic_write_json(path, dict(data))
        except OSError as exc:
            remapped.append("not written back: {}".format(exc))
        data["remapped"] = remapped
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
