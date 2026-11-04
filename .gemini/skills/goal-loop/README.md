# Goal loop for Gemini

This project installs the community `goal-loop` skill from
[`sickn33/antigravity-awesome-skills`](https://github.com/sickn33/antigravity-awesome-skills/tree/48de968b4615c294de601f72268aa7be88dde863/skills/goal-loop)
at commit `48de968b4615c294de601f72268aa7be88dde863`. `SKILL.md` is an
unmodified upstream copy, originally adapted from `davidondrej/skills`.
The MIT notices for both sources are preserved in `LICENSE`.

Gemini CLI discovers this directory as a
[workspace skill](https://geminicli.com/docs/cli/skills/).
In a Gemini CLI session opened in this project, reload skills with
`/skills reload` (or start a new session), then ask:

> Use the goal-loop skill to draft a goal contract for this task, with an
> objective, constraints, validation command, and a verifiable stop condition.

The skill provides planning instructions. Installing it does not add a native
`/goal` command, persistent execution, or automatic continuation to Gemini.
Its examples of native commands, authentication requirements, and lifecycle
states belong to other hosts; check the current host's tools and help before
using them. If native goals are unavailable, use the contract as a normal
prompt and save progress explicitly when needed.
