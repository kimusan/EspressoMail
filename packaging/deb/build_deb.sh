#!/usr/bin/env bash
set -euo pipefail

# Build Debian (.deb) package for EspressoMail
# Usage: ./build_deb.sh <version> <binary_path> <output_dir>

VERSION="${1:-0.1.0}"
# Clean leading 'v' if present
VERSION="${VERSION#v}"
BINARY_PATH="${2:-dist/espressomail}"
OUTPUT_DIR="${3:-dist/packages}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

if [ ! -f "${BINARY_PATH}" ]; then
    echo "Error: Binary not found at '${BINARY_PATH}'" >&2
    exit 1
fi

BINARY_PATH="$(cd "$(dirname "${BINARY_PATH}")" && pwd)/$(basename "${BINARY_PATH}")"
mkdir -p "${OUTPUT_DIR}"
OUTPUT_DIR="$(cd "${OUTPUT_DIR}" && pwd)"
BUILD_ROOT="$(mktemp -d /tmp/espressomail-deb.XXXXXX)"
trap 'rm -rf "${BUILD_ROOT}"' EXIT

mkdir -p "${BUILD_ROOT}/DEBIAN"
chmod 755 "${BUILD_ROOT}"
chmod 755 "${BUILD_ROOT}/DEBIAN"
mkdir -p "${BUILD_ROOT}/usr/bin"
mkdir -p "${BUILD_ROOT}/usr/share/applications"
mkdir -p "${BUILD_ROOT}/usr/share/icons/hicolor/scalable/apps"
mkdir -p "${BUILD_ROOT}/usr/share/doc/espressomail"

# Copy binary
cp "${BINARY_PATH}" "${BUILD_ROOT}/usr/bin/espressomail"
chmod 755 "${BUILD_ROOT}/usr/bin/espressomail"

# Copy desktop and icon
cp "${REPO_ROOT}/packaging/espressomail.desktop" "${BUILD_ROOT}/usr/share/applications/espressomail.desktop"
chmod 644 "${BUILD_ROOT}/usr/share/applications/espressomail.desktop"

cp "${REPO_ROOT}/packaging/icons/espressomail.svg" "${BUILD_ROOT}/usr/share/icons/hicolor/scalable/apps/espressomail.svg"
chmod 644 "${BUILD_ROOT}/usr/share/icons/hicolor/scalable/apps/espressomail.svg"

# Copy docs and license
cp "${REPO_ROOT}/README.md" "${BUILD_ROOT}/usr/share/doc/espressomail/README.md"
cp "${REPO_ROOT}/LICENSE" "${BUILD_ROOT}/usr/share/doc/espressomail/copyright"

# Create control file
cat <<EOF > "${BUILD_ROOT}/DEBIAN/control"
Package: espressomail
Version: ${VERSION}
Section: mail
Priority: optional
Architecture: amd64
Maintainer: Kim Schulz <kim@schulz.dk>
Depends: libc6
Suggests: chafa, gnupg
Homepage: https://github.com/kimusan/EspressoMail
Description: Modern terminal email client built on Espresso TUI
 EspressoMail is a full-featured terminal email client built on The Elm
 Architecture (TEA). It features multi-account IMAP/SMTP, an offline SQLite
 store with fuzzy full-text search, Sieve filter management, CardDAV address
 book sync, PGP/GPG cryptography, inline image rendering (chafa/sixel/kitty),
 and responsive multi-pane terminal navigation.
EOF

# Calculate installed size in KB
INSTALLED_SIZE="$(du -sk "${BUILD_ROOT}" | cut -f1)"
echo "Installed-Size: ${INSTALLED_SIZE}" >> "${BUILD_ROOT}/DEBIAN/control"
chmod 644 "${BUILD_ROOT}/DEBIAN/control"

DEB_NAME="espressomail_${VERSION}_amd64.deb"
dpkg-deb --build --root-owner-group "${BUILD_ROOT}" "${OUTPUT_DIR}/${DEB_NAME}"

echo "Successfully built Debian package: ${OUTPUT_DIR}/${DEB_NAME}"
