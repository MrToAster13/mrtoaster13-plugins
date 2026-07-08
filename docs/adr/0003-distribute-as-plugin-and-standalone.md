# Distribute as both a plugin and standalone skills

The repo ships the two skills two ways: as a Claude Code plugin (`.claude-plugin/plugin.json` + `marketplace.json`) for managed install and updates, and as plain skill folders under `skills/` that a user copies into `~/.claude/skills/`.

The reason is naming. A plugin namespaces its skills, so a plugin install gives `/clear-not-compact:handoff` and `/clear-not-compact:resume`; bare `/handoff` and `/resume` are only possible from a standalone install. Rather than pick one, we offer both — managed updates for people who want them, short command names for people who want those.

## Consequences

- A user who already has a skill named `handoff` (common — several skill packs ship one) will collide on a standalone install. The fix is to rename one side; the plugin install sidesteps it entirely via the namespace.
