# Changelog

All notable changes to oModel are documented here.
Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Added
- `omodel --update` — update omodel itself to the latest GitHub release. It tells you what's
  available and asks before installing anything, so it doubles as "is there a newer version?"
  and saying no costs nothing; `--yes` skips the prompt for scripts, and with `--json` or no
  terminal to answer on it reports and stops. If you installed the standalone binary (the
  `install.sh` path), saying yes replaces it in place: it downloads the release tarball, checks
  it against the published sha256, and **runs the new binary and asks it for its version before
  swapping it in**, so a download that arrived truncated — or a linux build that needs a newer
  glibc than your machine has — leaves your working omodel exactly where it was. If you
  installed with pipx, uv or pip instead, it prints the exact command for your install and exits
  3 rather than writing into a tree another tool owns. This is the only time omodel talks to the
  network; launching it still doesn't.

### Changed
- Bundled omo suggestions refreshed to v4.19.2 (from v4.19.1): `claude-opus-4-8` → `claude-opus-5`
  throughout, `claude-fable-5` and `kimi-for-coding-highspeed` join the chains, `gpt-5.4-mini` →
  `gpt-5.4-mini-fast`. Still 11 agents, 8 categories, 15 families.
- Documentation accuracy pass. The README's screenshot is now captured from a real render rather
  than drawn by hand — it was showing a column of model names beside each agent that the app has
  never had, and was missing the presets card entirely. Its providers are also generic now, so the
  picture doesn't advertise whichever ones the author happens to use. Alongside that: pointers to
  `_GPT_ONLY_AGENTS` / `_ULTRAWORK_AGENTS` "in `app.py`" now name `session.py`, where they actually
  moved in 0.3.0; the release matrix no longer claims a `darwin-x64` binary that 0.2.0 stopped
  building; and the model ids in `omodel agent-guide` are refreshed, with the warning that they are
  illustrative moved up to the first example an agent reads.
- `omodel agent-guide`'s JSON examples now show every field the payloads actually carry. They were
  missing `sync_conflict` on all three — the one the guide tells an agent to check before its first
  write — and `settable` on a candidate row, two lines above the paragraph explaining what
  `settable: false` means.

### Fixed
- Candidate rows no longer flag `⚠ variant` on omo's own suggestion and then drop the flag the
  moment you pick something. Variant sets were being discarded once the 24h cache expired, which
  doesn't get you fresher data — it gets you none, leaving a coarse guess in its place. They're
  read at any age now, and the background fetch re-renders the candidate list when it lands
  instead of leaving the rows stale until your next edit. A fetch that landed while the `?` overlay
  was open used to skip that re-render with nothing ever retrying it, so the rows stayed wrong for
  the rest of the session.
- A variant chosen with `v` on a row that isn't the currently-set model no longer reverts to omo's
  suggested variant when a background fetch happens to land. The pick could vanish with no action
  on your part, and a later `Enter` then wrote the wrong variant. The same pick also outlived
  deleting and re-adding a sub-target, and would then be written on the next `Enter`.
- `omodel candidates --json` can no longer advertise a variant that `omodel set --variant` then
  refuses with exit 3. The two read the same providers in the same order now; only the verdict is
  age-gated, not which provider answers it. An agent following the documented loop could hit this
  whenever a gateway's cached data and a dedicated provider's had drifted apart in age.
- The `ctrl+c` hint showed its own `[b]` markup as literal text, and pointed at a key that exits
  without offering to save. It names `q` now, which is the quit that asks.

## [0.3.0] — 2026-07-26

### Added
- **oModel can now be driven by an LLM agent.** New subcommands emit JSON and return meaningful
  exit codes, so an agent can ask what's set, ask what you can run, and change it — through the
  same rules the TUI applies. Previously the only way to change a model without the TUI was to
  hand-edit `oh-my-openagent.jsonc`, which skips the `provider/` prefix, the variant check, the
  backup and the preset invariant.
  ```sh
  omodel agent-guide                          # the whole contract, in one call
  omodel candidates agent:sisyphus --json     # what this agent can run
  omodel set agent:sisyphus opencode/gpt-5.5 --variant medium
  ```
  Also `targets`, `show`, `check`, `clear`, `apply` (many assignments, one save) and
  `preset ls|use|new|rm`. Every one takes `--json` and `--config`; the mutating ones take
  `--dry-run` and `--force`. Exit `3` means "refused — pick something else", `1` means "oModel
  failed". `--force` writes despite an unavailable model or an invalid variant; it never
  overrides the GPT-only lock on `hephaestus`, because omo's own hook would reassign the session
  and the config could not take effect anyway.
  One limitation worth knowing before you parallelise: the mutating verbs are an unlocked
  read-modify-write, so two `set` calls running at once can lose one of the two writes — both
  still report success. Batch them into a single `apply` instead. Documented in
  `omodel agent-guide` §7 rather than fixed.
