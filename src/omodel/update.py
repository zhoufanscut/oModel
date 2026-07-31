"""`omodel --update` — self-update from GitHub Releases.  DESIGN.md §update.py.

This is the only module that touches the network at runtime, and it does so ONLY when the user
passes `--update` (there is no launch-time version ping: omodel's promise is that a normal run
needs neither an omo checkout nor a network call, and a check on every launch would trade that
away for a line of text nobody asked for).

Stdlib only — `urllib` + `tarfile` + `hashlib`. Adding `requests` to buy nothing would put a
transitive dependency inside the PyInstaller binary this module exists to replace.

**What "update" means depends on how omodel was installed**, so the first thing we do is work
that out (`detect_install`):

  * the **standalone binary** from `install.sh` — the one install kind we can update ourselves,
    by swapping the file in place (`apply_update`);
  * **pipx / uv / pip / a source checkout** — owned by another tool, so we print the exact
    command and stop. Reaching into someone else's venv is how you end up with a half-installed
    package and no way back.

The swap is the delicate part, and the order below is deliberate:

  1. download the tarball into a temp dir **inside the target's own directory**, so the final
     `os.replace` is same-filesystem and therefore atomic (`/tmp` is often a different mount →
     `EXDEV`);
  2. verify the release's published `.sha256` (same rule as `install.sh`: hard-fail on a
     mismatch, warn-and-continue when the release has no checksum asset);
  3. extract ONLY the `omodel` member, to a path we choose — never `extractall`, which would let
     a crafted tarball write anywhere (and 3.9 has no `filter="data"`);
  4. **run the new binary's `--version` and require it to print the release's version.** This is
     the guard that matters: a linux binary built against a newer glibc, a truncated download, a
     mis-uploaded asset — all of them fail here, while the user's working omodel is still in
     place. Nothing is swapped until a downloaded binary has proven it runs on THIS machine;
  5. `os.replace` over the running executable. POSIX renames the directory entry, so the running
     process keeps its own inode and finishes normally — you cannot *write* to a busy executable
     (`ETXTBSY`), but you can always replace it.
"""
from __future__ import annotations

import contextlib
import hashlib
import http.client
import json
import os
import platform
import re
import shlex
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field

from omodel import __version__

# Distribution is GitHub-only (DESIGN §Packaging). Hardcoded, as in `install.sh` — the two must
# name the same repo, and pyproject's URLs are not readable from the frozen binary.
REPO = "zhoufanscut/oModel"
API_ROOT = "https://api.github.com/"
API_LATEST = f"{API_ROOT}repos/{REPO}/releases/latest"
RELEASES_URL = f"https://github.com/{REPO}/releases"
GIT_SPEC = f"git+https://github.com/{REPO}"

# Timeouts are per socket operation, not per transfer — a stalled read is what we are guarding
# against, not a slow-but-progressing download.
META_TIMEOUT = 15
DOWNLOAD_TIMEOUT = 120
# The new binary's own `--version`: a PyInstaller one-file binary unpacks itself to a temp dir on
# first run, which on a cold page cache is seconds, not milliseconds.
SMOKE_TIMEOUT = 90

_USER_AGENT = f"omodel/{__version__} (+https://github.com/{REPO})"


class UpdateError(Exception):
    """Anything that stopped an update. `kind` is the machine-readable reason (`--json`'s
    `error`), and `cli.py` maps it to an exit code: an environmental refusal the user can act on
    (unsupported platform, unwritable install) is a 3, a genuine failure (network, checksum,
    smoke test) is a 1."""

    def __init__(self, kind: str, message: str) -> None:
        super().__init__(message)
        self.kind = kind


# ---------------------------------------------------------------------------
# Versions
# ---------------------------------------------------------------------------

def parse_version(text) -> tuple:
    """`"v0.3.1"` → `(0, 3, 1)`. `()` when nothing numeric is there.

    Deliberately lenient: this reads a *release tag*, which is written by a human under time
    pressure. Leading `v`, a `+build` or `-suffix` tail, and a short `0.4` are all accepted. A
    trailing non-numeric chunk is truncated (`0.4.0rc1` → `(0, 4, 0)`), so a pre-release compares
    equal to its final — harmless here, since `releases/latest` never returns a pre-release."""
    if not isinstance(text, str):
        return ()
    core = text.strip().lstrip("vV").split("+")[0].split("-")[0]
    parts = []
    for chunk in core.split("."):
        match = re.match(r"\d+", chunk)
        if match is None:
            break
        parts.append(int(match.group()))
    return tuple(parts)


