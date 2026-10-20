"""Tests for the model and effort rules in workflows/batch.js.

batch.js is a workflow script with a top-level return, so it cannot be
imported. These tests cut the block from `const ALLOWED_MODELS` to
`if (!units.length)` out of the file, run it under node with stub `models`
and `log`, and read back what stageOpts would pass to agent(). One test runs
the whole script with stub `agent`, `pipeline` and `log` and checks every
call. They skip when node is not on PATH.
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
const models = JSON.parse(fs.readFileSync(0, 'utf8'))
const logs = []
const run = new Function('models', 'log', chunk +
  '\nreturn [stageOpts("worker", "w", "p", {}), stageOpts("worker", "w", "p", {}),' +
  ' stageOpts("verifier", "v", "p", {})]')
const [worker, again, verifier] = run(models, (m) => logs.push(m))
const pick = (o) => ({ model: o.model, effort: o.effort === undefined ? null : o.effort })
console.log(JSON.stringify({ worker: pick(worker), again: pick(again), verifier: pick(verifier), logs }))
"""


def stage_opts(models, timeout=60):
    # models goes in on stdin, so its size is not capped by the command line.
    out = subprocess.run([NODE, "-e", RUNNER, str(BATCH_JS)],
                         input=json.dumps(models).encode(), timeout=timeout,
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
    return json.loads(out.stdout.decode())


# Runs the whole of batch.js in strict mode, as the workflow runtime would,
# with stub agent, pipeline and log. Every verdict but the last attempt's
# rejects, so both agent() calls run once per attempt up to maxAttempts.
WHOLE_RUNNER = r"""
const fs = require('fs')
const src = '"use strict";\n' +
  fs.readFileSync(process.argv[1], 'utf8').replace('export const meta', 'const meta')
const input = JSON.parse(fs.readFileSync(0, 'utf8'))
const AsyncFunction = (async () => {}).constructor
const calls = []
const seen = new Map()
const agent = async (...callArgs) => {
  const opts = callArgs[1]
  calls.push({ count: callArgs.length, opts: opts && typeof opts === 'object'
    ? { model: opts.model === undefined ? null : opts.model,
        effort: opts.effort === undefined ? null : opts.effort,
        phase: opts.phase === undefined ? null : opts.phase }
    : null })
  const phase = opts && opts.phase
  if (phase === 'Verify') {
    const looks = (seen.get(opts.label) || 0) + 1
    seen.set(opts.label, looks)
    const ok = looks >= input.maxAttempts
    return { ok, reason: ok ? 'holds up' : 'look ' + looks }
  }
  return { artifact: 'out.txt', summary: 'done' }
}
const pipeline = async (items, fn) => Promise.all(items.map(fn))
const logs = []
const run = new AsyncFunction('args', 'agent', 'pipeline', 'log', src)
run(input, agent, pipeline, (m) => logs.push(m)).then(
  (result) => console.log(JSON.stringify({ calls, logs, result })),
  (error) => { console.error(String(error && error.stack || error)); process.exit(1) })
"""


def run_whole_workflow(models, max_attempts):
    payload = {"units": [{"id": "a"}, {"id": "b"}], "worker": "w {id}", "verifier": "v {id}",
               "models": models, "maxAttempts": max_attempts}
    out = subprocess.run([NODE, "-e", WHOLE_RUNNER, str(BATCH_JS)],
                         input=json.dumps(payload).encode(), timeout=60,
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    assert out.returncode == 0, out.stderr.decode()
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


@pytest.mark.parametrize("models", [None, {}, {"worker": None}, "x", [], 0, False, "", 5])
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
    "\rhaiku\r", "\x0bhaiku\x0c",
])
def test_model_names_resolve_the_same_in_python_and_javascript(raw):
    expected = estimate.normalize_model(raw)
    expected = expected if expected in estimate.PRICES else "opus"
    assert stage_opts({"verifier": {"model": raw}})["verifier"]["model"] == expected


