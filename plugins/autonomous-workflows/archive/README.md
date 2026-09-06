# Archive

Skills that were part of this plugin and no longer are. Nothing in this folder
loads. Claude Code only reads `../skills/`, so an archived skill stops firing the
moment it lands here, for existing installs and new ones alike.

They are kept as files rather than deleted so the approach stays readable and the
git history stays attached to the path.

## `pre-push`

Archived because it is out of date. A gated `simplify → review → security → test
→ commit` chain that stopped short of the push and handed back.

Two things overtook it. The stage list hardcoded a Python and Node stack
detection that no longer matches how these repos are set up, and the work it
gated is now covered better elsewhere: `/factory` runs an adversary pass on every
diff before merge, and `/remediate` owns the test-verified fix loop. What is left
of `pre-push` is a commit helper, which is not worth a skill.

Do not install it. If you want the stage-gate idea back, write it fresh against
the current stack rather than reviving this.
