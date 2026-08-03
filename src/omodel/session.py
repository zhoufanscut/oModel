"""Headless core — the editable state, with no UI.  DESIGN.md §session.py.

FROZEN CONTRACT — this is what `app.py` (the TUI) and `cli.py` (the agent surface) BOTH edit
through, so the two can't drift into two answers for "what may I set this target to?" or "what
does a save write?".  A `Session` is the four data sources (catalog / suggestions / resolver /
cfg) plus the presets store, and the operations that change them:

    rows(target)          — the pick list (chain + off-chain current), the read side
    set_model / clear     — the cfg mutations, the write side
    save_config / write_store / save   — publication, BOTH files together

**No Textual, no `app` import.**  Two reasons, both load-bearing.  (1) `cli.py` imports lazily so
`--version` / `--check` / the JSON verbs never pay for Textual; importing it here would defeat
that from underneath.  (2) The rules that make omodel worth using — provider prefixing, the
`none`-variant drop, the GPT-only lock, the config-equals-active-preset invariant — used to live
in `OModelApp` methods that queried widgets, so nothing outside a running TUI could apply them.
An agent asked to change a model would hand-edit the JSONC and bypass every one.  This module is
that logic with the widgets taken out.  `app.py` re-imports the four helpers it calls directly
(`SUBKINDS`, `is_gpt_model`, `is_no_variant`, `subkinds_for`) under their old private names and
reaches the rest through the module; the two frozensets it used to own are NOT re-imported, so
they exist here and nowhere else.

**What stays in `app.py`:** the undo `History`, the per-target row cache, `_custom_rows` (typed
off-chain rows), and every render.  Those are session-shaped only inside a UI — a CLI process
edits once and exits, so it has no undo stack and no cursor to remember.  `Session` is
deliberately ignorant of them: `rows()` takes the custom rows as an ARGUMENT rather than owning
them.

**Ownership of `cfg`:** the dict is shared by identity with `app.py` (`OModelApp.cfg` is a
property onto `session.cfg`), so a mutation through either is visible to both.  `store`, by
contrast, is REASSIGNED by a switch and by a write, so it is reached through the property rather
than aliased.
"""
from __future__ import annotations

import copy
import os
from dataclasses import dataclass, field

from . import catalog as catalog_mod
from . import config_io
from . import presets as presets_mod
from . import suggestions as suggestions_mod
from .catalog import Catalog, CatalogUnavailable, normalize_variant
from .resolve import Resolver
from .suggestions import Suggestions

# Sub-targets an agent may carry beyond its top-level `model`.
SUBKINDS = ("ultrawork", "compaction")

# Agents omo locks to a single model family. Hephaestus is GPT-exclusive: omo's
# `no-hephaestus-non-gpt` hook reassigns the session to Sisyphus for any non-GPT model. We
# mirror that — the chain + add-model are both restricted to GPT models for these agents.
GPT_ONLY_AGENTS = frozenset({"hephaestus"})

# Agents for which omo actually honors an `ultrawork` sub-model. The `ultrawork`/`ulw` keyword
# only swaps the model on Sisyphus; on any other agent an `ultrawork` block is dead config (omo
# never reads it). We mirror that — only Sisyphus can add an `ultrawork` sub-target.
# `compaction` is valid on every agent. Hard-coded agent key, like `GPT_ONLY_AGENTS`, not a data field.
ULTRAWORK_AGENTS = frozenset({"sisyphus"})


# ---------------------------------------------------------------------------
# Target-id helpers and guards (moved out of app.py — it re-imports the ones it calls directly,
# and reaches gpt_only / read_map / target_label through the module; see the module docstring)
# ---------------------------------------------------------------------------

def is_gpt_model(model_id: str) -> bool:
    """omo's `isGptModel` (model-core): the model name (after the LAST '/'), lowercased,
    contains 'gpt'. Gates the pick list + add-model for GPT-only agents (Hephaestus)."""
    return "gpt" in model_id.rsplit("/", 1)[-1].lower()