- `omodel agent-guide` prints the agent contract — target ids, the candidates→set loop, the JSON
  shapes, the exit codes, and what oModel won't do. It ships inside the binary, so an agent that
  finds `omodel` on `PATH` can read it without the repo.
- Named **presets** in a card under the agent list — and they're what you edit. One is always
  active (`●`): your model changes go into it, and `s` writes it to your config. `Enter` switches
  presets (your edits stay in the one you leave), `a` adds one holding the models you're looking at
  (so does `Enter` on the last row, `+ add preset…`), `r` renames one, `x` deletes one — keep **as
  many as you like**; `tab` / `shift+tab` reach the card. `a` never replaces a preset, whatever row
  the cursor is on. First run seeds a `default` preset from the config you
  already have, so **your config always matches one of your presets** — never a state you can't get
  back to.
- Presets live next to your config in `.omodel-presets.json` (a `--config` override keeps its own
  set). Nothing is written until you press `s`, which writes the config and the presets file
  together — so quitting without saving leaves both exactly as they were.
- Deleting a preset closes the gap, and undo keeps up: models restored from a preset you deleted
  say where they landed instead of quietly ending up in a different one.
- If your config changed outside oModel — a hand edit, another tool — the next launch says so and
  asks which way to sync: adopt the config into the preset you were using, or put the preset's
  models back. Nothing is written either way until you press `s`, and `esc` leaves the question
  for later. The JSON surface reports the same thing as `sync_conflict` on every payload.

### Changed
- Internally, the editable state moved into a new headless `session.py` that both the TUI and the
  CLI edit through, so the two can't drift on what a model may be set to or what a save writes.
  No change to how the TUI behaves. Every existing flag (`--print`, `--check`, `--restore`,
  `--refresh-omo`, `--refresh-models`, `--version`) works exactly as before; `omodel --check`
  still always exits 0 for CI, while the new `omodel check` exits 3 when it finds a problem.
- Quitting with unsaved work now offers **save & quit / discard / cancel** instead of a bare
  yes/no, since discarding drops preset changes as well as config changes.
- The `?` overlay now lists `Tab`, which cycles all three panes and is the way to reach the presets
  card. It always worked; it was just never written down.
- The `?` overlay is about half as long. It's grouped by pane, so you can see at a glance that
  `enter`, `a`, `r` and `x` do different things on a preset row, and it no longer repeats what's
  already on screen (`s` / `q` / `?` sit on the hint bar, and every dialog states its own keys) or
  what needs no telling (`esc` cancels, `y`/`n` answer). It fits a 30-row terminal without
  scrolling, and scrolls with `↑↓`/`jk` below that. *(Corrected after release: this line originally
  claimed it "fits an ordinary terminal without scrolling", which was never true — the body gets
  14 rows on an 80×24 and the text is 20 lines.)*
- The hint bar reads `s save · q quit · ? help` — save and quit next to each other, `?` at the end
  as the pointer to everything else.
- Confirmation dialogs now take `←`/`→` (and `h`/`l`) to move between buttons, not just `Tab`, and
  the highlight actually moves with you — previously one button stayed coloured whichever was
  selected, so it was easy to confirm the wrong thing.
- Bundled omo suggestions refreshed to v4.19.1 (from v4.13.0), so the suggested chains now carry
  omo's current picks — `gpt-5.6-sol` / `-terra` / `-luna`, `claude-opus-4-8`, `kimi-k3` and
  `glm-5.2`. Still 11 agents, 8 categories, 15 families.
- Housekeeping with no change in behaviour: the ruff version cap was lifted and its 0.16 default
  rule set adopted (two exclusions, each explained in `pyproject.toml`); the test suite lost its
  vacuous and duplicated cases and two sources of flakiness; and the weekly suggestion-refresh PR
  no longer carries a stale "tests failed" banner after the failure has been fixed.