@pytest.mark.parametrize("stage, raw, fallback", [
    ("worker", "claude-opusx", "haiku"),
    ("worker", "sonnet-opus", "haiku"),
    ("worker", "opus-haiku", "haiku"),
    ("worker", "Opus Sonnet", "haiku"),
    ("verifier", "haiku-opus", "opus"),
    ("verifier", "claude-haikux", "opus"),
])
def test_a_name_without_exactly_one_allowed_family_word_falls_back(stage, raw, fallback):
    other = "verifier" if stage == "worker" else "worker"
    result = stage_opts({stage: {"model": raw}, other: {"model": "opus"}})
    assert result[stage]["model"] == fallback
    assert result["logs"] == ["{}: model {} is not allowed, running {}".format(
        stage, json.dumps(raw), fallback)]


def test_a_null_model_with_an_effort_gives_the_reason_python_gives():
    result = stage_opts({"worker": {"model": "haiku"},
                         "verifier": {"model": None, "effort": "low"}})
    assert result["verifier"] == {"model": "opus", "effort": None}
    assert result["logs"] == ['verifier: effort "low" dropped, no model was named']


def test_a_null_model_with_no_effort_runs_the_default_silently():
    result = stage_opts({"worker": {"model": None}, "verifier": {"model": "opus"}})
    assert result["worker"] == {"model": "haiku", "effort": None}
    assert result["logs"] == []


@pytest.mark.parametrize("raw", [
    "opus" + " " * 1_000_000 + "x", " " * 1_000_000 + "haiku" + "\t" * 1_000_000,
], ids=["inner-run", "outer-runs"])
def test_a_long_run_of_spaces_trims_the_same_in_both_languages_and_fast(raw):
    expected = estimate.normalize_model(raw)
    expected = expected if expected in estimate.PRICES else "opus"
    # A regex trim takes minutes on a million inner spaces; the loop takes
    # milliseconds. The timeout fails the slow one.
    assert stage_opts({"verifier": {"model": raw}}, timeout=20)["verifier"]["model"] == expected


