"""Tests for the model guard in workflows/batch.js.

batch.js is a workflow script with a top-level return, so it cannot be
imported. These tests run the whole script under node in strict mode with stub
`agent`, `pipeline` and `log`. estimate.py load-config owns the model and
effort rules; batch.js runs the stages it is handed and, before a single agent
starts, refuses any model but opus or haiku, a verifier on anything but opus,
any effort on haiku and any effort that is not plain lowercase letters. One test feeds the output of
`estimate.py load-config --or-default` straight into a run, which is the
contract the skill relies on. They skip when node is not on PATH.
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


MISSING = object()


def _run(models, max_attempts):
    payload = {"units": [{"id": "a"}, {"id": "b"}], "worker": "w {id}", "verifier": "v {id}",
               "maxAttempts": max_attempts}
    if models is not MISSING:
        payload["models"] = models
    return subprocess.run([NODE, "-e", WHOLE_RUNNER, str(BATCH_JS)],
                          input=json.dumps(payload).encode(), timeout=60,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def run_whole_workflow(models, max_attempts=2):
    out = _run(models, max_attempts)
    assert out.returncode == 0, out.stderr.decode()
    return json.loads(out.stdout.decode())


def refused_run(models):
    """Run batch.js with models it must refuse, and return the error text.

    WHOLE_RUNNER prints only after the run returns, so an empty stdout and a
    nonzero exit mean the script threw. The thrown error is checked to be the
    guard's, which runs before the first agent() call.
    """
    out = _run(models, 2)
    assert out.returncode != 0, out.stdout.decode()
    assert out.stdout.decode() == ""
    return out.stderr.decode()


DEFAULTS = {"worker": {"model": "haiku"}, "verifier": {"model": "opus"}}


@pytest.mark.parametrize("models", [
    MISSING,
    None,
    [],
    "opus",
    {},
    {"worker": {"model": "haiku"}},
    {"worker": "haiku", "verifier": "opus"},
    {"worker": {"model": "sonnet"}, "verifier": {"model": "opus"}},
    {"worker": {"model": "haiku"}, "verifier": {"model": "claude-opus-5-5"}},
    {"worker": {"model": "haiku"}, "verifier": {"model": "Opus"}},
    {"worker": {"model": "haiku"}, "verifier": {"model": " opus"}},
    {"worker": {"model": None}, "verifier": {"model": "opus"}},
    {"worker": {}, "verifier": {"model": "opus"}},
    {"worker": {"model": "haiku"}, "verifier": {"model": "opus", "effort": 3}},
    {"worker": {"model": "haiku"}, "verifier": {"model": "opus", "effort": ["low"]}},
    {"worker": {"model": "haiku"}, "verifier": {"model": "opus", "effort": ""}},
    {"worker": {"model": "haiku"}, "verifier": {"model": "opus", "effort": "  "}},
    {"worker": {"model": "haiku"}, "verifier": {"model": "opus", "effort": "HIGH"}},
    {"worker": {"model": "haiku"}, "verifier": {"model": "opus", "effort": " high"}},
    {"worker": {"model": "haiku", "effort": "high"}, "verifier": {"model": "opus"}},
    {"worker": {"model": "haiku"}, "verifier": {"model": "haiku"}},
    {"worker": {"model": "opus"}, "verifier": {"model": "haiku"}},
], ids=["missing", "null", "array", "string", "empty", "no-verifier", "bare-strings",
        "sonnet", "full-id", "capitalised", "padded", "null-model", "no-model",
        "number-effort", "list-effort", "empty-effort", "blank-effort", "upper-effort",
        "padded-effort", "haiku-effort", "haiku-both", "haiku-verifier"])
def test_models_load_config_would_never_emit_are_refused_before_any_agent_runs(models):
    error = refused_run(models)
    assert "load-config --dir <checkpoint> --or-default" in error


def test_the_refusal_names_the_stage_and_the_value():
    error = refused_run({"worker": {"model": "haiku"}, "verifier": {"model": "sonnet"}})
    assert 'models.verifier.model is "sonnet", not one of opus, haiku' in error
    error = refused_run({"worker": {"model": "haiku", "effort": "low"}, "verifier": DEFAULTS["verifier"]})
    assert 'models.worker.effort is "low", but haiku takes no effort override' in error
    error = refused_run({"worker": {"model": "opus"}, "verifier": {"model": "haiku"}})
    assert 'models.verifier.model is "haiku", but the verifier runs on opus only' in error


def test_the_start_log_names_each_stage_as_it_runs():
    models = {"worker": {"model": "haiku"}, "verifier": {"model": "opus", "effort": "max"}}
    logs = run_whole_workflow(models)["logs"]
    assert "Writer: haiku. Verifier: opus (effort max)." in logs[0]


def test_no_pending_units_needs_no_models():
    payload = {"units": [], "worker": "", "verifier": ""}
    out = subprocess.run([NODE, "-e", WHOLE_RUNNER, str(BATCH_JS)],
                         input=json.dumps(payload).encode(), timeout=60,
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    assert out.returncode == 0, out.stderr.decode()
    assert json.loads(out.stdout.decode())["result"]["note"] == "no pending units"


@pytest.mark.parametrize("models, worker, verifier", [
    ({"worker": {"model": "opus", "effort": "low"}, "verifier": {"model": "opus", "effort": "max"}},
     {"model": "opus", "effort": "low"}, {"model": "opus", "effort": "max"}),
    ({"worker": {"model": "haiku", "effort": None}, "verifier": {"model": "opus"}},
     {"model": "haiku", "effort": None}, {"model": "opus", "effort": None}),
    # Not a level load-config keeps, but which levels are valid is
    # estimate.py's rule, not batch.js's.
    ({"worker": {"model": "opus", "effort": "banana"}, "verifier": {"model": "opus"}},
     {"model": "opus", "effort": "banana"}, {"model": "opus", "effort": None}),
    ({"choice": "custom", "remapped": ["x"], "worker": {"model": "haiku", "extra": 1},
      "verifier": {"model": "opus"}},
     {"model": "haiku", "effort": None}, {"model": "opus", "effort": None}),
], ids=["effort-passed", "null-effort", "unknown-level", "extra-keys"])
def test_allowed_models_pass_through_unchanged(models, worker, verifier):
    calls = run_whole_workflow(models)["calls"]
    assert len(calls) == 2 * 2 * 2, calls
    for call in calls:
        expected = worker if call["opts"]["phase"] == "Write" else verifier
        assert {"model": call["opts"]["model"], "effort": call["opts"]["effort"]} == expected


ESTIMATE = Path(__file__).resolve().parents[1] / "scripts" / "estimate.py"


def _load_config_cli(cdir):
    out = subprocess.run([sys.executable, str(ESTIMATE), "load-config", "--dir", str(cdir),
                          "--or-default"], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                         timeout=60)
    assert out.returncode == 0, out.stderr.decode()
    return json.loads(out.stdout.decode())


@pytest.mark.parametrize("saved, worker, verifier", [
    (None, "haiku", "opus"),
    ({"choice": "opus-low-effort", "worker": {"model": "opus", "effort": "low"},
      "verifier": {"model": "opus", "effort": "low"}}, "opus", "opus"),
    # An old Sonnet checkpoint with an effort on Haiku: estimate.py remaps it,
    # and batch.js runs what comes out.
    ({"choice": "sonnet-both", "worker": {"model": "Haiku 5.5", "effort": "high"},
      "verifier": {"model": "claude-sonnet-5-5"}}, "haiku", "opus"),
    # A record from the removed haiku-both option resumes on an Opus verifier.
    ({"choice": "haiku-both", "worker": {"model": "haiku"},
      "verifier": {"model": "haiku"}}, "haiku", "opus"),
], ids=["nothing-saved", "saved-choice", "old-sonnet", "old-haiku-both"])
def test_load_config_output_runs_as_is(tmp_path, saved, worker, verifier):
    """The contract: whatever load-config --or-default prints, batch.js runs."""
    cdir = tmp_path / "checkpoint"
    if saved is not None:
        cdir.mkdir()
        estimate.state.config_path(cdir).write_text(json.dumps(saved), encoding="utf-8")
    config = _load_config_cli(cdir)
    calls = run_whole_workflow(config)["calls"]
    assert len(calls) == 2 * 2 * 2, calls
    for call in calls:
        stage = "worker" if call["opts"]["phase"] == "Write" else "verifier"
        assert call["opts"]["model"] == {"worker": worker, "verifier": verifier}[stage]
        assert call["opts"]["effort"] == config[stage].get("effort")


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
    (DEFAULTS, "haiku", "opus"),
    ({"worker": {"model": "opus", "effort": "high"}, "verifier": {"model": "opus"}},
     "opus", "opus"),
    ({"worker": {"model": "opus", "effort": "low"}, "verifier": {"model": "opus", "effort": "low"}},
     "opus", "opus"),
], ids=["defaults", "opus-both", "opus-low-effort"])
@pytest.mark.parametrize("max_attempts", [2, estimate.MAX_ATTEMPTS],
                         ids=["default-attempts", "max-attempts"])
def test_every_agent_call_in_a_whole_run_names_an_allowed_model(
        models, worker, verifier, max_attempts):
    """The static test reads the source; this one runs it.

    A call the static test cannot see, such as an escaped identifier or a
    shadowed stageOpts, still has to pass the stage's model here. The run goes
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
    payload = {"units": units, "worker": "w", "verifier": "v", "models": DEFAULTS,
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