### Fixed
- Piping a JSON verb into something that stops reading — `omodel show --json | head`, or a pager
  you quit early — no longer exits `120` with `Exception ignored on flushing sys.stdout` on
  stderr. It reads like a crash, and `120` is a fifth exit code on a surface whose contract says
  there are four. The reader stopping is not a failure: you get `0`, and nothing on stderr.
- `x` on a model you added now deletes that row, instead of clearing whichever model happened to
  be set. Adding a model, picking a different one, then pressing `x` on the row you added used to
  unset the model you'd just picked and leave the added row in place — and there was no way to get
  rid of that row at all. `x` reads the row under the cursor now: on a row you added it removes it
  (and unsets the model too, if that's the one that was set — `u` brings both back); on any other
  row it clears the target as before.
- A model id containing square brackets no longer crashes oModel. Typing one in the add-model box
  took the app down on the keystroke, and one saved in your config (or a preset) took it down on
  every launch, before anything was drawn. Ids are now shown exactly as they are, brackets and all.

## [0.2.0] — 2026-07-04

### Added
- Press `?` for a full key-reference overlay — every keybinding, grouped (Navigate / Edit / Undo /
  Models & file / In dialogs) — so the on-screen hint bar can stay minimal.
- Add-model modal: fuzzy `provider/model` picker with an inline variant-selection step and
  Emacs-style list navigation (`Ctrl-P`/`Ctrl-N`), replacing the old free-text input.
- The candidate list now surfaces a target's current off-chain (non-omo) assignment as its own
  pickable, ●-marked row, so a hand-set model — or one that dropped off the chain — is no longer
  invisible.
- `x` on an `ultrawork`/`compaction` sub-target now deletes the whole row (undoable) instead of
  leaving an empty, unsavable placeholder behind; non-sisyphus agents can add their only sub-kind
  (`compaction`) directly, skipping the redundant one-option chooser.

### Changed
- The bottom hint bar is now a fixed, minimal `s save · ? help · q quit` (previously it was
  pane-aware and changed with focus / highlighted row / undo state). Every other key — navigation,
  `enter`/`v`/`x`/`a`, undo/redo, refresh — is documented in the new `?` overlay instead.
- The `Providers:` header now shows just the connected list; the `(cached Nh ago · r to refresh)`
  suffix was dropped (the served list is already bounded-fresh, and `r` is documented under `?`).
- Save is now **edit-in-place / text-preserving**: only the top-level `agents` and `categories`
  objects are rewritten clean; everything else in the file — other keys, formatting, and any
  comments or **commented-out config outside those two** — is preserved byte-for-byte. (Previously
  the whole file was re-emitted, dropping all comments everywhere.) The commented palette *inside*
  agents/categories is still dropped, and the full original is still pinned verbatim to
  `.backup/original.jsonc`. The save header (`// Generated by oModel`) is no longer injected over an
  existing file's own top matter.
- Variant choices (add-model modal and the `v` key) now come from cached `opencode --verbose`
  (`Catalog.variants_for`) instead of the heuristic family registry, so a model with no real
  variants (e.g. `kimi-k2.5`) no longer offers fake ones; the registry is now used only as a
  fallback for the omo-suggestion variant `⚠` warn when opencode reports nothing for that model.
- Same-line substitution is more precise: model ids carrying a trailing date stamp or sub-version
  tag now match their bare omo equivalent instead of falling through to a substitute, and Claude
  substitution is guarded by product line (haiku/sonnet/fable/mythos/…) rather than a fixed set of
  known sizes.
- `detect_family` now checks a family's `includes` even when it also has a `pattern`, matching
  omo's heuristic exactly (no effect on the current bundled data, but keeps future `--refresh-omo`
  runs correct).
- The `ultrawork` sub-target is now offered for `sisyphus` only, matching omo's actual behavior —
  it never honors the ulw swap on any other agent.
- The save-confirmation diff modal is now scrollable (previously clipped at 20 rows).
- Bundled omo suggestions refreshed to v4.13.0 (adds a `qwen` → `alibaba` family mapping; 15
  families total, up from 14).