def is_newer(candidate, current) -> bool:
    """Is `candidate` (a release tag) a later version than `current`?

    An unparseable candidate is never newer — the failure mode we care about is claiming an
    update that isn't there and then swapping the user's binary for it."""
    new = parse_version(candidate)
    old = parse_version(current)
    if not new:
        return False
    width = max(len(new), len(old))
    return new + (0,) * (width - len(new)) > old + (0,) * (width - len(old))


# ---------------------------------------------------------------------------
# Platform → release asset
# ---------------------------------------------------------------------------

def platform_asset() -> str | None:
    """The asset base name for this machine, or None if no binary is published for it.

    Mirrors `install.sh`'s detection and `release.yml`'s matrix — **linux-x64 and darwin-arm64
    only**. Intel macs and linux-arm64 install via pipx; they get a `command`, not a download."""
    system = platform.system()
    machine = platform.machine().lower()
    if system == "Linux" and machine in ("x86_64", "amd64"):
        return "omodel-linux-x64"
    if system == "Darwin" and machine in ("arm64", "aarch64"):
        return "omodel-darwin-arm64"
    return None


# ---------------------------------------------------------------------------
# How was this omodel installed?
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Install:
    """Where this omodel lives and who owns it.

    `kind` ∈ `binary` (PyInstaller one-file — the self-updatable one) · `pipx` · `uv` · `pip`
    (any other Python environment) · `source` (an editable install / repo checkout).
    `path` is the file we would replace (`binary`) or the checkout root (`source`), else None."""

    kind: str
    path: str | None = None

    @property
    def self_updatable(self) -> bool:
        return self.kind == "binary"

    def command_for(self, tag: str) -> str:
        """The command that updates THIS install to `tag`.

        Tag-pinned on purpose: `pipx upgrade` re-resolves a `git+` spec against the default
        branch, which is not necessarily the release we just told the user about, and pip can
        decide an unchanged version is already satisfied. Naming the tag makes it exact."""
        spec = f'"{GIT_SPEC}@{tag}"'
        if self.kind == "pipx":
            return f"pipx install --force {spec}"
        if self.kind == "uv":
            return f"uv tool install --force {spec}"
        if self.kind == "source":
            # shlex.quote throughout: these are meant to be copy-pasted, and a path with a space
            # (`~/My Projects/oModel`, a macOS framework python) silently becomes two arguments.
            root = shlex.quote(self.path or ".")
            return f"git -C {root} fetch --tags && git -C {root} checkout {tag}"
        if self.kind == "binary":
            return "omodel --update"
        return f"{shlex.quote(sys.executable)} -m pip install --upgrade {spec}"


def detect_install() -> Install:
    """Classify the running omodel. Never raises — an unrecognized layout falls back to `pip`,
    whose command (`<this python> -m pip install --upgrade …`) is correct for any environment we
    failed to name."""
    # PyInstaller sets sys.frozen AND sys._MEIPASS; sys.executable is then the one-file binary
    # itself (NOT the unpacked _MEIPASS dir). Both are required, because `frozen` alone is a
    # convention several freezers and embedders set, and the thing we do with this answer is
    # `os.replace` over sys.executable — mistaking a real interpreter for our binary would
    # overwrite THAT, and the smoke test can't catch it (it validates the download, never the
    # target). `_MEIPASS` is PyInstaller-specific; anything else falls through to a printed
    # command, which is the safe way to be wrong. realpath so a symlinked ~/.local/bin/omodel
    # updates the real file and leaves the symlink alone.
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return Install("binary", os.path.realpath(sys.executable))

    prefix_parts = os.path.realpath(sys.prefix).split(os.sep)
    # pipx venvs live at $PIPX_HOME/venvs/<pkg> — ~/.local/pipx (older) or ~/.local/share/pipx
    # (newer), and $PIPX_HOME may point anywhere; the `pipx/venvs` pair is the stable part.
    if "pipx" in prefix_parts:
        return Install("pipx")
    # `uv` alone, not `uv` + `tools`: `uv tool install` lands in …/uv/tools/<name>, but a `uvx`
    # run (which README documents) executes from …/uv/archive-v0/<hash>, where the old check
    # missed and fell through to `pip` — and uv-created environments generally have no `pip`, so
    # the command we printed could not run at all.
    if "uv" in prefix_parts:
        return Install("uv")
    root = _source_checkout()
    if root is not None:
        return Install("source", root)
    return Install("pip")


