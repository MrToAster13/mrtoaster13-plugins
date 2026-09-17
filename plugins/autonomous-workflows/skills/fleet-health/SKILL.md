---
name: fleet-health
description: Audit every local git repo at once: sync state against the live remote, unpushed commits, stale merged branches, and staged secrets or SSH logs. Produces a per-repo report with ready-to-run, shell-correct ship commands, then executes pushes and branch cleanups after explicit confirmation. Use on "fleet health", "check all my repos", "what's unpushed", "clean up stale branches".
---

# Fleet health

Cross-repo audit. Read-only until the user confirms, then it ships.

## Stage 0: discover

```powershell
Get-ChildItem -Path $HOME -Directory -Recurse -Force -Filter ".git" -Depth 4 -ErrorAction SilentlyContinue |
  Select-Object -ExpandProperty FullName
```

Drop anything under `AppData`, `Temp`, `node_modules`, or `.venv`. Those are
build artifacts and scratch clones, not fleet.

## Stage 1: never trust the working directory

Every git call uses `git -C <abs-path>`. No `cd`, no assumption that the shell
is where the last command left it. Before reading a repo's state, confirm the
path resolves to the repo you think it does:

```powershell
git -C $r rev-parse --show-toplevel
```

If the toplevel does not match `$r`, you are inside a parent repo or a
submodule. Stop and report it rather than acting.

## Stage 2: check against the live remote

`git status` alone lies about the remote: it compares to the last-fetched
ref. Fetch first.

```powershell
git -C $r fetch --prune --quiet
git -C $r status --porcelain=v1 --branch
git -C $r for-each-ref --format='%(refname:short) | up=%(upstream:short) | %(upstream:track)' refs/heads
```

`--prune` is what makes deleted remote branches show up as `[gone]`, which is
the strongest stale-branch signal there is.

Collect per repo:

- **dirty**: `M `/` M`/`??` lines from porcelain
- **unpushed**: `git -C $r log --oneline @{u}..HEAD` on each tracking branch
- **behind**: `[behind N]` in the track field
- **stale merged**: `git -C $r branch --merged origin/main`, minus the current
  branch and minus `main`/`master` itself
- **gone upstream**: track field contains `gone`

## Stage 3: secret and SSH-log scan

Scan the tracked-dirty and untracked files, not the whole tree. What matters is
what is about to be committed.

```powershell
$pat = 'BEGIN [A-Z ]*PRIVATE KEY|ssh-rsa AAAA|AKIA[0-9A-Z]{16}|ghp_[A-Za-z0-9]{20,}|github_pat_|xox[baprs]-|sk-[A-Za-z0-9]{20,}|Authorization:\s*Bearer\s+[A-Za-z0-9._-]{20,}'
Select-String -Path $p -Pattern $pat -AllMatches
```

Separately flag filenames: `auth.log`, `secure`, `*.pem`, `*.key`, `id_rsa`,
`known_hosts`, `.env`. A homelab repo will legitimately contain sanitized log
excerpts, so a filename hit is a question for the user, not an automatic block.
A key-material content hit is a hard block: report it and refuse to stage that
repo until the user resolves it.

## Stage 4: emit shell-correct ship commands

The report gives one copy-pasteable block per repo. Rules:

**Detect the shell first.** `$PSVersionTable` exists in PowerShell and does not
in bash. On this machine the default is PowerShell 7.

**Never mix shells.** A `\` line continuation is a bash-ism; in PowerShell it
becomes a literal backslash inside the argument. PowerShell's continuation is a
backtick, but the fix is to not continue lines at all.

**Keep commit messages on one line.** Single-quote them so `$`, backticks, and
`!` stay literal:

```powershell
git -C <repo> commit -m 'add valuation and fee tests'
```

If a multi-line body is genuinely needed, use a single-quoted here-string with
the closing `'@` at column 0. Never a bash heredoc.

**Bake the absolute path into every command.** `git -C <abs-path>` and nothing
that depends on cwd.

Emit the commands. Do not run them.

## Stage 5: execute after confirmation

Show the plan, wait for an explicit yes. Then:

- Push only branches the user named.
- Delete stale branches with `git -C $r branch -d` (lowercase). Never `-D`.
  `-d` refuses to drop unmerged work, which is the whole safety net.
- Never force-push. Never touch a remote branch without a separate yes.
- Re-run stage 2 afterward and report the new state.

## Output

The deliverable is a self-contained HTML report in the OS temp dir, named
`fleet-health-<YYYY-MM-DD-HHMMSS>.html`, plus a chat summary table with the verdict.