- `install.sh` now prints a clear pipx-install hint for Intel Macs instead of failing on a missing
  asset; the release matrix no longer builds a `darwin-x64` binary (GitHub is retiring those
  runners).
- Docs housekeeping: README trimmed to user-facing content (design detail moved to DESIGN.md) and
  aligned with the chain-only picker; GLOSSARY.md added; `AGENTS.md` adopted with a `CLAUDE.md`
  shim; stale `master`-branch references retargeted to `main`; the UI pattern renamed
  master-detail → list-detail; `refresh-suggestions.yml` hardened (correct omo checkout
  branch/sparse-checkout, scoped PR permissions, only opens a PR when suggestion data — not just
  metadata — changes).

### Fixed
- `action_variant` (the `v` key) restage now matches on the full `provider/model`, not model
  alone, and survives a background refresh clearing the row cache mid-edit.
- Undoing an add-model now drops its typed candidate row too, not just the assignment.
- The add-model modal's backspace-after-tab-fill now falls back to fuzzy matching instead of
  surfacing a spurious `⚠ unavailable` row.
- Fixed a crash on hand-edited configs with a null `agents` or `categories` value.
- Quitting no longer blocks on an in-flight `opencode` fetch/refresh.
- The add-model modal now works in catalog-error degraded mode.
- `--refresh-omo` no longer silently discards its output when run from the packaged binary — it
  now writes to the XDG data dir.
- Malformed configs now show a friendly error instead of a raw traceback.
- `--restore` no longer crashes on Ctrl-D/Ctrl-C.
- `--config` with a bare relative filename no longer crashes.
- A stale user-local `omo-suggestions.json` no longer shadows newer bundled data.
- The detail pane now describes the assignment's own `(provider, model)` pair — an
  `opencode/…` assignment shows the gateway's context/cost record, not silently the dedicated
  provider's.
- Release pipeline hardening: the smoke test now also runs `--check` (catches PyInstaller
  data-bundling breakage that `--version` alone can't), release tarballs include `LICENSE`/
  `NOTICE`, a tag/version consistency gate runs before publishing, tests run before the build, and
  published checksums are verified by `install.sh`.

## [0.1.0] — 2026-06-20

### Added
- Initial release.
- Textual two-pane TUI: agents + categories (left) / candidate list (right).
- Candidate list merges omo's fallback-chain suggestions with the models you actually have
  (from `opencode models`), filtered to what you can run.
- Dedicated-first prefix resolution: every serving provider is shown as its own row (a gateway
  serves ≥ 2 vendors; a single-vendor dedicated provider sorts first) — pick the row to choose
  the prefix.
- Variant defaulting from the bundled family registry; `v` to override.
- In-session **undo/redo** for mis-press recovery: `u` undoes the last edit
  (set / clear / variant / add-model / add sub-target), `ctrl+r` redoes it — a snapshot stack
  (`history.py`) recorded on every config mutation. Each notifies what changed; the hint bar
  shows `u undo` / `⌃r redo` only when available.
- Vim navigation: `h`/`j`/`k`/`l` alongside the arrow keys.
- Clean JSONC rewrite on save (comments dropped by design); timestamped `.backup/` each save;
  pinned `original.jsonc` (never pruned, never counts toward the 20-snapshot buffer).
- `omodel --restore` — list newest 10 backups + pinned original; restore interactively.
- `omodel --refresh-omo [--omo-src P]` — regenerate `omo-suggestions.json` via bun + an omo
  checkout; non-fatal if omo source or bun is absent.
- `omodel --refresh-models` — force `opencode models --refresh` + rebuild the local cache (the
  in-TUI `r` key does the same).
- `omodel --print` — print current resolved agent/category models, no UI.
- `omodel --check` — dry-run CI-safe resolve for every target (exits 0; degrades if no opencode).
- `omodel --version`.
- `install.sh` — POSIX-sh curl|sh installer (linux-x64, darwin-arm64; Intel macs via pipx).
- GitHub Actions: `ci.yml` (matrix 3.9–3.13), `release.yml` (PyInstaller one-file binaries
  on tag push), `refresh-suggestions.yml` (weekly omo snapshot → PR on change).
- Bundled `omo-suggestions.json` from oh-my-openagent v4.11.1 @ b949c34:
  11 agents, 8 categories, 14 families, 9 known variants.