def _source_checkout() -> str | None:
    """The repo root when we are running out of a checkout (`pip install -e .`), else None.
    Recognized by the src-layout the package actually ships in: `<root>/src/omodel/update.py`
    with a `pyproject.toml` and a `.git` beside it."""
    here = os.path.dirname(os.path.abspath(__file__))          # <root>/src/omodel
    root = os.path.dirname(os.path.dirname(here))              # <root>
    if os.path.basename(os.path.dirname(here)) != "src":
        return None
    if os.path.isfile(os.path.join(root, "pyproject.toml")) and os.path.exists(
        os.path.join(root, ".git")
    ):
        return root
    return None


# ---------------------------------------------------------------------------
# GitHub Releases
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Release:
    """One GitHub release, reduced to what an update needs."""

    tag: str
    version: str                       # tag without the leading `v` — what `--version` prints
    url: str                           # html_url, for "read the notes"
    published_at: str
    assets: dict = field(default_factory=dict)   # asset name -> browser_download_url


def latest_release(timeout: float = META_TIMEOUT) -> Release:
    """The repo's latest published release. Raises `UpdateError` on anything else.

    `releases/latest` — not `releases[0]` — so drafts and pre-releases are excluded by GitHub
    rather than by us."""
    data = _get_json(API_LATEST, timeout)
    tag = data.get("tag_name")
    if not isinstance(tag, str) or not tag.strip():
        raise UpdateError(
            "no_release",
            f"the GitHub API returned a release with no tag — see {RELEASES_URL}",
        )
    tag = tag.strip()
    # One leading `v`, matching release.yml's `${GITHUB_REF_NAME#v}` — `lstrip("vV")` would eat
    # every one of them, so a `vv1.0` tag would disagree with the version the binary prints and
    # the smoke test would refuse a release that is actually fine.
    version = tag[1:] if tag[:1] in ("v", "V") else tag
    assets = {}
    for asset in data.get("assets") or []:
        if isinstance(asset, dict) and asset.get("name") and asset.get("browser_download_url"):
            assets[asset["name"]] = asset["browser_download_url"]
    return Release(
        tag=tag,
        version=version,
        url=data.get("html_url") or f"{RELEASES_URL}/tag/{tag}",
        published_at=data.get("published_at") or "",
        assets=assets,
    )


