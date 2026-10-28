#!/usr/bin/env bash
set -euo pipefail

# Build Arch Linux (.pkg.tar.zst) package for EspressoMail
# Usage: ./build_arch.sh <version> <binary_path> <output_dir>

VERSION="${1:-0.1.0}"
VERSION="${VERSION#v}"
BINARY_PATH="${2:-dist/espressomail}"
OUTPUT_DIR="${3:-dist/packages}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

if [ ! -f "${BINARY_PATH}" ]; then
    echo "Error: Binary not found at '${BINARY_PATH}'" >&2
    exit 1
fi

mkdir -p "${OUTPUT_DIR}"
BUILD_DIR="$(mktemp -d /tmp/espressomail-arch.XXXXXX)"
trap 'rm -rf "${BUILD_DIR}"' EXIT

# Prepare sources in build dir
cp "${SCRIPT_DIR}/PKGBUILD" "${BUILD_DIR}/PKGBUILD"
sed -i "s/^pkgver=.*/pkgver=${VERSION}/" "${BUILD_DIR}/PKGBUILD"
cp "${BINARY_PATH}" "${BUILD_DIR}/espressomail"
cp "${REPO_ROOT}/packaging/espressomail.desktop" "${BUILD_DIR}/espressomail.desktop"
cp "${REPO_ROOT}/packaging/icons/espressomail.svg" "${BUILD_DIR}/espressomail.svg"
cp "${REPO_ROOT}/LICENSE" "${BUILD_DIR}/LICENSE"
cp "${REPO_ROOT}/README.md" "${BUILD_DIR}/README.md"

if command -v makepkg &>/dev/null; then
    echo "Building Arch package using local makepkg..."
    (
        cd "${BUILD_DIR}"
        makepkg -f --nodeps
    )
    find "${BUILD_DIR}" -name "*.pkg.tar.*" -exec cp {} "${OUTPUT_DIR}/" \;
elif command -v bsdtar &>/dev/null && command -v zstd &>/dev/null; then
    echo "Building Arch package using bsdtar and zstd..."
    PKG_ROOT="${BUILD_DIR}/pkg"
    mkdir -p "${PKG_ROOT}/usr/bin"
    mkdir -p "${PKG_ROOT}/usr/share/applications"
    mkdir -p "${PKG_ROOT}/usr/share/icons/hicolor/scalable/apps"
    mkdir -p "${PKG_ROOT}/usr/share/licenses/espressomail"
    mkdir -p "${PKG_ROOT}/usr/share/doc/espressomail"

    install -m 755 "${BINARY_PATH}" "${PKG_ROOT}/usr/bin/espressomail"
    install -m 644 "${REPO_ROOT}/packaging/espressomail.desktop" "${PKG_ROOT}/usr/share/applications/espressomail.desktop"
    install -m 644 "${REPO_ROOT}/packaging/icons/espressomail.svg" "${PKG_ROOT}/usr/share/icons/hicolor/scalable/apps/espressomail.svg"
    install -m 644 "${REPO_ROOT}/LICENSE" "${PKG_ROOT}/usr/share/licenses/espressomail/LICENSE"
    install -m 644 "${REPO_ROOT}/README.md" "${PKG_ROOT}/usr/share/doc/espressomail/README.md"

    INSTALLED_SIZE=$(du -sb "${PKG_ROOT}" | cut -f1)
    BUILD_DATE=$(date +%s)

    cat <<EOF > "${PKG_ROOT}/.PKGINFO"
pkgname = espressomail
pkgbase = espressomail
pkgver = ${VERSION}-1
pkgdesc = A modern, multi-account terminal email client built on Espresso TUI
url = https://github.com/kimusan/EspressoMail
builddate = ${BUILD_DATE}
packager = Kim Schulz <kim@schulz.dk>
size = ${INSTALLED_SIZE}
arch = x86_64
license = MIT
depend = glibc
optdepend = chafa: high-resolution inline terminal image rendering
optdepend = gnupg: PGP/GPG email signing and encryption
provides = espressomail
conflict = espressomail
EOF

    (
        cd "${PKG_ROOT}"
        LANG=C bsdtar -czf .MTREE --format=mtree \
            --options='!all,use-set,type,uid,gid,mode,time,size,sha256,link' \
            .PKGINFO usr
        bsdtar --uid 0 --gid 0 -cf - .PKGINFO .MTREE usr | zstd -c -T0 > "${OUTPUT_DIR}/espressomail-${VERSION}-1-x86_64.pkg.tar.zst"
    )
else
    echo "Building Arch package using tar and zstd..."
    PKG_ROOT="${BUILD_DIR}/pkg"
    mkdir -p "${PKG_ROOT}/usr/bin"
    mkdir -p "${PKG_ROOT}/usr/share/applications"
    mkdir -p "${PKG_ROOT}/usr/share/icons/hicolor/scalable/apps"
    mkdir -p "${PKG_ROOT}/usr/share/licenses/espressomail"
    mkdir -p "${PKG_ROOT}/usr/share/doc/espressomail"

    install -m 755 "${BINARY_PATH}" "${PKG_ROOT}/usr/bin/espressomail"
    install -m 644 "${REPO_ROOT}/packaging/espressomail.desktop" "${PKG_ROOT}/usr/share/applications/espressomail.desktop"
    install -m 644 "${REPO_ROOT}/packaging/icons/espressomail.svg" "${PKG_ROOT}/usr/share/icons/hicolor/scalable/apps/espressomail.svg"
    install -m 644 "${REPO_ROOT}/LICENSE" "${PKG_ROOT}/usr/share/licenses/espressomail/LICENSE"
    install -m 644 "${REPO_ROOT}/README.md" "${PKG_ROOT}/usr/share/doc/espressomail/README.md"

    INSTALLED_SIZE=$(du -sb "${PKG_ROOT}" | cut -f1)
    BUILD_DATE=$(date +%s)

    cat <<EOF > "${PKG_ROOT}/.PKGINFO"
pkgname = espressomail
pkgbase = espressomail
pkgver = ${VERSION}-1
pkgdesc = A modern, multi-account terminal email client built on Espresso TUI
url = https://github.com/kimusan/EspressoMail
builddate = ${BUILD_DATE}
packager = Kim Schulz <kim@schulz.dk>
size = ${INSTALLED_SIZE}
arch = x86_64
license = MIT
depend = glibc
optdepend = chafa: high-resolution inline terminal image rendering
optdepend = gnupg: PGP/GPG email signing and encryption
provides = espressomail
conflict = espressomail
EOF

    (
        cd "${PKG_ROOT}"
        tar --owner=0 --group=0 --numeric-owner -cf - .PKGINFO usr | zstd -c -T0 > "${OUTPUT_DIR}/espressomail-${VERSION}-1-x86_64.pkg.tar.zst"
    )
fi

echo "Successfully built Arch Linux package in: ${OUTPUT_DIR}/"
