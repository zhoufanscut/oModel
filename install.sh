#!/bin/sh
# install.sh — curl|sh installer for omodel
# Usage: curl -fsSL https://raw.githubusercontent.com/zhoufanscut/oModel/main/install.sh | sh
#
# Detects OS/arch, downloads the matching release tarball from GitHub, verifies its published
# sha256 (fail-closed: OMODEL_SKIP_VERIFY=1 is the explicit, documented opt-out), checks the
# binary runs on this machine, installs it to ~/.local/bin/omodel, and prints a PATH hint if
# needed.
#
# Everything is inside main(), called on the LAST line: if the download of this script is cut
# short, the shell has read only a partial function definition and runs nothing at all.
set -e

main() {
  REPO="zhoufanscut/oModel"
  BIN_DIR="${HOME}/.local/bin"
  BIN_NAME="omodel"

  # ---------------------------------------------------------------------------
  # Detect OS
  # ---------------------------------------------------------------------------
  OS="$(uname -s)"
  case "${OS}" in
    Linux*)  PLATFORM="linux" ;;
    Darwin*) PLATFORM="darwin" ;;
    *)
      echo "error: unsupported OS: ${OS}" >&2
      exit 1
      ;;
  esac

  # ---------------------------------------------------------------------------
  # Detect architecture
  # ---------------------------------------------------------------------------
  ARCH="$(uname -m)"
  case "${ARCH}" in
    x86_64|amd64)
      if [ "${PLATFORM}" = "darwin" ]; then
        echo "error: Intel-mac (darwin-x64) binaries are not published; install via pipx (needs Python 3.11+):" >&2
        echo "  pipx install git+https://github.com/${REPO}" >&2
        exit 1
      fi
      ARCH_TAG="x64"
      ;;
    arm64|aarch64)
      if [ "${PLATFORM}" = "linux" ]; then
        echo "error: Linux arm64 binaries are not yet published; install via pipx (needs Python 3.11+):" >&2
        echo "  pipx install git+https://github.com/${REPO}" >&2
        exit 1
      fi
      ARCH_TAG="arm64"
      ;;
    *)
      echo "error: unsupported architecture: ${ARCH}" >&2
      exit 1
      ;;
  esac

  ASSET="${BIN_NAME}-${PLATFORM}-${ARCH_TAG}"

  # ---------------------------------------------------------------------------
  # Resolve the latest release tag from the GitHub API, then download the asset
  # ---------------------------------------------------------------------------
  API_URL="https://api.github.com/repos/${REPO}/releases/latest"

  echo "Fetching latest release info from ${API_URL} ..."
  if command -v curl > /dev/null 2>&1; then
    RELEASE_JSON="$(curl -fsSL "${API_URL}")"
  else
    echo "error: curl is required" >&2
    exit 1
  fi

  # Extract the tag name with minimal tooling (POSIX sh + grep/sed)
  TAG="$(printf '%s\n' "${RELEASE_JSON}" | grep '"tag_name"' | head -n1 | sed 's/.*"tag_name": *"\([^"]*\)".*/\1/')"
  if [ -z "${TAG}" ]; then
    echo "error: could not determine latest release tag" >&2
    exit 1
  fi

  TARBALL="${ASSET}.tar.gz"
  DOWNLOAD_URL="https://github.com/${REPO}/releases/download/${TAG}/${TARBALL}"
  CHECKSUM_URL="${DOWNLOAD_URL}.sha256"

  # ---------------------------------------------------------------------------
  # Download, verify, and install
  # ---------------------------------------------------------------------------
  # Staged INSIDE the install directory, so the final `mv` is a same-filesystem rename — atomic.
  # From /tmp (often another filesystem) it was a copy, and an interrupted re-install could leave
  # a partial binary at ~/.local/bin/omodel.
  mkdir -p "${BIN_DIR}"
  WORK_DIR="$(mktemp -d "${BIN_DIR}/.omodel-install.XXXXXX")"
  trap 'rm -rf "${WORK_DIR}"' EXIT

  echo "Downloading ${TARBALL} (${TAG}) ..."
  curl -fsSL --output "${WORK_DIR}/${TARBALL}" "${DOWNLOAD_URL}"

  # Fail-closed. Every release since v0.2.0 publishes a .sha256 and this script only installs
  # the latest one, so a checksum that can't be fetched or checked is never "an old release".
  # Treating any fetch failure (a 5xx, a dropped connection) as "no checksum" and installing
  # anyway made the check optional exactly when it mattered; the reason was hidden as well.
  if [ "${OMODEL_SKIP_VERIFY:-}" = "1" ]; then
    echo "warning: OMODEL_SKIP_VERIFY=1 — installing WITHOUT checking the sha256" >&2
  else
    if ! curl -fsSL --output "${WORK_DIR}/${TARBALL}.sha256" "${CHECKSUM_URL}"; then
      echo "error: could not download the checksum ${CHECKSUM_URL} (see above)." >&2
      echo "  Nothing was installed. Retry, or set OMODEL_SKIP_VERIFY=1 to install unverified." >&2
      exit 1
    fi
    if command -v sha256sum > /dev/null 2>&1; then
      VERIFY_CMD="sha256sum -c"
    elif command -v shasum > /dev/null 2>&1; then
      VERIFY_CMD="shasum -a 256 -c"
    else
      echo "error: neither sha256sum nor shasum is installed, so the download can't be checked." >&2
      echo "  Nothing was installed. Install one, or set OMODEL_SKIP_VERIFY=1." >&2
      exit 1
    fi
    echo "Verifying checksum ..."
    if ! ( cd "${WORK_DIR}" && ${VERIFY_CMD} "${TARBALL}.sha256" ); then
      echo "error: checksum verification failed for ${TARBALL} — nothing was installed" >&2
      exit 1
    fi
  fi

  echo "Extracting ..."
  tar xzf "${WORK_DIR}/${TARBALL}" -C "${WORK_DIR}"
  chmod +x "${WORK_DIR}/${BIN_NAME}"

  # Run it before installing it, as `omodel --update` does: the prebuilt linux binary needs a
  # glibc at least as new as the build runner's, and on an older distro it fails to start. Better
  # to stop here with the way out than to install something that can't run.
  if ! "${WORK_DIR}/${BIN_NAME}" --version > /dev/null 2>&1; then
    echo "error: the downloaded omodel does not run on this machine (an older glibc?)." >&2
    echo "  Nothing was installed. Install from source instead (needs Python 3.11+):" >&2
    echo "    pipx install git+https://github.com/${REPO}" >&2
    exit 1
  fi

  DEST="${BIN_DIR}/${BIN_NAME}"
  mv "${WORK_DIR}/${BIN_NAME}" "${DEST}"

  echo ""
  echo "Installed: ${DEST}"

  # ---------------------------------------------------------------------------
  # PATH hint
  # ---------------------------------------------------------------------------
  case ":${PATH}:" in
    *":${BIN_DIR}:"*)
      # Already on PATH — nothing to print
      ;;
    *)
      echo ""
      echo "  ${BIN_DIR} is not on your PATH."
      echo "  Add the following line to your shell profile (~/.bashrc, ~/.zshrc, …):"
      echo ""
      echo "    export PATH=\"\${HOME}/.local/bin:\${PATH}\""
      echo ""
      ;;
  esac

  echo "Run \`omodel --version\` to verify the installation."
}

main "$@"
