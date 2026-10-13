export const meta = {
  name: 'batch-run',
  description: 'Write and independently verify a batch of work units, checkpointing each one to disk as it lands',
  whenToUse: 'Many similar artifacts to produce, where losing the run to a session limit must not lose the finished work.',
  phases: [
    { title: 'Write', detail: 'one agent per unit, writes its artifact and checkpoints' },
    { title: 'Verify', detail: 'a second, independent agent tries to refute each artifact' },
  ],
}

// Everything domain-specific arrives through args. The skill reads units.json
// and spec.md on the outside and passes them in, because workflow scripts have
// no filesystem access of their own.
// models defaults to a Haiku writer and an Opus verifier rather than inheriting
// the session model. Inheriting would silently run a 30-unit batch at 60 Opus
// agents, and the session model may be one subagents must not run on.
const {
  units = [],
  worker = '',
  verifier = '',
  checkpointDir = 'checkpoint',
  statePath = 'state.py',
  maxAttempts = 2,
  models = { worker: { model: 'haiku' }, verifier: { model: 'opus' } },
} = args || {}

// The rules match load_config in estimate.py. A model name maps to an alias
// when exactly one family word appears in it as a whole word, so
// 'us.anthropic.claude-opus-5-5' and 'Opus 5.5' are opus while 'claude-opusx'
// and 'sonnet-opus' are not allowed. A model that is not allowed falls back to
// the stage default without its effort, so a stray 'sonnet' from an old config
// never reaches agent(). Off the Claude API the haiku alias resolves to
// Haiku 4.5, which has no effort parameter, so an effort on haiku is dropped.
// An effort that is not a known level is dropped too. Each drop is logged once
// per stage.
const ALLOWED_MODELS = ['opus', 'haiku']
const FAMILIES = ['opus', 'haiku', 'sonnet', 'fable', 'mythos']
const EFFORT_LEVELS = ['low', 'medium', 'high', 'xhigh', 'max']
const normalizeModel = (value) => {
  if (typeof value !== 'string') return null
  const name = value.trim().toLowerCase()
  const found = [...new Set((name.match(/[a-z]+/g) || []).filter((w) => FAMILIES.includes(w)))]
  return found.length === 1 && ALLOWED_MODELS.includes(found[0]) ? found[0] : name
}
const normalizeEffort = (value) => {
  if (typeof value !== 'string') return null
  const level = value.trim().toLowerCase()
  return EFFORT_LEVELS.includes(level) ? level : null
}
const warned = new Set()
const warnOnce = (key, message) => {
  if (warned.has(key)) return
  warned.add(key)
  log(message)
}
const stageOpts = (stage, label, phase, schema) => {
  const fallback = stage === 'worker' ? 'haiku' : 'opus'
  const raw = models && models[stage]
  const cfg = typeof raw === 'string' ? { model: raw } : (raw && typeof raw === 'object' ? raw : {})
  const asked = normalizeModel(cfg.model)
  const allowed = ALLOWED_MODELS.includes(asked)
  const model = allowed ? asked : fallback
  if (!allowed && cfg.model !== undefined) {
    warnOnce(`${stage}:model`, `${stage}: model ${JSON.stringify(cfg.model)} is not allowed, running ${fallback}`)
  }
  const opts = { label, phase, schema, model }
  if (cfg.effort !== undefined && cfg.effort !== null) {
    const shown = JSON.stringify(cfg.effort)
    const level = normalizeEffort(cfg.effort)
    let dropped = null
    if (!allowed) dropped = cfg.model === undefined ? 'no model was named' : 'it came with a model that is not allowed'
    else if (level === null) dropped = `it is not one of ${EFFORT_LEVELS.join(', ')}`
    else if (model === 'haiku') dropped = 'haiku takes no effort override'
    if (dropped) warnOnce(`${stage}:effort`, `${stage}: effort ${shown} dropped, ${dropped}`)
    else opts.effort = level
  }
  return opts
}

if (!units.length) {
  log('Nothing to do: every unit is already verified.')
  return { total: 0, verified: 0, failed: [], note: 'no pending units' }
}
if (!worker || !verifier) {
  throw new Error('args.worker and args.verifier are required (the ## Worker and ## Verifier sections of spec.md)')
}