class _StripAuthOnRedirect(urllib.request.HTTPRedirectHandler):
    """Drop `Authorization` when a redirect crosses to another host.

    urllib re-sends the original request's headers on a redirect, including to a different host.
    GitHub redirects release-asset downloads to `objects.githubusercontent.com`, so without this
    a `$GITHUB_TOKEN` would be handed to a host that never needed it. We only ever authenticate
    against api.github.com, and only to dodge the unauthenticated rate limit."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        new = super().redirect_request(req, fp, code, msg, headers, newurl)
        if new is None:
            return new
        # Host OR scheme: an https→http hop on the same host would put the token on the wire in
        # cleartext, which is the one outcome worse than dropping a header we didn't need.
        if urllib.parse.urlsplit(newurl).scheme.lower() != "https":
            # Don't follow it at all. `_open`'s https check only sees the FIRST url, so without
            # this a 302 could still walk the update onto plain http (or a file:// handler
            # urllib's default opener still carries). Returning None makes urllib raise the
            # HTTPError, which `_open` reports as `http_error`.
            return None
        if _host(newurl) != _host(req.full_url):
            for name in [k for k in new.headers if k.lower() == "authorization"]:
                del new.headers[name]
        return new


def _host(url: str) -> str:
    return urllib.parse.urlsplit(url).netloc.lower()


# What a read can raise that ISN'T an OSError. `http.client.IncompleteRead` (a chunked transfer
# cut short mid-body) descends from HTTPException → Exception, so it sails straight through an
# `except OSError` — out of this module, out of the verb's `except UpdateError`, and onto the
# terminal as a traceback with an EMPTY --json stdout. Every read below catches both.
_READ_ERRORS = (OSError, http.client.HTTPException)


_opener = None


def _open(url: str, timeout: float):
    """Open `url` for reading, or raise `UpdateError`. The single network seam in this module —
    everything else goes through it, and tests monkeypatch this (three of them patch `_opener`
    one level lower, to exercise `_open` itself)."""
    global _opener
    if _opener is None:
        _opener = urllib.request.build_opener(_StripAuthOnRedirect())

    # https only. `build_opener` keeps urllib's default File/FTP/Data handlers, so a
    # `browser_download_url` of `file:///…` — which we read straight out of the release JSON —
    # would otherwise be opened as happily as an https one. Nothing legitimate is ever non-https
    # here, so the whole class goes away for the price of one check.
    if urllib.parse.urlsplit(url).scheme.lower() != "https":
        raise UpdateError("bad_url", f"refusing to fetch a non-https URL: {url}")

    request = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    # A token is optional and only for the API: 60 requests/hour/IP is generous for a manual
    # command but not for a shared NAT, and a 403 there reads like a broken updater. Never sent
    # to an asset download — those are public, and see _StripAuthOnRedirect.
    if url.startswith(API_ROOT):
        request.add_header("Accept", "application/vnd.github+json")
        token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
        if token:
            request.add_header("Authorization", f"Bearer {token.strip()}")
    try:
        return _opener.open(request, timeout=timeout)
    except urllib.error.HTTPError as exc:
        if exc.code in (403, 429):
            raise UpdateError(
                "rate_limited",
                f"GitHub rate-limited this request (HTTP {exc.code}) — wait a few minutes, or set "
                f"$GITHUB_TOKEN. You can always download from {RELEASES_URL}",
            ) from exc
        if exc.code == 404:
            raise UpdateError(
                "not_found",
                f"{url} returned HTTP 404 — no published release yet?",
            ) from exc
        raise UpdateError(
            "http_error", f"{url} returned HTTP {exc.code}"
        ) from exc
    except _READ_ERRORS as exc:
        # URLError wraps DNS/TLS/connection-refused; a socket timeout arrives as OSError.
        raise UpdateError(
            "network", f"could not reach {_host(url) or url}: {exc}"
        ) from exc


def _get_json(url: str, timeout: float) -> dict:
    response = _open(url, timeout)
    try:
        raw = response.read()
    except _READ_ERRORS as exc:
        raise UpdateError("network", f"could not read {_host(url) or url}: {exc}") from exc
    finally:
        _close(response)
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise UpdateError("bad_response", f"{url} did not return JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise UpdateError("bad_response", f"{url} did not return a JSON object")
    return data


def _get_text(url: str, timeout: float) -> str:
    response = _open(url, timeout)
    try:
        return response.read().decode("utf-8", "replace")
    except _READ_ERRORS as exc:
        raise UpdateError("network", f"could not read {_host(url) or url}: {exc}") from exc
    finally:
        _close(response)


def _download(url: str, dest: str, timeout: float) -> None:
    """Stream `url` to `dest`. Streamed rather than `read()` — the binary is tens of MB and
    there is no reason to hold it all in memory on a machine we are already asking to unpack a
    PyInstaller bundle.

    A transfer that dies mid-body is a NETWORK error, not a write error: the two are separated
    here because the messages point at different things to go and check."""
    response = _open(url, timeout)
    try:
        with open(dest, "wb") as handle:
            shutil.copyfileobj(response, handle)
    except OSError as exc:
        raise UpdateError("write_failed", f"could not write {dest}: {exc}") from exc
    except http.client.HTTPException as exc:
        raise UpdateError(
            "network", f"the download from {_host(url) or url} ended early: {exc}"
        ) from exc
    finally:
        _close(response)


def _close(response) -> None:
    """Best-effort close. The bytes are already read (or the read already failed) by the time
    this runs, so a socket that objects to being closed must not become the reported error."""
    with contextlib.suppress(Exception):
        response.close()


# ---------------------------------------------------------------------------
# The swap
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class UpdateResult:
    """What `apply_update` did. `verified` is False only when the release published no
    `.sha256` asset (older releases) — `install.sh` warns and continues there too, and the
    smoke test still has to pass either way."""

    path: str
    version: str
    verified: bool


def preflight(install: Install, release: Release | None = None) -> str:
    """Everything that makes an update impossible, checked BEFORE anyone is asked to confirm
    one — returns the tarball asset name. Raises `UpdateError`.

    Split out of `apply_update` so `cli.py` can run it ahead of the prompt: being asked "Update
    now? [y/N]", answering yes, and only then being told this platform has no binary (or that
    the install directory isn't yours to write) is a question that should never have been put.
    `apply_update` calls it too — it is a public entry point and must stay safe on its own."""
    if not install.self_updatable or not install.path:
        # With no release in hand there is no tag to pin, and interpolating a placeholder would
        # print a command that looks copy-pasteable and isn't (`…@the latest tag`). Point at the
        # releases page instead; `cli.py` always has the release, so users see the real command.
        detail = (
            f"run: {install.command_for(release.tag)}" if release is not None
            else f"see {RELEASES_URL} for the current release, then reinstall with {install.kind}"
        )
        raise UpdateError(
            "not_self_updatable",
            f"this omodel was not installed as a standalone binary — {detail}",
        )

    base = platform_asset()
    if base is None:
        raise UpdateError(
            "unsupported_platform",
            f"no binary is published for {platform.system()}/{platform.machine()} — install with `pipx install {GIT_SPEC}`",
        )

    target_dir = os.path.dirname(install.path) or "."
    # `os.replace` needs write permission on the DIRECTORY, not the file — a root-owned binary in
    # a user-writable dir is replaceable, and a user-owned one in /usr/local/bin is not.
    if not os.access(target_dir, os.W_OK):
        raise UpdateError(
            "not_writable",
            f"{target_dir} is not writable by this user — re-run the installer with the right permissions: "
            f"curl -fsSL https://raw.githubusercontent.com/{REPO}/main/install.sh | sh",
        )

    tarball = f"{base}.tar.gz"
    if release is not None and not release.assets.get(tarball):
        raise UpdateError(
            "missing_asset",
            "release {} has no {} asset (it publishes: {}) — see {}".format(
                release.tag, tarball, ", ".join(sorted(release.assets)) or "nothing", release.url
            ),
        )
    return tarball


def apply_update(
    release: Release,
    install: Install,
    timeout: float = DOWNLOAD_TIMEOUT,
    on_step=None,
) -> UpdateResult:
    """Download `release` and replace `install.path` with it. Raises `UpdateError`; on ANY
    failure the existing binary is left exactly as it was.

    `on_step(message)` receives progress lines (the CLI prints them in prose mode and passes
    None for `--json`, where stdout must stay a single object)."""
    def step(message: str) -> None:
        if on_step is not None:
            on_step(message)

    tarball = preflight(install, release)
    url = release.assets[tarball]
    target = install.path
    target_dir = os.path.dirname(target) or "."
    _sweep_stale(target_dir)

    # Temp dir INSIDE the target's directory: os.replace is only atomic within one filesystem,
    # and $TMPDIR is very often a different mount (→ EXDEV).
    try:
        work = tempfile.mkdtemp(prefix=".omodel-update-", dir=target_dir)
    except OSError as exc:
        raise UpdateError(
            "not_writable", f"could not create a temp dir in {target_dir}: {exc}"
        ) from exc

    try:
        archive = os.path.join(work, tarball)
        step(f"Downloading {tarball} ({release.tag}) ...")
        _download(url, archive, timeout)

        verified = _verify_checksum(release, tarball, archive, timeout, step)

        step("Extracting ...")
        staged = os.path.join(work, "omodel.new")
        _extract_binary(archive, staged)
        _apply_mode(target, staged)

        step("Checking the new binary ...")
        _smoke_test(staged, release.version)

        os.replace(staged, target)
        _sync_dir(target_dir)
    except OSError as exc:
        raise UpdateError("install_failed", f"could not install {target}: {exc}") from exc
    finally:
        shutil.rmtree(work, ignore_errors=True)

    return UpdateResult(path=target, version=release.version, verified=verified)


# A run killed between mkdtemp and its cleanup (SIGKILL, power loss) leaves a temp dir holding a
# whole release tarball — tens of MB — in the user's bin directory, and nothing else would ever
# remove it. Only sweep OLD ones: a concurrent `--update` is the other reason a
# `.omodel-update-*` exists, and deleting its download mid-flight would be a self-inflicted
# failure.
_STALE_AFTER = 3600.0


def _sweep_stale(target_dir: str) -> None:
    """Best-effort removal of temp dirs abandoned by an earlier, killed run."""
    try:
        names = os.listdir(target_dir)
    except OSError:
        return
    now = time.time()
    for name in names:
        if not name.startswith(".omodel-update-"):
            continue
        path = os.path.join(target_dir, name)
        if now - _touched_at(path) >= _STALE_AFTER:
            shutil.rmtree(path, ignore_errors=True)


def _touched_at(path: str) -> float:
    """When anything in `path` last changed — the newest mtime of the directory OR its contents.

    The directory's own mtime is NOT enough, and that is the whole point: a directory's mtime
    advances when an entry is created, renamed or removed, and **not** when a file inside it is
    written. Judging staleness by it measures time since `mkdtemp`, not time since anything
    happened — so a download slower than `_STALE_AFTER` (`DOWNLOAD_TIMEOUT` is per socket
    operation, not per transfer, so a big asset on a bad link can run for hours) would look
    abandoned to a second `--update`, which would then delete a live run's work out from under
    it. The archive file's mtime does advance, so the newest-of-both closes it.

    Returns `now` — i.e. "brand new, do not touch" — if the directory can't be read at all."""
    newest = 0.0
    try:
        newest = os.stat(path).st_mtime
        for name in os.listdir(path):
            with contextlib.suppress(OSError):
                newest = max(newest, os.stat(os.path.join(path, name)).st_mtime)
    except OSError:
        return time.time()
    return newest