def subkinds_for(name: str) -> tuple:
    """Sub-target kinds addable to agent `name`, in `SUBKINDS` order: `compaction` for every
    agent; `ultrawork` only for the agents omo honors it on (`ULTRAWORK_AGENTS` — Sisyphus)."""
    return tuple(k for k in SUBKINDS if k != "ultrawork" or name in ULTRAWORK_AGENTS)


def is_no_variant(variant) -> bool:
    """True when `variant` means "no level at all → drop the key": None or empty.

    **NOT the literal "none" any more.** That was right while `none` and "no variant" were the
    same thing; omo 4.19.4 made `off` a real bottom rung of the reasoning ladder and kept `none`
    as its alias, so "none" now means *reasoning explicitly off*, which is the opposite of
    dropping the key (that means "use the default"). The spelling is handled one layer down by
    `catalog.normalize_variant`, which turns opencode's `none` into omo's `off` before this
    predicate ever sees it; this one only answers "is there a level at all".

    NB a whitespace-ONLY string (`"   "`) is not "no variant" here; it can only arrive from a
    hand-edited config, and this predicate is deliberately unchanged from the TUI's original so
    the two surfaces agree. `cli.py` strips its `--variant` input before it ever reaches here."""
    return not variant


def read_map(parent, key: str) -> dict:
    """`parent[key]` when it is a dict, else `{}` — a READ-ONLY companion to `coerce_dict`
    (which writes the coercion back).

    `(x.get(k) or {})` is not enough: it rescues `null` and `[]` but a *truthy* non-dict
    (`"agents": "oops"`) sails through and blows up on the next `.get`/`.items`. Both shapes are
    reachable from a hand-edited config, and reaching them via the agent surface used to give a
    raw AttributeError traceback instead of a sentence."""
    if not isinstance(parent, dict):
        return {}
    value = parent.get(key)
    return value if isinstance(value, dict) else {}


def coerce_dict(parent: dict, key: str) -> dict:
    """`parent[key]`, creating `{}` on demand — and REPLACING a present non-dict value (e.g. a
    hand-edited `"agents": null` or `"sisyphus": null`) with a fresh `{}` written back into
    `parent`, rather than handing the caller a non-dict node (or crashing, for a plain
    `dict.setdefault` on a non-dict parent) to write `model`/`variant` into."""
    value = parent.get(key)
    if not isinstance(value, dict):
        value = {}
        parent[key] = value
    return value


def managed_root(cfg) -> dict:
    """The node holding `agents`/`categories` for READS — `config_io.managed_root`, re-exported so
    `app.py` / `cli.py` reach the scope through one place (they already go through this module for
    `read_map` / `target_label`). Never creates anything."""
    return config_io.managed_root(cfg)


# omo resolves a reasoning level from the FIRST of these that is set, checking every source's
# `reasoning` before any source's `variant` (`omo-opencode/src/shared/agent-variant.ts:80-83`,
# `:102-109`). The ordering is why omodel writes only one of them and clears the rest: a stale
# `variant` left beside a `reasoning` is dead config, and — because a CATEGORY's `reasoning`
# outranks an AGENT's `variant` — a `variant` written into a migrated config can be overridden
# by an entirely different object.
REASONING_KEYS = ("reasoning", "variant", "reasoningEffort")


def read_variant(node) -> str | None:
    """The reasoning level set on a cfg node, in omo's own precedence. Returns None when none of
    the spellings carries a non-blank string, so a hand-edited `"reasoning": null` reads as unset
    rather than crashing a later `.strip()`."""
    if not isinstance(node, dict):
        return None
    for key in REASONING_KEYS:
        value = node.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return None