const ARTIFACT = {
  type: 'object',
  required: ['artifact', 'summary'],
  properties: {
    artifact: { type: 'string', description: 'Path to the file you produced.' },
    summary: { type: 'string', description: 'One sentence on what you produced.' },
  },
}

const VERDICT = {
  type: 'object',
  required: ['ok', 'reason'],
  properties: {
    ok: { type: 'boolean', description: 'True only if you could not find a reason to reject it.' },
    reason: { type: 'string', description: 'The specific defect, or why the artifact holds up.' },
  },
}

// Replace {key} with the unit's value for that key. Unmatched braces are left
// alone so prompts can contain literal JSON examples.
const fill = (template, unit) =>
  Object.keys(unit).reduce(
    (text, key) => text.split('{' + key + '}').join(String(unit[key])),
    template,
  )

const checkpoint = (id, status, extra) =>
  `\n\nWhen you are done, record your result by running exactly this from the ` +
  `directory holding the checkpoint dir:\n\n` +
  `    python "${statePath}" record --dir "${checkpointDir}" --id "${id}" --status ${status}${extra}\n\n` +
  `Run it even if you are unsure. An unrecorded unit is retried from scratch on the next run.`

const workerPrompt = (unit, attempt, lastVerdict) =>
  fill(worker, unit) +
  (attempt > 1
    ? `\n\nThis is attempt ${attempt}. The previous attempt was rejected: ${lastVerdict.reason}\nFix that specifically.`
    : '') +
  checkpoint(unit.id, 'written', ' --artifact "<path you wrote>"')

const verifierPrompt = (unit, art) =>
  fill(verifier, unit) +
  `\n\nThe artifact to check is at: ${art.artifact}\nThe writer described it as: ${art.summary}\n\n` +
  `Your job is to refute it, not to bless it. Read the artifact yourself rather than trusting that ` +
  `description. If you are uncertain, return ok=false. A false pass is far more expensive than a false ` +
  `fail, because a false pass is the one nobody looks at again.` +
  `\n\nThen record the outcome: use --status verified with ok=true, or --status failed with a --reason ` +
  `naming the specific defect.\n\n` +
  `    python "${statePath}" record --dir "${checkpointDir}" --id "${unit.id}" --status <verified|failed> --reason "<one line>"`

const runUnit = async (unit) => {
  let last = { reason: 'no attempt completed' }
  for (let attempt = 1; attempt <= maxAttempts; attempt++) {
    const art = await agent(workerPrompt(unit, attempt, last),
      stageOpts('worker', `write:${unit.id}`, 'Write', ARTIFACT))
    if (!art) { last = { reason: 'worker agent died before returning' }; continue }

    const verdict = await agent(verifierPrompt(unit, art),
      stageOpts('verifier', `verify:${unit.id}`, 'Verify', VERDICT))
    if (verdict && verdict.ok) {
      return { id: unit.id, status: 'verified', artifact: art.artifact, attempts: attempt }
    }
    last = verdict || { reason: 'verifier agent died before returning' }
    log(`${unit.id}: attempt ${attempt} rejected (${last.reason})`)
  }
  return { id: unit.id, status: 'failed', reason: last.reason, attempts: maxAttempts }
}

const describe = (stage) => {
  const o = stageOpts(stage, '', '', null)
  return o.effort ? `${o.model} (effort ${o.effort})` : o.model
}
log(`${units.length} pending units, up to ${maxAttempts} attempts each. ` +
    `Writer: ${describe('worker')}. Verifier: ${describe('verifier')}.`)

// One stage, because a unit's verify depends only on its own write. pipeline
// keeps every unit independent: unit A can be on its retry while unit B is
// still on its first write, with no barrier between them.
const results = (await pipeline(units, (unit) => runUnit(unit))).filter(Boolean)

const verified = results.filter((r) => r.status === 'verified')
const failed = results.filter((r) => r.status !== 'verified')
const missing = units.filter((u) => !results.some((r) => r.id === u.id)).map((u) => u.id)

if (missing.length) {
  log(`${missing.length} units produced no result at all (agent died past retry). They stay pending on disk.`)
}

return {
  total: units.length,
  verified: verified.length,
  failed: failed.map((r) => ({ id: r.id, reason: r.reason })),
  missing,
  // The workflow's return value is a convenience. The checkpoint dir on disk is
  // the authority, and `state.py report` is what should be trusted afterward.
  authority: `python ${statePath} report --units <units.json> --dir ${checkpointDir}`,
}
