# Instructions for AI assistants

Follow https://github.com/romkey/python-ai-rules/blob/main/RULES.md.
Follows python-ai-rules v0.0.0. When re-scanning, apply the entries in
its UPGRADING.md newer than that, then update this line.
With Claude Code, the `python-rules` plugin loads them for you.

Read docs/overview.md first. `main` is protected: work on a branch and
open a pull request; CI and Lint must pass before it can merge.

<!-- Project-specific notes go below. -->

- The Ruida protocol is reverse-engineered. Every protocol detail, memory
  address and unit needs a source (MeerK40t's `meerk40t/ruida/`, the Ruida
  wiki, jnweiger/ruida-laser, ruida_bridge); cite it in a comment. Mark
  values nobody has checked on hardware `verified=False` in `metrics.py`.
- Monitor only: never send the controller anything but memory reads
  (`DA 00`). No motion, laser, file or setting commands.