def variant_key_for(cfg, subkind: str | None) -> str:
    """Which spelling to WRITE: `reasoning` for agents/categories on a unified document,
    `variant` for a legacy document — and `variant` for `ultrawork`/`compaction` sub-objects in
    BOTH scopes, because their override reads that key and nothing else
    (`omo-opencode/src/plugin/ultrawork-model-override.ts:81,84,93`; `reasoning` is accepted by
    the schema there but no consumer ever reads it)."""
    if subkind is not None:
        return "variant"
    return "reasoning" if config_io.scope_of(cfg) == "opencode" else "variant"


def gpt_only(target: str) -> bool:
    """True if `target` (incl. its sub-targets) belongs to a GPT-exclusive agent — currently
    Hephaestus (see GPT_ONLY_AGENTS). Such targets hide the add-model escape hatch in the TUI,
    and `omodel set` refuses a non-GPT model on them even with `--force`: omo's hook would
    reassign the session anyway, so writing one is writing config that cannot take effect."""
    if not target.startswith("agent:"):
        return False
    name = target[len("agent:"):].split(".", 1)[0]
    return name in GPT_ONLY_AGENTS


def target_label(target: str) -> str:
    """Short human name for a target id: 'agent:sisyphus' → 'sisyphus',
    'agent:sisyphus.ultrawork' → 'sisyphus.ultrawork', 'cat:deep' → 'deep'."""
    for prefix in ("agent:", "cat:"):
        if target.startswith(prefix):
            return target[len(prefix):]
    return target


def split_target(target: str):
    """`(kind, name, subkind)` for a §Data-contracts target id, or None if it isn't one.
    kind is 'agent' | 'cat'; subkind is the ultrawork/compaction tail or None.
    Validates only the SHAPE — whether the agent/category exists is `Session.known_targets`."""
    if target.startswith("agent:"):
        rest = target[len("agent:"):]
        if "." in rest:
            name, sub = rest.split(".", 1)
            if sub not in SUBKINDS or not name:
                return None
            return ("agent", name, sub)
        return ("agent", rest, None) if rest else None
    if target.startswith("cat:"):
        name = target[len("cat:"):]
        return ("cat", name, None) if name else None
    return None


# ---------------------------------------------------------------------------
# The session
# ---------------------------------------------------------------------------

