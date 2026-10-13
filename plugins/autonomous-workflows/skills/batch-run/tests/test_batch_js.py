"""Tests for the model and effort rules in workflows/batch.js.

batch.js is a workflow script with a top-level return, so it cannot be
imported. These tests cut the block from `const ALLOWED_MODELS` to
`if (!units.length)` out of the file, run it under node with stub `models`
and `log`, and read back what stageOpts would pass to agent(). They skip
when node is not on PATH.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import estimate  # noqa: E402

BATCH_JS = Path(__file__).resolve().parents[1] / "workflows" / "batch.js"
NODE = shutil.which("node")

pytestmark = pytest.mark.skipif(NODE is None, reason="node is not on PATH")

RUNNER = r"""
const fs = require('fs')
const src = fs.readFileSync(process.argv[1], 'utf8')
const start = src.indexOf('const ALLOWED_MODELS')
const end = src.indexOf('if (!units.length)')
if (start < 0 || end < start) throw new Error('stageOpts block not found')
const chunk = src.slice(start, end)
const models = JSON.parse(process.argv[2])
const logs = []
const run = new Function('models', 'log', chunk +
  '\nreturn [stageOpts("worker", "w", "p", {}), stageOpts("worker", "w", "p", {}),' +
  ' stageOpts("verifier", "v", "p", {})]')
const [worker, again, verifier] = run(models, (m) => logs.push(m))
const pick = (o) => ({ model: o.model, effort: o.effort === undefined ? null : o.effort })
console.log(JSON.stringify({ worker: pick(worker), again: pick(again), verifier: pick(verifier), logs }))
"""


def stage_opts(models):
    out = subprocess.run([NODE, "-e", RUNNER, str(BATCH_JS), json.dumps(models)],
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
    return json.loads(out.stdout.decode())


@pytest.mark.parametrize("raw, model", [
    ("Opus 5.5", "opus"),
    ("opus[1m]", "opus"),
    ("us.anthropic.claude-opus-5-5", "opus"),
    ("anthropic.claude-haiku-5-5-v1:0", "haiku"),
    ("claude-opusx", "opus"),   # not allowed, so the verifier default
    ("sonnet-opus", "opus"),
    ("claude-sonnet-5-5", "opus"),
])
def test_model_names_match_on_whole_words(raw, model):
    result = stage_opts({"worker": {"model": "haiku"}, "verifier": {"model": raw}})
    assert result["verifier"]["model"] == model


def test_a_near_miss_is_logged_once_and_falls_back():
    result = stage_opts({"worker": {"model": "claude-opusx"}, "verifier": {"model": "opus"}})
    assert result["worker"]["model"] == "haiku"
    assert result["logs"] == ['worker: model "claude-opusx" is not allowed, running haiku']


def test_an_effort_on_haiku_is_dropped_and_logged():
    result = stage_opts({"worker": {"model": "haiku", "effort": "high"}})
    assert result["worker"] == {"model": "haiku", "effort": None}
    assert result["logs"] == ['worker: effort "high" dropped, haiku takes no effort override']


@pytest.mark.parametrize("effort, kept, log", [
    ("low", "low", None),
    ("MAX", "max", None),
    ("turbo", None, 'verifier: effort "turbo" dropped, it is not one of low, medium, high, xhigh, max'),
    (3, None, "verifier: effort 3 dropped, it is not one of low, medium, high, xhigh, max"),
    ("", None, 'verifier: effort "" dropped, it is not one of low, medium, high, xhigh, max'),
])
def test_an_invalid_effort_is_dropped_and_logged(effort, kept, log):
    result = stage_opts({"verifier": {"model": "opus", "effort": effort}})
    assert result["verifier"] == {"model": "opus", "effort": kept}
    assert result["logs"] == ([log] if log else [])


def test_an_effort_with_no_model_is_dropped_and_logged():
    result = stage_opts({"verifier": {"effort": "low"}})
    assert result["verifier"] == {"model": "opus", "effort": None}
    assert result["logs"] == ['verifier: effort "low" dropped, no model was named']


def test_an_effort_on_a_rejected_model_goes_with_it():
    result = stage_opts({"verifier": {"model": "sonnet", "effort": "low"}})
    assert result["verifier"] == {"model": "opus", "effort": None}
    assert result["logs"] == [
        'verifier: model "sonnet" is not allowed, running opus',
        'verifier: effort "low" dropped, it came with a model that is not allowed',
    ]


@pytest.mark.parametrize("models", [None, {}, {"worker": None}, "x", []])
def test_missing_or_junk_models_run_the_defaults_silently(models):
    result = stage_opts(models)
    assert result["worker"] == {"model": "haiku", "effort": None}
    assert result["verifier"] == {"model": "opus", "effort": None}
    assert result["logs"] == []


def test_a_stage_written_as_a_bare_string_is_its_model():
    result = stage_opts({"worker": "opus", "verifier": "Haiku"})
    assert result["worker"] == {"model": "opus", "effort": None}
    assert result["verifier"] == {"model": "haiku", "effort": None}
    assert result["logs"] == []


@pytest.mark.parametrize("value, shown", [(5, "5"), (["opus"], '["opus"]'), (True, "true")])
def test_a_junk_stage_value_is_logged_once_and_falls_back(value, shown):
    result = stage_opts({"worker": value, "verifier": {"model": "opus"}})
    assert result["worker"] == {"model": "haiku", "effort": None}
    assert result["logs"] == ["worker: {} is not a model setting, running haiku".format(shown)]


@pytest.mark.parametrize("raw", [
    "opus", "Opus 5.5", "OPUS\n", "opus-haiku", "haiku haiku", "claude-opusx",
    "\u043epus", "op\u200bus", "HAI\u212aU", "HA\u0130KU", "\ufeffhaiku",
])
def test_model_names_resolve_the_same_in_python_and_javascript(raw):
    expected = estimate.normalize_model(raw)
    expected = expected if expected in estimate.PRICES else "opus"
    assert stage_opts({"verifier": {"model": raw}})["verifier"]["model"] == expected


@pytest.mark.parametrize("raw", [
    " high\t", "\ufeffhigh", "\x85high", "high\x1f", "\u00a0high", "HIGH",
])
def test_effort_levels_resolve_the_same_in_python_and_javascript(raw):
    result = stage_opts({"verifier": {"model": "opus", "effort": raw}})
    assert result["verifier"]["effort"] == estimate.normalize_effort(raw)


def _call_args(source, start):
    """Split the argument list of the call whose "(" is at start."""
    depth, args, current = 0, [], []
    for char in source[start + 1:]:
        if char in "([{":
            depth += 1
        elif char in ")]}":
            if depth == 0:
                args.append("".join(current).strip())
                return args
            depth -= 1
        elif char == "," and depth == 0:
            args.append("".join(current).strip())
            current = []
            continue
        current.append(char)
    raise AssertionError("unclosed call at {}".format(start))


def test_every_agent_call_takes_its_options_from_stage_opts():
    """stageOpts is only safe if nothing reaches agent() around it."""
    import re

    lines = BATCH_JS.read_text(encoding="utf-8").splitlines()
    code = "\n".join(line for line in lines if not line.lstrip().startswith("//"))
    calls = [m.end() - 1 for m in re.finditer(r"(?<![\w.])agent\(", code)]
    assert len(calls) == 2  # the writer and the verifier
    for start in calls:
        args = _call_args(code, start)
        assert len(args) == 2 and args[1].startswith("stageOpts("), args
