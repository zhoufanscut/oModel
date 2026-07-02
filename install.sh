#!/bin/sh
# install.sh — curl|sh installer for omodel
# Usage: curl -fsSL https://raw.githubusercontent.com/zhoufanscut/oModel/main/install.sh | sh
#
# Detects OS/arch, downloads the matching release tarball from GitHub (verifying its published
# checksum when available), extracts the binary to ~/.local/bin/omodel, and prints a PATH hint
# if needed.
set -e

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
      echo "error: Intel-mac (darwin-x64) binaries are not published; install via pipx:" >&2
      echo "  pipx install git+https://github.com/${REPO}" >&2
      exit 1
    fi
    ARCH_TAG="x64"
    ;;
  arm64|aarch64)
    if [ "${PLATFORM}" = "linux" ]; then
      echo "error: Linux arm64 binaries are not yet published; install via pipx:" >&2
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
WORK_DIR="$(mktemp -d)"
trap 'rm -rf "${WORK_DIR}"' EXIT

echo "Downloading ${TARBALL} (${TAG}) ..."
curl -fsSL --output "${WORK_DIR}/${TARBALL}" "${DOWNLOAD_URL}"

if curl -fsSL --output "${WORK_DIR}/${TARBALL}.sha256" "${CHECKSUM_URL}" 2>/dev/null; then
  echo "Verifying checksum ..."
  if command -v sha256sum > /dev/null 2>&1; then
    VERIFY_CMD="sha256sum -c"
  elif command -v shasum > /dev/null 2>&1; then
    VERIFY_CMD="shasum -a 256 -c"
  else
    VERIFY_CMD=""
  fi

  if [ -n "${VERIFY_CMD}" ]; then
    if ! ( cd "${WORK_DIR}" && ${VERIFY_CMD} "${TARBALL}.sha256" ); then
      echo "error: checksum verification failed for ${TARBALL}" >&2
      exit 1
    fi
  else
    echo "warning: no sha256sum/shasum found; skipping checksum verification" >&2
  fi
else
  echo "warning: checksum file not found for this release; skipping verification" >&2
fi

echo "Extracting ..."
tar xzf "${WORK_DIR}/${TARBALL}" -C "${WORK_DIR}"

mkdir -p "${BIN_DIR}"
DEST="${BIN_DIR}/${BIN_NAME}"
mv "${WORK_DIR}/${BIN_NAME}" "${DEST}"
chmod +x "${DEST}"

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
