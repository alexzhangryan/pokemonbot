# Working on This Project

How to actually run day to day development across Claude Code and Cowork, with git in the middle.

Repository: `https://github.com/alexzhangryan/pokemonbot`, public.

## 1. Security first, because the repo is public

Two things will show up in M0 and M7 that must never be committed:

- Showdown account credentials for laddering.
- An API key, if the language model policy provider is built.

`.gitignore` already excludes `.env` and `config/local.*`. Put every secret in `.env` and load it from there, never inline in a source file, not even temporarily. A key pushed to a public repo is compromised the moment it lands, and deleting it later does not help because the history keeps it.

If it ever happens: revoke the key first, rewrite history second. In that order.

## 2. What GitHub changed

Before, the only channel between Claude Code and Cowork was a bridge to your machine, which required it to be online.

Now the Cowork session can read the repository directly from GitHub, so it can review code, check what changed, and revise design documents whether or not your machine is on. Writing still goes the other way through the device bridge, since Cowork cannot run git, so its changes arrive as uncommitted files in your working tree for you to review and commit.

Practical consequence: push at the end of every session. Unpushed work is invisible to the other half of the project.

## 3. The session loop

The same five steps every time. It looks like ceremony until the first time it saves you an afternoon.

```
git status                  # must be clean before you start
                            # read docs/STATUS.md
                            # pick ONE task
                            # work
git diff                    # read it, actually
git add -A && git commit -m "T0.3: dex dump and mainline delta"
                            # update docs/STATUS.md
git commit -am "status: T0.3 done"
git push
```

The acceptance criteria in `docs/09-m0-tasks.md` were written to be commit boundaries. One task, one verified criterion, one commit.

## 4. The habits that matter with an agent

These are the ones specific to this style of development. General git advice applies too, but these are what people get wrong.

Start clean. Never begin an agent session with uncommitted changes you care about. If the tree is dirty you cannot tell your changes from its changes in the diff, and that distinction is the whole point of reviewing.

Read the diff, not the summary. The agent's description of what it did is a claim. The diff is evidence. This is the single most valuable habit and the one most often skipped, precisely because the agent is usually right. Being usually right is what makes unreviewed accumulation dangerous: the errors that slip through are the ones nobody was looking for, and they compound.

Scope one task at a time. Ask for T0.3, verify it, commit it, then ask for T0.4. Do not ask for all of M0. A long unreviewed run produces a large diff you will not read carefully, which defeats the review step.

Reset rather than negotiate. When a session goes sideways, `git reset --hard HEAD` is faster and more reliable than asking the agent to undo its own work. You have a commit from twenty minutes ago. Use it. Arguing with an agent about a mess it made is almost always slower than deleting the mess.

Do not mix refactors with features in one commit. When something breaks later, you want to bisect a history where each commit does one thing.

Verify against the written criterion, not against the agent's confidence. Every M0 task has an acceptance criterion. Run it. "It works" is not the same as "the 50 game self-play run completed with zero timeouts and produced 50 valid traces."

## 5. Escape hatches

Worth memorizing. They are what make it safe to let an agent write code.

| Command | Effect |
| --- | --- |
| `git status` | what has changed |
| `git diff` | unstaged changes, in detail |
| `git restore .` | throw away all uncommitted changes |
| `git reset --hard HEAD` | back to the last commit, discarding everything since |
| `git reset --hard origin/main` | back to the last push |
| `git stash` | set changes aside without losing them, `git stash pop` to restore |
| `git log --oneline` | history, newest first |
| `git revert <sha>` | undo a specific commit safely, after it has been pushed |

`reset --hard` discards work permanently. `revert` is the safe version for anything already pushed.

## 6. Branching, kept light

For solo work on sequential M0 tasks, committing to `main` is fine. Do not impose pull request ceremony on yourself.

Branch when the work is speculative or large enough that abandoning it is plausible. The Rust engine at M8 is the obvious candidate.

```
git switch -c m8/rust-engine
# work, commit freely, it is disposable
git switch main            # abandon it
# or
git merge m8/rust-engine   # keep it
```

The value of a branch here is disposability, not review process.

## 7. Which surface for which work

| Work | Surface | Why |
| --- | --- | --- |
| Writing code, tests, debugging | Claude Code | It has the repo, a terminal, and a tight loop |
| Running and reading benchmarks | Claude Code | The numbers that matter are from your machine |
| Design revision, new documents | Cowork | Design is its job, and it owns `docs/` |
| Web research, papers, metagame data | Cowork | It has search and a disposable sandbox |
| Exploratory analysis you do not want in the repo | Cowork | Its sandbox is throwaway by construction |
| Something contradicts the plan | Cowork, via `STATUS.md` | The design document has to be fixed, not worked around |

## 8. Anti-patterns

Letting the agent run unattended across many tasks and reviewing at the end. The diff is too large to read, so you will not read it.

Skipping `docs/STATUS.md` because you remember where you are. You will not remember in four days, and the other surface never knew.

Working around a design document instead of fixing it. Once the code and the docs disagree, the docs stop being read, and the project loses the thing that made it coherent.

Committing generated data. `data/`, `vendor/`, traces, and model checkpoints are all gitignored. Keep it that way. The repository holds code and documents.

Treating a green test as proof the design is right. It proves the implementation matches what you asked for. Whether what you asked for is correct is a separate question, and it is the one Cowork exists to argue about.