@pytest.mark.parametrize("raw", [
    " high\t", "\ufeffhigh", "\x85high", "high\x1f", "\u00a0high", "HIGH",
    "\rhigh", "high\r", "\x0bhigh\x0c",
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


def _close_of(source, start):
    """The index of the bracket that closes the one at start."""
    depth = 0
    for index in range(start, len(source)):
        if source[index] in "([{":
            depth += 1
        elif source[index] in ")]}":
            depth -= 1
            if depth == 0:
                return index
    raise AssertionError("unclosed bracket at {}".format(start))


def _blank_comments_and_strings(source):
    """Replace comments and string text with spaces, keeping offsets.

    Code inside a template literal's ${...} is kept, since a call there runs.
    Regex literals are not recognised; batch.js has none holding a quote,
    a slash pair or a backtick.
    """
    out = list(source)
    i, n = 0, len(source)
    braces = []  # one entry per open "{": True when it opened a ${...}

    def blank(a, b):
        for k in range(a, b):
            if out[k] != "\n":
                out[k] = " "

    def template_from(i):
        # i is just past a backtick or a closing "}" of ${...}.
        start = i
        while i < n:
            if source[i] == "\\":
                i += 2
            elif source[i] == "`":
                blank(start, i + 1)
                return i + 1
            elif source.startswith("${", i):
                blank(start, i + 2)
                braces.append(True)
                return i + 2
            else:
                i += 1
        raise AssertionError("unclosed template literal")

    while i < n:
        c = source[i]
        if source.startswith("//", i):
            end = source.find("\n", i)
            end = n if end < 0 else end
            blank(i, end)
            i = end
        elif source.startswith("/*", i):
            end = source.find("*/", i + 2)
            assert end >= 0, "unclosed block comment"
            blank(i, end + 2)
            i = end + 2
        elif c in "'\"":
            j = i + 1
            while j < n and source[j] != c:
                j += 2 if source[j] == "\\" else 1
            assert j < n, "unclosed string at {} (a regex literal holding a quote?)".format(i)
            blank(i, j + 1)
            i = j + 1
        elif c == "`":
            out[i] = " "
            i = template_from(i + 1)
        elif c == "{":
            braces.append(False)
            i += 1
        elif c == "}":
            if braces and braces.pop():
                out[i] = " "
                i = template_from(i + 1)
            else:
                i += 1
        else:
            i += 1
    return "".join(out)


def test_the_comment_and_string_blanker_keeps_template_code():
    code = _blank_comments_and_strings(
        "a('agent(') // agent(x)\n/* agent( */ `t ${agent(1, {k: `${b}`})} u` + \"agent(\"")
    assert code.count("agent") == 1 and "agent(1, {k:" in code


def test_every_agent_call_takes_its_options_from_stage_opts():
    """stageOpts is only safe if nothing reaches agent() around it.

    Every use of the name `agent` in code must be one of the two calls, so an
    alias (`const a = agent`), a method call (`this.agent(`) or a call hidden
    in a template literal fails the test. The second argument must be a single
    stageOpts(...) call with nothing after it.
    """
    import re

    code = _blank_comments_and_strings(BATCH_JS.read_text(encoding="utf-8"))
    uses = list(re.finditer(r"(?<![\w$])agent(?![\w$])", code))
    assert len(uses) == 2, [code[m.start():m.start() + 40] for m in uses]
    for use in uses:
        call = re.compile(r"\s*\(").match(code, use.end())
        assert call, code[use.start():use.start() + 40]
        args = _call_args(code, call.end() - 1)
        assert len(args) == 2, args
        opts = re.match(r"stageOpts\s*\(", args[1])
        assert opts, args
        assert _close_of(args[1], opts.end() - 1) == len(args[1]) - 1, args[1]


def test_the_blanker_names_an_unclosed_string_instead_of_crashing():
    with pytest.raises(AssertionError, match="unclosed string"):
        _blank_comments_and_strings("const quoteRe = /'/g")


@pytest.mark.parametrize("models, worker, verifier", [
    (None, "haiku", "opus"),
    ({"worker": {"model": "sonnet", "effort": "high"}, "verifier": "claude-sonnet-5-5"},
     "haiku", "opus"),
    ({"worker": "opus", "verifier": {"model": "Haiku", "effort": "low"}}, "opus", "haiku"),
], ids=["defaults", "sonnet-everywhere", "swapped"])
@pytest.mark.parametrize("max_attempts", [2, estimate.MAX_ATTEMPTS],
                         ids=["default-attempts", "max-attempts"])
def test_every_agent_call_in_a_whole_run_names_an_allowed_model(
        models, worker, verifier, max_attempts):
    """The static test reads the source; this one runs it.

    A call the static test cannot see, such as an escaped identifier or a
    shadowed stageOpts, still has to pass an allowed model here. The run goes
    to the highest attempt count estimate.py accepts, so a stageOpts swapped
    out only on a late retry is caught too.
    """
    result = run_whole_workflow(models, max_attempts)
    calls = result["calls"]
    # Two units, each rejected until its last attempt: one write and one
    # verify per unit per attempt.
    assert len(calls) == 2 * 2 * max_attempts, calls
    for call in calls:
        assert call["count"] == 2 and call["opts"] is not None, call
        assert call["opts"]["model"] in ("opus", "haiku"), call
        if call["opts"]["model"] == "haiku":
            assert call["opts"]["effort"] is None, call
    by_phase = {}
    for call in calls:
        by_phase.setdefault(call["opts"]["phase"], set()).add(call["opts"]["model"])
    assert by_phase == {"Write": {worker}, "Verify": {verifier}}
    assert result["result"]["verified"] == 2


# Runs the whole of batch.js with stub agent, pipeline and log, and keeps
# every prompt agent() receives plus the return value. Every verdict passes.
PROMPT_RUNNER = r"""
const fs = require('fs')
const src = '"use strict";\n' +
  fs.readFileSync(process.argv[1], 'utf8').replace('export const meta', 'const meta')
const input = JSON.parse(fs.readFileSync(0, 'utf8'))
const AsyncFunction = (async () => {}).constructor
const prompts = []
const agent = async (prompt, opts) => {
  prompts.push(prompt)
  return opts.phase === 'Verify' ? { ok: true, reason: 'holds up' }
    : { artifact: 'out.txt', summary: 'done' }
}
const pipeline = async (items, fn) => Promise.all(items.map(fn))
const run = new AsyncFunction('args', 'agent', 'pipeline', 'log', src)
run(input, agent, pipeline, () => {}).then(
  (result) => console.log(JSON.stringify({ prompts, result })),
  (error) => { console.error(String(error && error.stack || error)); process.exit(1) })
"""

SH = shutil.which("sh")

# Each of these broke out of the old double quotes or changed the value.
HOSTILE_IDS = [
    "x$(echo PWNED)y", "price-$5", 'a"b', "it's", "back`echo PWNED`tick",
    "semi;colon && amp", "two  spaces", "back\\slash", "new\nline", "$HOME",
    "*", "!bang", "'", "''", "-",
]
HOSTILE_STATE = "C:\\Program Files\\it's $(echo PWNED)\\state.py"
HOSTILE_DIR = 'check "point" `echo PWNED` $HOME'


def rendered_prompts(units, state_path, checkpoint_dir):
    payload = {"units": units, "worker": "w", "verifier": "v",
               "statePath": state_path, "checkpointDir": checkpoint_dir}
    out = subprocess.run([NODE, "-e", PROMPT_RUNNER, str(BATCH_JS)],
                         input=json.dumps(payload).encode(), timeout=60,
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    assert out.returncode == 0, out.stderr.decode()
    return json.loads(out.stdout.decode())


def shell_words(command):
    """Run a command line through sh with `python` stubbed to print its argv,
    and return the words the real python would have received."""
    script = "python() { printf '%s\\0' \"$@\"; }\n" + command + "\n"
    out = subprocess.run([SH], input=script.encode("utf-8"), timeout=60,
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    assert out.returncode == 0, (command, out.stderr.decode())
    return out.stdout.decode("utf-8").split("\0")[:-1]


# What an agent might put in the placeholders: backticks and a dollar sign,
# which double quotes would run or expand, and no single quote.
AGENT_REASON = "the `price` field reads $5, expected $(total)"
AGENT_PATH = "out/$UNIT `id`.md"


def record_commands(prompt):
    import re
    found = re.findall(r"^    (python .*?)(?=\n\n|\Z)", prompt, re.M | re.S)
    # The agent fills these placeholders in; sh would read < and | as syntax.
    return [c.replace("<verified|failed>", "verified")
             .replace("<one line>", AGENT_REASON)
             .replace("<path you wrote>", AGENT_PATH) for c in found]


@pytest.mark.skipif(SH is None, reason="sh is not on PATH")
def test_record_commands_pass_hostile_values_through_sh_unchanged():
    units = [{"id": unit_id} for unit_id in HOSTILE_IDS]
    run = rendered_prompts(units, HOSTILE_STATE, HOSTILE_DIR)
    assert len(run["prompts"]) == 2 * len(units)
    seen = []
    for prompt in run["prompts"]:
        commands = record_commands(prompt)
        assert len(commands) == 1, prompt
        words = shell_words(commands[0])
        assert words[0] == HOSTILE_STATE, words
        assert words[1] == "record", words
        assert words[words.index("--dir") + 1] == HOSTILE_DIR, words
        seen.append(words[words.index("--id") + 1])
        if "--artifact" in words:
            assert words[words.index("--artifact") + 1] == AGENT_PATH, words
        else:
            assert words[words.index("--reason") + 1] == AGENT_REASON, words
    assert sorted(seen) == sorted(HOSTILE_IDS * 2)


@pytest.mark.skipif(SH is None, reason="sh is not on PATH")
def test_the_authority_command_passes_hostile_paths_through_sh_unchanged():
    run = rendered_prompts([{"id": "a"}], HOSTILE_STATE, HOSTILE_DIR)
    command = run["result"]["authority"].replace("<units.json>", "units.json")
    words = shell_words(command)
    assert words == [HOSTILE_STATE, "report", "--units", "units.json", "--dir", HOSTILE_DIR]