@dataclass
class Session:
    """The editable state for one config: what omo suggests, what you have, what's set, and the
    presets those assignments belong to.

    Construct with `Session.build(config_path)` in production (it loads all four sources), or
    directly from already-built parts in tests. Either way `__post_init__` does the presets
    load / seed / launch-reconcile and takes the two dirtiness baselines — so every entry point
    upholds the invariant identically."""

    catalog: Catalog
    suggestions: Suggestions
    resolver: Resolver | None
    cfg: dict
    config_path: str
    catalog_error: BaseException | None = None

    # Filled by __post_init__ — never passed in.
    store: presets_mod.Store = field(init=False, default=None)
    sync_conflict: bool = field(init=False, default=False)
    saved_text: str = field(init=False, default="")
    saved_store_fp: str = field(init=False, default="")
    adopted_presets: int | None = field(init=False, default=None)

    def __post_init__(self) -> None:
        # Dirtiness baseline for the config: the serialization last written to (or loaded from)
        # disk. Dirtiness is COMPUTED against this, never a flag, so undoing back to the saved
        # state reads clean and a structural-but-unserialized change (an empty sub-object) is
        # undoable yet never marks the file dirty.
        self.saved_text = config_io.serialize(self.cfg)
        # The presets store for THIS config (presets.py; decision #17). Read best-effort; a
        # missing or mangled file is SEEDED in memory from the config you already have, so there
        # is always at least one preset and the invariant holds from the first frame. NOTHING is
        # written until a save (one write rule) — the seed materializes with the first one.
        self._adopt_legacy_presets()
        self.store = presets_mod.load(self.config_path)
        if self.store.is_empty():
            self.store = presets_mod.seeded(self.managed)
        # Presets captured before omo's reasoning rename carry `variant`; applied to a unified
        # config that spelling resolves behind `reasoning` and the switch would silently no-op.
        # Done BEFORE the dirtiness baseline below, so a rename alone never reads as unsaved.
        self._normalize_store_spelling()
        self._reconcile()
        # Taken AFTER the reconcile, so a re-point alone reads clean.
        self.saved_store_fp = presets_mod.store_fingerprint(self.store)

    def _adopt_legacy_presets(self) -> None:
        """One-time hand-over of the sidecars stranded beside the pre-4.19.3 config — the presets
        store, and the pinned pre-omodel config as `.backup/original-legacy.jsonc`.

        Guarded to the DEFAULT unified path on purpose: sidecars live next to whatever config is
        active, so running `--config /tmp/scratch.jsonc` must not drag the real presets into a
        temp directory and delete the original. Both adopters are no-ops unless the destination
        is empty and the source is a readable file."""
        # Both unified filenames count: `config_path()` resolves `omo.json` too (mirroring omo's
        # own `detectUserOmoJsonPath`), and a user on that spelling needs the hand-over just as
        # much. Anything else — a `--config` override — is left alone.
        unified = os.path.dirname(os.path.abspath(config_io.unified_config_path()))
        here = os.path.abspath(self.config_path)
        if os.path.dirname(here) != unified or os.path.basename(here) not in ("omo.jsonc", "omo.json"):
            return
        legacy = config_io.legacy_config_path()
        try:
            self.adopted_presets = presets_mod.adopt(legacy, self.config_path)
        except Exception:
            self.adopted_presets = None  # best-effort: never block a launch on the hand-over
        try:
            config_io.adopt_original_backup(legacy, self.config_path)
        except OSError:
            pass  # ditto — a missing archive copy is untidy, not a failure worth a traceback

    def _normalize_store_spelling(self) -> None:
        """Rewrite every preset's reasoning level to the spelling THIS config's scope resolves.

        Applies to the whole store, not just an adopted one, so a preset written by an older
        omodel is corrected too. Agent/category nodes take `variant_key_for`; `ultrawork` /
        `compaction` sub-objects always keep `variant`.

        IN MEMORY only, and deliberately: the dirtiness baseline is taken after this runs, so a
        rename alone never shows as unsaved work. The corrected spelling reaches the presets file
        with the next save — until then the in-memory store is what every switch writes, which is
        the part that has to be right."""
        top_key = variant_key_for(self.cfg, None)

        def fix(node, key: str) -> None:
            if not isinstance(node, dict):
                return
            value = read_variant(node)
            for stale in REASONING_KEYS:
                node.pop(stale, None)
            if value is not None:
                node[key] = value

        for preset in self.store.presets:
            for agent in preset.agents.values():
                fix(agent, top_key)
                if isinstance(agent, dict):
                    for sub in SUBKINDS:
                        fix(agent.get(sub), "variant")
            for category in preset.categories.values():
                fix(category, top_key)

    def _reconcile(self) -> None:
        """Launch reconciliation. If the config matches a DIFFERENT preset, just activate it —
        no conflict, nothing to ask, and re-deriving it each launch is idempotent. If it matches
        NONE, set `sync_conflict`: something outside omodel wrote the config, the one case the
        invariant can't cover on its own (the TUI asks which way to sync; the CLI reports it).

        Prefer the RECORDED active when it also matches. `matching_index` returns the FIRST
        match, and a fork creates a byte-identical duplicate by construction — so scanning first
        would silently move you back to preset 1 on every relaunch after the most common flow
        (fork → save → quit), with nothing dirty to correct it."""
        current = self.store.current()
        if current is not None and presets_mod.fingerprint(
            current.agents, current.categories
        ) == presets_mod.fingerprint(self.managed.get("agents"), self.managed.get("categories")):
            match = self.store.active
        else:
            match = presets_mod.matching_index(self.store, self.managed)
        if match is not None:
            self.store.active = match
        self.sync_conflict = match is None and self.store.current() is not None

    @classmethod
    def build(cls, config_path: str | None = None) -> Session:
        """Load all four data sources and construct a Session — the production wiring, shared by
        `app.create_app()` and every CLI verb.

        Degrades gracefully: on CatalogUnavailable the session still builds, with an empty
        catalog and `catalog_error` set (the TUI shows a banner + `r` retry; the CLI reports
        `degraded`). The resolver is built UNCONDITIONALLY — over the real catalog, or over the
        empty degraded-mode one — so add-model (the only route to a model while degraded) stays
        live; only a genuine Resolver.build() failure (e.g. corrupt bundled suggestions data)
        leaves it None. Raises ConfigParseError for a malformed config; callers report it."""
        suggestions = suggestions_mod.load()
        cfg, resolved_path = config_io.load_config(config_path)

        catalog_error: BaseException | None = None
        try:
            catalog = catalog_mod.load()
        except CatalogUnavailable as exc:
            catalog_error = exc
            catalog = Catalog(available={}, connected=[])

        resolver: Resolver | None = None
        try:
            resolver = Resolver.build(catalog, suggestions)
        except Exception:
            resolver = None

        return cls(
            catalog=catalog,
            suggestions=suggestions,
            resolver=resolver,
            cfg=cfg,
            config_path=resolved_path,
            catalog_error=catalog_error,
        )

    # ----- availability ---------------------------------------------------------------

    @property
    def degraded(self) -> bool:
        """True when availability is UNKNOWN — `opencode` is absent or unreadable, so the
        catalog is empty. Distinct from "no candidates": a caller must not conclude a model is
        unusable from an empty pick list taken in this state. Surfaced as `degraded` in every
        JSON payload for exactly that reason."""
        return not self.catalog.connected

    # ----- targets --------------------------------------------------------------------

    def known_targets(self) -> list:
        """Every target id omo defines, in pane order: each agent, then its valid sub-targets
        (`subkinds_for`), then the categories. Sub-targets are included whether or not the config
        currently carries them — they are addressable, which is what a caller enumerating targets
        needs to know."""
        out: list = []
        for name in self.suggestions.agents:
            out.append(f"agent:{name}")
            for kind in subkinds_for(name):
                out.append(f"agent:{name}.{kind}")
        for name in self.suggestions.categories:
            out.append(f"cat:{name}")
        return out

    def is_known(self, target: str) -> bool:
        """Does `target` name an agent/category omo actually defines (and, for a sub-target, one
        valid on that agent)? Shape alone isn't enough — `agent:nope` parses fine."""
        parts = split_target(target)
        if parts is None:
            return False
        kind, name, sub = parts
        if kind == "cat":
            return name in self.suggestions.categories
        if name not in self.suggestions.agents:
            return False
        return sub is None or sub in subkinds_for(name)

    # ----- cfg nodes ------------------------------------------------------------------

    @property
    def managed(self) -> dict:
        """The node of `cfg` holding `agents`/`categories` — `cfg["[opencode]"]` on a unified
        document, `cfg` itself on a legacy one. Read-only view; writes go through
        `ensure_node`."""
        return managed_root(self.cfg)

    @property
    def scope(self) -> str:
        """`"opencode"` or `"root"` — which shape this session's config is in. Surfaced by
        `omodel show --json` as `config_scope` so an agent can tell the two apart."""
        return config_io.scope_of(self.cfg)

    def node_for(self, target: str):
        """The dict node holding {model, reasoning|variant} for `target` in cfg, or None if its
        parent agent/category isn't present. Does NOT create nodes."""
        managed = self.managed
        if target.startswith("agent:"):
            rest = target[len("agent:"):]
            if "." in rest:
                name, kind = rest.split(".", 1)
                agent = read_map(managed, "agents").get(name)
                if not isinstance(agent, dict):
                    return None
                sub = agent.get(kind)
                return sub if isinstance(sub, dict) else None
            return read_map(managed, "agents").get(rest)
        if target.startswith("cat:"):
            name = target[len("cat:"):]
            return read_map(managed, "categories").get(name)
        return None

    def ensure_node(self, target: str) -> dict:
        """The cfg node for `target`, creating it if needed. agents/categories maps and the agent
        object / sub-object are created on demand so edits can land. Every level goes through
        `coerce_dict`, so a hand-edited config's non-dict value anywhere along the path is coerced
        back to `{}` instead of crashing or handing back a non-dict node."""
        root = config_io.managed_root_for_write(self.cfg)
        if target.startswith("agent:"):
            rest = target[len("agent:"):]
            agents = coerce_dict(root, "agents")
            if "." in rest:
                name, kind = rest.split(".", 1)
                agent = coerce_dict(agents, name)
                return coerce_dict(agent, kind)
            return coerce_dict(agents, rest)
        # cat:
        name = target[len("cat:"):]
        cats = coerce_dict(root, "categories")
        return coerce_dict(cats, name)

    def assignment(self, target: str):
        """`(model_str, variant)` currently assigned for `target`; `('', None)` if unset.
        model_str is the full 'provider/model' as stored. The reasoning level is read in omo's
        precedence (`read_variant`), so a config written before the reasoning rename still
        reports what omo will actually resolve."""
        node = self.node_for(target)
        if not isinstance(node, dict):
            return "", None
        return node.get("model", "") or "", read_variant(node)

    # ----- the pick list --------------------------------------------------------------

    def rows(self, target: str, custom_rows=()) -> list:
        """Candidate rows for `target`: `resolver.candidates(target)` (the chain-only pick list)
        when a resolver exists, plus any `custom_rows` the caller is holding (the TUI's typed
        off-chain models), plus the current off-chain assignment — so a model that's set but not
        in the chain is still shown. In degraded mode the chain is empty, leaving just those two.

        The caller owns the caching and the custom rows; this is a pure function of
        (resolver, catalog, cfg, target, custom_rows)."""
        rows: list = []
        if self.resolver is not None:
            rows = list(self.resolver.candidates(target))
        # Re-merge caller-held custom rows the chain doesn't already cover, so a typed model
        # stays a pickable row.
        existing = {f"{r['provider']}/{r['model']}" for r in rows}
        for cr in custom_rows:
            key = f"{cr['provider']}/{cr['model']}"
            if key not in existing:
                rows.append(cr)
                existing.add(key)
        # Surface the target's CURRENT off-chain assignment as its own row when neither the chain
        # nor a custom row already covers it — e.g. a model set in a prior session, a hand-edited
        # config, or one that has since dropped off the chain. Derived straight from cfg, so it
        # always reflects what's set. Appended LAST so it renders right before `+ add model…`.
        # Skips a bare id with no `provider/` (a malformed value) rather than rendering `/model`.
        current, current_variant = self.assignment(target)
        if current and "/" in current and current not in existing:
            provider, model = current.split("/", 1)
            # ⚠ unavailable only when the catalog is readable and no connected provider serves
            # the model; never in degraded mode, where availability is unknown and an unqualified
            # ⚠ would mislead. source 'add' = off-chain pick (CONTRACTS enum).
            warn = []
            if self.catalog.connected and provider not in self.catalog.providers_for(model):
                warn.append("unavailable")
            rows.append({
                "source": "add",
                "model": model,
                "provider": provider,
                "variant": current_variant,
                "entry": None,
                "substitute_for": None,
                "warn": warn,
            })
        return rows

    def variants_for(self, provider: str, model: str, stale_ok: bool = True) -> list:
        """The variants opencode reports for (provider, model) — cached `--verbose` only, never a
        subprocess. `[]` means "no information", NOT "no variants": dedicated providers report
        `{}` and an uncached model reports nothing, so callers must not treat empty as an
        authoritative refusal (decision #14 / `resolve._variant_warn`).

        `stale_ok=False` re-applies the 24h TTL that this read is otherwise exempt from — for
        callers that REFUSE on the answer rather than annotate with it, where an expired file
        must read as "no information" instead of as grounds to reject. See
        `Catalog.variants_for`; the CLI's `_variant_offered` is the only such caller."""
        return self.catalog.variants_for(provider, model, stale_ok=stale_ok)

    # ----- cfg mutations --------------------------------------------------------------

    def set_model(self, target: str, provider: str, model: str, variant=None) -> None:
        """Write `provider/model` (+ variant) into `target`'s cfg node.

        The value written is `f"{provider}/{model}"` — the CONTRACTS-frozen rule. An empty/None
        variant means "no level", so the key is DROPPED rather than written; this covers the
        picker, `v`, a restage, and a cleared target.

        The level itself goes through `normalize_variant` first, so opencode's `none` is written
        as omo's `off` — the same conversion `catalog.variants_for` already applies to the
        offered set, repeated here because this is the write point and a value can also arrive
        from `--variant` or a hand-edited config that never passed through a picker.

        The reasoning level is written under ONE spelling (`variant_key_for`) and the other two
        are removed from the node, so no stale key survives to outrank the one omodel just
        wrote."""
        variant = normalize_variant(variant)
        node = self.ensure_node(target)
        node["model"] = f"{provider}/{model}"
        parsed = split_target(target)
        key = variant_key_for(self.cfg, parsed[2] if parsed else None)
        for stale in REASONING_KEYS:
            if stale != key:
                node.pop(stale, None)
        if is_no_variant(variant):
            node.pop(key, None)
        else:
            node[key] = variant

    def set_row(self, target: str, row: dict) -> None:
        """`set_model` from a candidate-row dict — the shape `rows()` yields."""
        self.set_model(target, row["provider"], row["model"], row.get("variant"))

    def clear(self, target: str) -> bool:
        """Drop `target`'s model and reasoning level, keeping the node. Every spelling goes —
        leaving one behind would keep resolving in omo after omodel reported the target clear.
        Returns whether anything changed."""
        node = self.node_for(target)
        if isinstance(node, dict) and ("model" in node or any(k in node for k in REASONING_KEYS)):
            node.pop("model", None)
            for key in REASONING_KEYS:
                node.pop(key, None)
            return True
        return False

    def delete_subtarget(self, name: str, kind: str) -> None:
        """Remove an ultrawork/compaction sub-target from agent `name` outright, dropping the cfg
        node along with any model it held. For a sub-target clear == delete: a cleared sub-object
        serializes away anyway (config_io drops empty sub-objects), so a model-less placeholder
        isn't worth keeping."""
        agent = read_map(self.managed, "agents").get(name)
        if isinstance(agent, dict):
            agent.pop(kind, None)

    # ----- presets --------------------------------------------------------------------

    def projected_store(self) -> presets_mod.Store:
        """The store as it would be WRITTEN: a copy whose ACTIVE entry carries the live cfg.

        The active preset's content is never stored twice — `cfg` IS it. Everything that needs
        the whole store (dirtiness, saving, switching away) goes through here, so the two can't
        drift, and "your edits go into the preset you're on" is structural rather than something
        each edit path has to remember to do."""
        store = copy.deepcopy(self.store)
        current = store.current()
        if current is not None:
            fresh = presets_mod.capture(current.name, self.managed)
            same = presets_mod.fingerprint(
                current.agents, current.categories
            ) == presets_mod.fingerprint(fresh.agents, fresh.categories)
            if same:
                fresh.saved_at = current.saved_at  # unchanged content keeps its stamp
            store.presets[store.active] = fresh
        return store

    def preset_index(self, ref):
        """Resolve a preset reference — a name, or a 1-based index as int or digit string — to a
        0-based index, or None. Name match is exact first, then case-insensitive; a purely
        numeric ref is read as an index only when no preset is literally NAMED that, so a preset
        called "2" stays addressable."""
        names = [p.name for p in self.store.presets]
        if isinstance(ref, str):
            if ref in names:
                return names.index(ref)
            lowered = [n.lower() for n in names]
            if ref.lower() in lowered:
                return lowered.index(ref.lower())
        text = str(ref).strip()
        if text.lstrip("+-").isdigit():
            idx = int(text) - 1
            if 0 <= idx < len(self.store.presets):
                return idx
        return None

    def switch_preset(self, index: int) -> presets_mod.Preset:
        """Make preset `index` the one being edited, returning it.

        The edits made while on the OLD preset are folded into it first (via `projected_store`),
        so switching back and forth never loses work. Then cfg is REPLACED by this preset's
        assignments — a target it doesn't define is CLEARED, because a preset is a complete
        state, not an overlay. Staged only: nothing reaches disk until a save.

        EXCEPT under `sync_conflict`, where the banking is skipped. Folding assumes the live cfg
        belongs to the active preset; when the config matches NO preset (something outside
        omodel wrote it) that is false, and banking would overwrite the preset you are leaving
        with a state that was never its content — silent, and unrecoverable since the presets
        file has no backup ring. Switching then means "discard the foreign edit and go to this
        preset", which is exactly what a caller reaching for it wants, and it RESOLVES the
        conflict: afterwards cfg equals the active preset again."""
        preset = self.store.presets[index]
        if self.sync_conflict:
            self.store = copy.deepcopy(self.store)  # switch away without banking
            self.sync_conflict = False
        else:
            self.store = self.projected_store()  # bank the in-flight edits into the old preset
        self.store.active = index
        agents, categories = presets_mod.assignments(preset)  # deep-copied OUT: never alias
        root = config_io.managed_root_for_write(self.cfg)
        root["agents"] = agents
        root["categories"] = categories
        return preset

    # ----- dirtiness ------------------------------------------------------------------

    def store_is_dirty(self) -> bool:
        return presets_mod.store_fingerprint(self.projected_store()) != self.saved_store_fp

    def is_dirty(self) -> bool:
        """True iff a save would change anything on disk — the config (`serialize(cfg)` vs the
        text last written/loaded) OR the presets file. Both, because a save writes both and
        quitting discards both. NB: an empty ultrawork/compaction sub-object serializes away, so
        adding one is undoable but does NOT count as dirty — there's nothing to save."""
        if config_io.serialize(self.cfg) != self.saved_text:
            return True
        return self.store_is_dirty()

    def diff(self) -> str:
        """Unified diff of what a save would write vs what's on disk."""
        return config_io.diff_text(self.cfg, self.config_path)

    # ----- publication ----------------------------------------------------------------

    def save_config(self) -> config_io.SaveResult:
        """Write the config (backup + atomic replace) and re-baseline its dirtiness.
        Raises on write failure. Half of a save — `write_store` is the other."""
        result = config_io.save(self.cfg, self.config_path)
        # Re-baseline to what's now on disk (== serialize(cfg) either way).
        self.saved_text = config_io.serialize(self.cfg)
        return result

    def write_store(self, store: presets_mod.Store | None = None) -> presets_mod.Store:
        """Write the presets file and re-baseline its dirtiness. Defaults to `projected_store()`.
        RAISES on failure (presets.write does, by contract) — a silently dropped preset write
        would be a lie about durable state."""
        self.store = presets_mod.write(
            self.config_path, self.projected_store() if store is None else store
        )
        self.saved_store_fp = presets_mod.store_fingerprint(self.store)
        return self.store

    def save(self) -> config_io.SaveResult:
        """Publish BOTH files, config first — decision #17's one write rule.

        The invariant is that the config on disk equals the active preset, so letting one land
        without the other is exactly the orphan state the design exists to prevent. Config goes
        first because it is the artifact with the backup: if the presets write then fails, the
        config is ahead of the store and a second save heals it — whereas the reverse would leave
        a preset naming models the config never got.

        `app.py` deliberately does NOT call this: its save is interactive (diff → confirm) and it
        reports a config-landed-store-didn't failure differently. It calls the two halves."""
        result = self.save_config()
        self.write_store()
        return result