def _sync_dir(path: str) -> None:
    """fsync the directory so the rename itself survives a crash. Best-effort: not every
    filesystem (or platform) allows opening a directory for fsync, and a working update must not
    fail on the last line because of it."""
    with contextlib.suppress(OSError, AttributeError):
        fd = os.open(path, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def _verify_checksum(release, tarball: str, archive: str, timeout: float, step) -> bool:
    """Check the tarball against the release's published `.sha256`.

    Same rule as `install.sh`: a mismatch is fatal, a MISSING checksum asset warns and
    continues (releases before the checksum step existed have none). Returning the verdict
    rather than swallowing it lets `--json` report `verified: false` instead of implying a
    verification that never happened."""
    sums_url = release.assets.get(f"{tarball}.sha256")
    if not sums_url:
        step("warning: this release publishes no checksum — skipping verification")
        return False

    step("Verifying checksum ...")
    expected = _get_text(sums_url, timeout).split()
    if not expected:
        step("warning: checksum file is empty — skipping verification")
        return False

    digest = hashlib.sha256()
    with open(archive, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    actual = digest.hexdigest()
    if actual.lower() != expected[0].strip().lower():
        raise UpdateError(
            "checksum_mismatch",
            f"{tarball} does not match its published sha256 (expected {expected[0].strip()}, got {actual}) — nothing was "
            "installed",
        )
    return True


def _extract_binary(archive: str, dest: str) -> None:
    """Extract the single `omodel` member of `archive` to `dest`.

    Never `extractall`: the destination is ours to choose, so a crafted tarball has nowhere to
    write (`..` members, absolute paths, symlink tricks). 3.9 has no `filter="data"` — this is
    the portable form of the same guarantee, and it also means LICENSE/NOTICE, which the tarball
    also carries, are simply not written."""
    try:
        with tarfile.open(archive, "r:gz") as tar:
            member = None
            for candidate in tar.getmembers():
                if candidate.isfile() and os.path.basename(candidate.name) == "omodel":
                    member = candidate
                    break
            if member is None:
                raise UpdateError(
                    "bad_asset", f"{os.path.basename(archive)} contains no `omodel` binary"
                )
            source = tar.extractfile(member)
            if source is None:
                raise UpdateError(
                    "bad_asset", f"could not read `omodel` out of {os.path.basename(archive)}"
                )
            with source, open(dest, "wb") as handle:
                shutil.copyfileobj(source, handle)
                # fsync BEFORE the caller renames this over the live binary. `os.replace` is
                # atomic against a concurrent reader, not against power loss: the rename can
                # reach disk while the file's data has not, leaving a zero-length `omodel` —
                # exactly the broken install this module promises never to produce.
                #
                # Best-effort, deliberately: some FUSE and network mounts fail fsync outright
                # (EINVAL/ENOSYS), and there was NO durability guarantee here before, so a
                # refused fsync must not turn a working update into a failed one.
                handle.flush()
                with contextlib.suppress(OSError):
                    os.fsync(handle.fileno())
    except tarfile.TarError as exc:
        raise UpdateError(
            "bad_asset", f"{os.path.basename(archive)} is not a readable tarball: {exc}"
        ) from exc


def _apply_mode(target: str, staged: str) -> None:
    """Give the new file the old one's permissions (plus executable). Inheriting the mode keeps
    a deliberately-restricted install (say 0700 in a shared home) restricted after an update."""
    mode = 0o755
    try:
        mode = stat.S_IMODE(os.stat(target).st_mode)
    except OSError:
        pass
    os.chmod(staged, mode | stat.S_IXUSR | stat.S_IRUSR)


def _child_env() -> dict:
    """The environment for running the DOWNLOADED binary — PyInstaller's own leftovers removed.

    Here the parent is a frozen one-file binary and the child is a *different* frozen one-file
    binary, which is the one case PyInstaller's runtime environment actively breaks:

      * the bootloader points `LD_LIBRARY_PATH` (`DYLD_LIBRARY_PATH` on macOS) at **this**
        binary's unpacked `_MEIPASS`, stashing any previous value in `…_ORIG`. A child inheriting
        it resolves its shared libraries out of the OLD build's temp dir — across a Python or
        OpenSSL bump that is a loader error, not an omodel error;
      * `_MEIPASS2` / `_PYI_*` are how the bootloader tells its own second stage where the
        archive is. Leaked into a child, they point it at the wrong archive entirely.

    Either one would fail the smoke test on a perfectly good release and, because a failed smoke
    test refuses to install, would leave `--update` permanently "broken" for that user with
    no hint as to why. Restore what PyInstaller saved, drop what it invented — the documented
    recipe for launching another program from a frozen app."""
    env = dict(os.environ)
    for name in ("LD_LIBRARY_PATH", "DYLD_LIBRARY_PATH", "LIBPATH"):
        original = env.pop(name + "_ORIG", None)
        if original is not None:
            env[name] = original
        elif getattr(sys, "frozen", False):
            # No `_ORIG` under a frozen run means the bootloader set the var from nothing.
            env.pop(name, None)
    for name in [k for k in env if k.startswith(("_MEIPASS", "_PYI_"))]:
        del env[name]
    return env


def _smoke_test(binary: str, expected_version: str) -> None:
    """Run the downloaded binary's `--version` and require the release's version back.

    The single most valuable step here. A linux binary built against a newer glibc than this
    machine's (the documented failure of the prebuilt asset), a truncated download, an asset
    uploaded from the wrong build — each one dies here, with the user's working omodel still
    installed and the reason on screen."""
    try:
        result = subprocess.run(
            [binary, "--version"],
            capture_output=True,
            text=True,
            timeout=SMOKE_TIMEOUT,
            check=False,   # a non-zero exit is the very thing being tested for
            env=_child_env(),
        )
    except subprocess.TimeoutExpired as exc:
        raise UpdateError(
            "smoke_failed",
            f"the downloaded binary did not answer `--version` within {SMOKE_TIMEOUT}s — nothing was "
            "installed",
        ) from exc
    except OSError as exc:
        raise UpdateError(
            "smoke_failed",
            f"the downloaded binary would not run ({exc}) — nothing was installed",
        ) from exc

    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip().splitlines()
        raise UpdateError(
            "smoke_failed",
            "the downloaded binary exited {} on `--version` ({}) — nothing was installed".format(
                result.returncode, detail[0] if detail else "no output"
            ),
        )

    reported = (result.stdout or "").strip()
    if reported != expected_version:
        raise UpdateError(
            "version_mismatch",
            f"the downloaded binary reports {reported!r}, but the release is {expected_version!r} — nothing was "
            "installed",
        )
