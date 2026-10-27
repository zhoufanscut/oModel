"""Availability from the `opencode` CLI.  DESIGN.md §catalog.py / §Data sources.

FROZEN CONTRACT — owned by the Core-logic specialist. Implement the bodies; do not
change the public signatures or the `Catalog` shape without the Lead reconciling
CONTRACTS.md (app.py and resolve.py depend on these exact shapes).
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass

from . import cache

# One `provider/model` line — a model line of `opencode models` and a record header of
# `opencode models <provider> --verbose` alike: a provider token (no whitespace, `/` or `:`), the
# FIRST `/`, then a model id with no whitespace (it may hold more `/`s: `openrouter/a/b`). One
# shape for both readers. The header pattern used to be `[a-z0-9_-]+`, so a provider spelled with
# a capital or a dot (`lm.studio`, `MyGateway`) never got a detail line or a variant picker; the
# model-line reader took ANY line with a `/`, so `Warning: config at /home/…` became a provider.
_LINE_RE = re.compile(r"^(?P<prov>[^\s/:]+)/(?P<model>\S+)$")

# Subprocess timeouts (seconds). opencode measures ~3s warm; the headroom guards against a
# hung CLI holding memory forever (each `--verbose` peaks ~320 MB). `--refresh` hits the
# network, so it gets a much longer budget.
_MODELS_TIMEOUT = 20
_VERBOSE_TIMEOUT = 20
_REFRESH_TIMEOUT = 90

# `cache.read(ttl_seconds=…)` for a read that accepts ANY age. Only `variants_for` uses it —
# see the rationale there; availability (`load`) and cost/context (`detail`) keep the 24h TTL.
_STALE_OK = float("inf")

# opencode and omo have drifted apart on ONE reasoning-level name, and omodel sits in the gap: it
# reads variants from opencode but writes a config omo resolves. omo 4.19.4's
# `2026-08-reasoning-unification` renamed the bottom rung `none` → `off`
# (`normalizeReasoning("none") -> {level: "off"}`, the old spelling kept as an alias); opencode
# still reports `none`. Converting HERE — the single seam where opencode's vocabulary enters
# omodel — means every picker, guard and warning downstream speaks omo's, and what omodel writes
# says what omo will read.
#
# `off` is a REAL rung of omo's ladder (off < minimal < low < medium < high < xhigh < max), NOT
# "no variant": omitting the key means "use the default", which on a reasoning model is the
# opposite end. That distinction is why this is a rename and not a drop — see
# `session.is_no_variant`, which is the "is there a level at all" predicate and knows nothing
# about spellings. DESIGN §Reasoning vocabulary.
_VARIANT_ALIASES = {"none": "off"}


def normalize_variant(variant):
    """opencode's spelling of a reasoning level → omo's (`none` → `off`).

    Anything else passes through UNTOUCHED, original casing and all: `None` (no level), a level
    both already agree on, and an unknown value — which omo forwards to the provider verbatim
    (`normalizeReasoning`'s `passthrough`), so omodel must not mangle it either."""
    if variant is None:
        return None
    return _VARIANT_ALIASES.get(str(variant).strip().lower(), variant)


class CatalogUnavailable(Exception):
    """`opencode` IS on PATH but `opencode models` exited != 0 or produced zero
    `provider/model` lines. (opencode MISSING from PATH is NOT this — `load()` returns
    an empty Catalog so the UI degrades to suggestions/add-model only.) The UI shows a
    "couldn't read models" banner + `r` retry."""


class NoNativeLogin(CatalogUnavailable):
    """`omo --list-models` answered, but no listed provider passes `native_logins`. Not a failure
    of `omo`, so no retry fixes it: every surface says "log in" instead of "couldn't read", and
    `--refresh-models` still counts as done (the listing it re-cached is fine)."""


@dataclass
class Catalog:
    available: dict  # {provider: [model_id, ...]} — FIRST-SEEN order
    connected: list  # [provider, ...] — FIRST-SEEN order, NEVER a set

    def providers_for(self, model_id: str) -> list:
        """Connected providers that serve `model_id`, in first-seen order."""
        return [p for p in self.connected if model_id in self.available.get(p, [])]

    def detail(self, model_id: str, use_cache: bool = True, provider: str | None = None):
        """On-demand `opencode models <prov> --verbose` for `provider` — when given AND it
        serves the model — else the RESOLVED provider (providers_for(model_id)[0]). The detail
        pane passes the current assignment's provider so an `opencode/x` assignment shows the
        gateway's record (its cost can differ), never silently the dedicated provider's.
        Brace-count + json.loads each record; pick the one whose header == `<prov>/<model_id>`.
        Returns a DISPLAY-ONLY dict
        {"context": int|None, "cost": {...}, "reasoning": bool, "image": bool} or None.
        Reads NEITHER `--verbose.variants` NOR `.family`: variants are sourced separately by
        variants_for (the decision #14 picker carve-out); `.family` is never read.

        The ~3s `--verbose` call is cached per PROVIDER (one subprocess yields every model of
        that provider) under cache key `verbose-<prov>`; `use_cache=False` forces a live call.
        Still a blocking call on a miss — app.py invokes it from a worker, never the UI thread."""
        providers = self.providers_for(model_id)
        if provider is not None and provider in providers:
            prov = provider
        elif providers:
            prov = providers[0]
        else:
            return None

        stdout = cache.read(f"verbose-{prov}") if use_cache else None
        if stdout is None:
            try:
                result = subprocess.run(
                    ["opencode", "models", prov, "--verbose"],
                    capture_output=True,
                    text=True,
                    timeout=_VERBOSE_TIMEOUT,
                    check=False,   # returncode inspected below; a non-zero exit is a handled state
                )
            except (OSError, UnicodeDecodeError, subprocess.TimeoutExpired):
                return None  # missing, unrunnable (ENOEXEC, EACCES…), or undecodable output
            if result.returncode != 0:
                return None
            stdout = result.stdout
            if use_cache:
                cache.write(
                    f"verbose-{prov}", stdout, ["opencode", "models", prov, "--verbose"]
                )

        return _parse_verbose_record(stdout, f"{prov}/{model_id}")

    def variants_for(self, provider: str, model: str, stale_ok: bool = True) -> list:
        """The variant names opencode exposes for (provider, model), read from the CACHED
        `opencode models <prov> --verbose` output — NEVER a fresh subprocess, so it is safe to
        call on the UI thread. This is the authoritative variant source for the model pickers:
        opencode's per-(provider, model) `variants` map, not the heuristic family registry (the
        deliberate reversal of decision #14 for variants — `.family` is still never read).

        Returns the FIRST NON-EMPTY variant set (lowercased, opencode's order) found across
        `provider` then any other connected provider serving `model`. A provider that reports an
        EMPTY `variants` object is treated as "no info from this endpoint, keep looking" rather
        than an authoritative "no variants" — a dedicated provider very often reports `{}` (zhipuai
        for every model; moonshotai-cn for the whole kimi K2 line), so trusting its emptiness would
        hide real variants that live in the `opencode` gateway's verbose (glm-5.2 → high/max).

        **But "dedicated is empty, the gateway has the real set" is a tendency, not a rule, and
        the search order is what makes it not matter.** It runs the other way too: moonshotai-cn
        reports low/high/max for kimi-k3 while the gateway reports only max, so a dedicated
        endpoint can be the RICHER one. Starting at `provider` is therefore load-bearing in both
        directions — it skips a dedicated `{}` to reach the gateway, AND keeps a dedicated set the
        gateway would have narrowed. Do not "optimise" this into a gateway-first lookup; per-row
        warns depend on it (resolve.TestVariantWarnOpencodeFirst pins the divergence). So:
        `provider`'s own non-empty set wins if it has one; else the next serving provider's;
        else []. Returns [] when NO cached provider reports a non-empty set
        — i.e. opencode genuinely lists none (kimi) OR nothing is cached for the model anywhere (a
        total miss): the caller offers nothing, we never guess.

        `stale_ok=True` (the default) **ignores the 24h TTL** (`_STALE_OK`) — the read half of
        stale-while-revalidate. An expired `verbose-<prov>.json` is still opencode's answer, and
        the alternative is not "fresher data" but no data: resolve then falls back to the coarse
        heuristic `family.variants` (glm → low/medium/high, no `max`) and flags omo's own
        suggestion `⚠ variant`, while `v` offers nothing at all. Variant sets are near-static next
        to availability and cost, so a day-old set beats the heuristic every time. The REVALIDATE
        half is detail()'s still-TTL'd background fetch, whose write re-warms this very file, plus
        `r` / `--refresh-models` (cache.clear), which deletes it outright — the only thing that
        makes a genuinely-removed variant disappear.

        `stale_ok=False` restores the TTL, and exists for exactly one caller: the CLI's HARD
        variant guard (`cli._variant_offered` → `bad_variant` → exit 3). Serving stale data is
        right for a *marker* you can overrule and a picker you can ignore; it is wrong for a
        *refusal*. That surface also never calls detail(), so it has no revalidate half at all —
        an agent would otherwise be told a perfectly valid variant is invalid, on the strength of
        a week-old file, until someone ran `--refresh-models`. Expired → `[]` → "no information"
        → the guard allows, exactly as before this became TTL-exempt.

        **The TTL gates the VERDICT, never the provider search.** Both modes walk the providers
        age-blind and settle on the same one; `stale_ok=False` then returns `[]` if *that*
        provider's entry is expired. Applying the TTL inside the loop instead would let an
        expired provider be skipped and a DIFFERENT provider's set answer — and since the loop
        takes the first NON-EMPTY set, and the gateway plus openai both report real sets whose
        cache ages drift apart (detail() only re-warms the provider you view), the guard and the
        advisory `candidates --json` would return two different non-empty sets for one model.
        `candidates` would then advertise a variant that `set` rejects with exit 3, which is the
        contradiction the whole split exists to prevent."""
        tried = set()
        for prov in [provider, *self.providers_for(model)]:
            if prov in tried:
                continue
            tried.add(prov)
            stdout = cache.read(f"verbose-{prov}", ttl_seconds=_STALE_OK)
            if stdout is None:
                continue
            variants = _parse_verbose_variants(stdout, f"{prov}/{model}")
            if variants:  # non-empty → authoritative for this model; empty/None → keep looking
                # This provider answers, in either mode. A freshness-demanding caller now asks
                # whether ITS entry is current; expired → "no information", never another
                # provider's set.
                if not stale_ok and cache.read(f"verbose-{prov}") is None:
                    return []
                # Converted to omo's vocabulary on the way out (`normalize_variant`), so no
                # caller ever handles both spellings. Deduped because the rename can collide with
                # a level the same provider already reports under omo's name.
                out: list = []
                for v in variants:
                    v = normalize_variant(v)
                    if v not in out:
                        out.append(v)
                return out
        return []


def _parse_models(stdout: str):
    """`opencode models` stdout → (available, connected). Split each line on the FIRST '/';
    both maps preserve FIRST-SEEN order (connected is a list, never a set)."""
    available: dict = {}
    connected: list = []
    for line in stdout.splitlines():
        match = _LINE_RE.match(line.strip())
        if match is None:
            continue  # a banner, a warning naming a path, a URL — anything but `provider/model`
        provider, model_id = match.group("prov"), match.group("model")
        if provider not in available:
            available[provider] = []
            connected.append(provider)
        if model_id not in available[provider]:
            available[provider].append(model_id)
    return available, connected


def _find_verbose_record(stdout: str, target_header: str):
    """The parsed JSON record whose header line == `target_header` in
    `opencode models <prov> --verbose` stdout, or None (not found / unparseable). Records are
    split on header lines at column 0 (`_LINE_RE`) and each JSON block is brace-counted. Shared
    scan for _parse_verbose_record (display fields) and _parse_verbose_variants (variant keys)."""
    lines = stdout.splitlines()
    i = 0
    while i < len(lines):
        if _LINE_RE.match(lines[i]):
            header = lines[i].strip()
            # Collect the JSON block for this record via brace counting.
            i += 1
            brace_depth = 0
            block_lines = []
            while i < len(lines):
                bl = lines[i]
                brace_depth += bl.count("{") - bl.count("}")
                block_lines.append(bl)
                i += 1
                if brace_depth <= 0 and block_lines:
                    break

            if header == target_header:
                try:
                    return json.loads("\n".join(block_lines))
                except (json.JSONDecodeError, ValueError):
                    return None
        else:
            i += 1

    return None


def _parse_verbose_record(stdout: str, target_header: str):
    """The DISPLAY-ONLY fields for the `<prov>/<model>` record, or None. Reads NEITHER
    `.variants` NOR `.family`: variants are read separately by _parse_verbose_variants /
    Catalog.variants_for (the deliberate decision #14 carve-out); `.family` is never read."""
    record = _find_verbose_record(stdout, target_header)
    if record is None:
        return None

    # opencode's output is external data: every field is type-checked here, at the seam, so a
    # string `context` or a list `capabilities` degrades to "unknown" instead of raising — in
    # the app that was a crash (`ctx >= 1000` on a str) or a detail fetch failing on every try.
    def _obj(value) -> dict:
        return value if isinstance(value, dict) else {}

    context = _obj(record.get("limit")).get("context")
    if isinstance(context, bool) or not isinstance(context, (int, float)):
        context = None

    cost = record.get("cost")
    if not isinstance(cost, dict):
        cost = None
    caps = _obj(record.get("capabilities"))
    reasoning = bool(caps.get("reasoning"))
    image = bool(_obj(caps.get("input")).get("image"))

    return {
        "context": context,
        "cost": cost,
        "reasoning": reasoning,
        "image": image,
    }


def _parse_verbose_variants(stdout: str, target_header: str):
    """The variant names opencode exposes for the `<prov>/<model>` record — the KEYS of its
    `variants` object (opencode's order, lowercased) — or [] when that object is empty/missing,
    or None when the record isn't in this stdout (so Catalog.variants_for keeps looking in the
    next provider). Reads ONLY `.variants` (the deliberate decision #14 carve-out for the model
    pickers); still never `.family`."""
    record = _find_verbose_record(stdout, target_header)
    if record is None:
        return None
    variants = record.get("variants")
    if not isinstance(variants, dict):
        return []
    return [str(k).lower() for k in variants]


def load(opencode_bin: str = "opencode", use_cache: bool = True) -> Catalog:
    """`opencode models` → Catalog. Split each line on the FIRST `/`.
    Error rule (DESIGN §Data sources — the single definition):
      * `opencode` not on PATH        → return Catalog(available={}, connected=[])
      * unrunnable (ENOEXEC, EACCES…), exit != 0 OR zero lines parsed → raise CatalogUnavailable

    With `use_cache` (default), a fresh `~/.cache/omodel/models.json` (≤24h) is served
    instead of shelling out — a warm launch is instant. opencode presence is still checked
    first, so 'not on PATH → empty' is unchanged (the cache is a perf layer, not a fallback).
    A live, successful run rewrites the cache. Tests must NOT hard-assert the count (varies)."""
    if shutil.which(opencode_bin) is None:
        return Catalog(available={}, connected=[])

    if use_cache:
        cached = cache.read("models")
        if cached is not None:
            available, connected = _parse_models(cached)
            if available:
                return Catalog(available=available, connected=connected)
            # Empty/garbage cache → ignore it and go live.

    try:
        result = subprocess.run(
            [opencode_bin, "models"],
            capture_output=True,
            text=True,
            timeout=_MODELS_TIMEOUT,
            check=False,   # returncode inspected below; a non-zero exit is a handled state
        )
    except FileNotFoundError:
        return Catalog(available={}, connected=[])
    except subprocess.TimeoutExpired as exc:
        raise CatalogUnavailable(
            f"`{opencode_bin} models` timed out after {_MODELS_TIMEOUT}s"
        ) from exc
    except (OSError, UnicodeDecodeError) as exc:
        # On PATH but unrunnable — a wrong-architecture or truncated binary (ENOEXEC), no
        # permission — or output that isn't text. Unhandled, this escaped Session.build as a
        # traceback: `--check` exited 1 and every `--json` verb printed nothing.
        raise CatalogUnavailable(f"`{opencode_bin} models` could not run: {exc}") from exc

    if result.returncode != 0:
        raise CatalogUnavailable(
            f"`{opencode_bin} models` exited with code {result.returncode}"
        )

    available, connected = _parse_models(result.stdout)

    if not available:
        raise CatalogUnavailable(
            f"`{opencode_bin} models` produced zero provider/model lines"
        )

    if use_cache:
        cache.write("models", result.stdout, [opencode_bin, "models"])

    return Catalog(available=available, connected=connected)


def refresh(opencode_bin: str = "opencode") -> Catalog:
    """Force opencode to re-fetch upstream (`opencode models --refresh`), rebuild the local
    cache from the result, and return the fresh Catalog. The explicit, manual refresh behind
    the CLI `--refresh-models` flag and the app's `r` key — NEVER on the hot path (it does a
    network re-fetch). app.py runs it in a worker so it never blocks the UI thread.

    Same error contract as load(): not on PATH → empty Catalog (+ cache cleared);
    unrunnable / exit != 0 / zero lines → CatalogUnavailable."""
    if shutil.which(opencode_bin) is None:
        cache.clear(keep=(_NATIVE_CACHE_KEY,))
        return Catalog(available={}, connected=[])

    try:
        result = subprocess.run(
            [opencode_bin, "models", "--refresh"],
            capture_output=True,
            text=True,
            timeout=_REFRESH_TIMEOUT,
            check=False,   # returncode inspected below; a non-zero exit is a handled state
        )
    except FileNotFoundError:
        cache.clear(keep=(_NATIVE_CACHE_KEY,))
        return Catalog(available={}, connected=[])
    except subprocess.TimeoutExpired as exc:
        raise CatalogUnavailable(
            f"`{opencode_bin} models --refresh` timed out after {_REFRESH_TIMEOUT}s"
        ) from exc
    except (OSError, UnicodeDecodeError) as exc:  # unrunnable binary — see load()
        raise CatalogUnavailable(
            f"`{opencode_bin} models --refresh` could not run: {exc}"
        ) from exc

    if result.returncode != 0:
        raise CatalogUnavailable(
            f"`{opencode_bin} models --refresh` exited with code {result.returncode}"
        )

    available, connected = _parse_models(result.stdout)
    if not available:
        raise CatalogUnavailable(
            f"`{opencode_bin} models --refresh` produced zero provider/model lines"
        )

    # A refresh invalidates ALL opencode-derived cache (the model list + every verbose-*) — but
    # not native's model list, which is omo's and has its own refresh — then pre-warms the model
    # list from this run's output so the next launch is instant.
    cache.clear(keep=(_NATIVE_CACHE_KEY,))
    cache.write("models", result.stdout, [opencode_bin, "models"])

    return Catalog(available=available, connected=connected)


# ---------------------------------------------------------------------------
# OmO Native (`omo`) — DESIGN §Harnesses
# ---------------------------------------------------------------------------
# No public `omo` command prints the models you can use, so the native catalog is the engine's
# whole catalog (`omo --list-models`, cached like `opencode models`) filtered to the providers you
# are logged into, decided the way the engine decides offline: a credential is PRESENT (never
# checked for validity). `native_logins` is that rule; it reads files and the environment only,
# so it runs on every load rather than being cached — a `/login` in omo shows up at once.

_NATIVE_MODELS_TIMEOUT = 30
_NATIVE_CACHE_KEY = "native-models"

# Where omo keeps engine state (omo-native `bin/lib/agent-dir.js` `AGENT_DIR_ENV_NAMES`).
_AGENT_DIR_ENV = ("OMO_CODING_AGENT_DIR", "SENPI_CODING_AGENT_DIR", "PI_CODING_AGENT_DIR")

# omo's reasoning ladder, offered for a model the engine lists as `thinking: yes`. The engine
# clamps a level a model lacks to the nearest one it has (pi-ai `clampThinkingLevel`), so the
# whole ladder is safe to offer; `--list-models` does not print the per-model level map.
NATIVE_LADDER = ("off", "minimal", "low", "medium", "high", "xhigh", "max")

# Bedrock's ambient-credential rule (pi-ai `getEnvApiKey`): any ONE of these sets it up, except
# the access key, which needs its secret too.
_BEDROCK_ENV = (
    "AWS_PROFILE", "AWS_BEARER_TOKEN_BEDROCK", "AWS_CONTAINER_CREDENTIALS_RELATIVE_URI",
    "AWS_CONTAINER_CREDENTIALS_FULL_URI", "AWS_WEB_IDENTITY_TOKEN_FILE",
)
_CLAUDE_OAUTH_RE = re.compile(r"^CLAUDE_CODE_OAUTH_TOKEN(_\d+)?$")


def native_agent_dir(env=None) -> str:
    """omo's engine-state directory: the first set `$OMO_CODING_AGENT_DIR` /
    `$SENPI_CODING_AGENT_DIR` / `$PI_CODING_AGENT_DIR`, else `<home>/.omo/agent` (home =
    `$HOME`, else `$USERPROFILE`, as omo resolves it)."""
    env = os.environ if env is None else env
    for name in _AGENT_DIR_ENV:
        value = (env.get(name) or "").strip()
        if value:
            return os.path.abspath(value)
    home = env.get("HOME") or env.get("USERPROFILE") or os.path.expanduser("~")
    return os.path.join(home, ".omo", "agent")


def _read_json_object(path: str) -> dict:
    """The JSON(C) object at `path`, or {} when it is missing, unreadable or not an object."""
    import json5

    try:
        with open(path, encoding="utf-8") as f:
            data = json5.load(f)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _auth_env_table() -> dict:
    """{provider: [env var, ...]} — the bundled `data/native-auth.json` (pi-ai's map)."""
    from importlib.resources import files

    try:
        raw = json.loads((files("omodel.data") / "native-auth.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    table = raw.get("envKeys") if isinstance(raw, dict) else None
    return table if isinstance(table, dict) else {}


def native_logins(agent_dir: str | None = None, env=None) -> set:
    """The providers omo's engine counts as logged in, from files and the environment only.

    1. a top-level key of `<agent dir>/auth.json` (keys only — the values are credentials and are
       never read into anything);
    2. a set env var the engine maps to the provider (`data/native-auth.json`);
    3. a custom provider in `<agent dir>/models.json` that carries an `apiKey`;
    4. `anthropic-subscription` from `CLAUDE_CODE_OAUTH_TOKEN[_<n>]`; `amazon-bedrock` and
       `google-vertex` from their ambient-credential rules.

    Not detected: the anthropic-subscription lane's opt-in ambient Claude login (the engine asks
    `claude auth status`). See DESIGN §Harnesses for what was measured against the engine."""
    env = os.environ if env is None else env
    agent_dir = native_agent_dir(env) if agent_dir is None else agent_dir
    out = set(_read_json_object(os.path.join(agent_dir, "auth.json")))

    def is_set(name: str) -> bool:
        return bool(env.get(name))

    for provider, names in _auth_env_table().items():
        if isinstance(names, list) and any(is_set(n) for n in names if isinstance(n, str)):
            out.add(provider)

    providers = _read_json_object(os.path.join(agent_dir, "models.json")).get("providers")
    if isinstance(providers, dict):
        for provider, entry in providers.items():
            if isinstance(entry, dict) and entry.get("apiKey"):
                out.add(provider)

    if any(_CLAUDE_OAUTH_RE.match(name) and env.get(name) for name in env):
        out.add("anthropic-subscription")
    if any(is_set(n) for n in _BEDROCK_ENV) or (
        is_set("AWS_ACCESS_KEY_ID") and is_set("AWS_SECRET_ACCESS_KEY")
    ):
        out.add("amazon-bedrock")
    adc = env.get("GOOGLE_APPLICATION_CREDENTIALS") or os.path.join(
        env.get("HOME") or os.path.expanduser("~"), ".config", "gcloud",
        "application_default_credentials.json",
    )
    if (
        os.path.exists(adc)
        and (is_set("GOOGLE_CLOUD_PROJECT") or is_set("GCLOUD_PROJECT"))
        and is_set("GOOGLE_CLOUD_LOCATION")
    ):
        out.add("google-vertex")
    return out


def _parse_list_models(stdout: str) -> list:
    """`omo --list-models` stdout → [(provider, model, info)] in its order, where info is
    {"context": str, "max_out": str, "reasoning": bool, "image": bool}. The table is a header row
    (`provider  model  context  max-out  thinking  images`) then one whitespace-separated row per
    model; anything else (a "no models" message, a warning) is skipped."""
    rows: list = []
    started = False
    for line in stdout.splitlines():
        parts = line.split()
        if not started:
            started = parts[:2] == ["provider", "model"]
            continue
        if len(parts) != 6 or parts[4] not in ("yes", "no") or parts[5] not in ("yes", "no"):
            continue
        provider, model, context, max_out, thinking, images = parts
        rows.append((provider, model, {
            "context": context, "max_out": max_out,
            "reasoning": thinking == "yes", "image": images == "yes",
        }))
    return rows


@dataclass
class NativeCatalog(Catalog):
    """The `Catalog` for OmO Native: same `available`/`connected` shape, filled from
    `omo --list-models` × `native_logins`. `info` holds the table's per-model columns, which is
    all `detail` and `variants_for` read — there is no per-provider `--verbose` to fetch."""

    info: dict = None  # {(provider, model): {"context", "max_out", "reasoning", "image"}}

    def detail(self, model_id: str, use_cache: bool = True, provider: str | None = None):
        """The listed columns for `provider`/`model_id` (or the first provider serving it), as the
        display-only dict the detail pane reads. No cost — the engine's table has none. Never a
        subprocess."""
        providers = self.providers_for(model_id)
        prov = provider if provider in providers else (providers[0] if providers else None)
        row = (self.info or {}).get((prov, model_id))
        if row is None:
            return None
        context = _token_count(row["context"])
        return {"context": context, "cost": None, "reasoning": row["reasoning"], "image": row["image"]}

    def variants_for(self, provider: str, model: str, stale_ok: bool = True) -> list:
        """omo's ladder for a model the engine lists as thinking, else [] ("nothing to offer").
        Looks at `provider`'s row, then any other serving provider's — the same search order as
        the opencode reader. `stale_ok` is accepted for signature parity; the table is read with
        the catalog, so there is no separate age to check."""
        info = self.info or {}
        for prov in [provider, *self.providers_for(model)]:
            row = info.get((prov, model))
            if row is not None:
                return list(NATIVE_LADDER) if row["reasoning"] else []
        return []


def _token_count(text: str):
    """`200K` / `1.5M` / `8192` → int, or None for anything else (the table's formatting)."""
    m = re.fullmatch(r"(\d+(?:\.\d+)?)([KM]?)", text or "")
    if m is None:
        return None
    scale = {"": 1, "K": 1_000, "M": 1_000_000}[m.group(2)]
    return int(float(m.group(1)) * scale)


def _native_catalog(stdout: str, logins: set) -> NativeCatalog:
    """The listing filtered to `logins`. No row left is CatalogUnavailable rather than an empty
    catalog: an empty one reads as "`omo` is not on PATH" everywhere downstream, and here `omo`
    answered — omodel just cannot see a login (none yet, or one it does not detect)."""
    available: dict = {}
    connected: list = []
    info: dict = {}
    for provider, model, row in _parse_list_models(stdout):
        if provider not in logins:
            continue
        if provider not in available:
            available[provider] = []
            connected.append(provider)
        if model not in available[provider]:
            available[provider].append(model)
        info[(provider, model)] = row
    if not connected:
        raise NoNativeLogin(
            "no logged-in provider found (omodel checks omo's auth.json, provider API-key env "
            "vars and models.json apiKey) — log in with omo's /login"
        )
    return NativeCatalog(available=available, connected=connected, info=info)


def _run_list_models(omo_bin: str) -> str:
    """`omo --list-models` stdout, or CatalogUnavailable (same error rule as `opencode models`:
    unrunnable, exit != 0, a timeout, or no table rows)."""
    try:
        result = subprocess.run(
            [omo_bin, "--list-models"],
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL,
            timeout=_NATIVE_MODELS_TIMEOUT,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise CatalogUnavailable(
            f"`{omo_bin} --list-models` timed out after {_NATIVE_MODELS_TIMEOUT}s"
        ) from exc
    except (OSError, UnicodeDecodeError) as exc:
        raise CatalogUnavailable(f"`{omo_bin} --list-models` could not run: {exc}") from exc
    if result.returncode != 0:
        raise CatalogUnavailable(f"`{omo_bin} --list-models` exited with code {result.returncode}")
    if not _parse_list_models(result.stdout):
        raise CatalogUnavailable(f"`{omo_bin} --list-models` listed no models")
    return result.stdout


def load_native(omo_bin: str = "omo", use_cache: bool = True, agent_dir: str | None = None,
                env=None) -> NativeCatalog:
    """The native catalog. Same contract as `load`: `omo` not on PATH → an empty catalog;
    unrunnable / exit != 0 / no rows → CatalogUnavailable. One more: rows but none from a
    logged-in provider → NoNativeLogin (a CatalogUnavailable). The `omo --list-models` output is
    cached 24h under `native-models`; the login filter is applied fresh every time."""
    if shutil.which(omo_bin) is None:
        return NativeCatalog(available={}, connected=[], info={})
    stdout = cache.read(_NATIVE_CACHE_KEY) if use_cache else None
    if stdout is None or not _parse_list_models(stdout):
        stdout = _run_list_models(omo_bin)
        if use_cache:
            cache.write(_NATIVE_CACHE_KEY, stdout, [omo_bin, "--list-models"])
    return _native_catalog(stdout, native_logins(agent_dir, env))


def refresh_native(omo_bin: str = "omo", agent_dir: str | None = None, env=None) -> NativeCatalog:
    """`r` / `--refresh-models` under native: re-run `omo --list-models` and rewrite its cache
    entry. Only that entry — the opencode cache is left alone. (omo has no `--refresh`: the list
    is the engine's built-in catalog plus `models.json`; `omo models discover` rewrites
    `models.json`, so omodel never runs it.) Raises like `load_native`; on NoNativeLogin the
    cache entry is already rewritten."""
    if shutil.which(omo_bin) is None:
        return NativeCatalog(available={}, connected=[], info={})  # load_native checks PATH first
    stdout = _run_list_models(omo_bin)
    cache.write(_NATIVE_CACHE_KEY, stdout, [omo_bin, "--list-models"])
    return _native_catalog(stdout, native_logins(agent_dir, env))
