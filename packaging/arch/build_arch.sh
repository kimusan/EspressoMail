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
elif command -v docker &>/dev/null && ([ -n "${CI:-}" ] || [ -n "$(docker images -q archlinux:latest 2>/dev/null)" ]); then
    echo "Building Arch package using Docker archlinux:latest container..."
    chmod -R 777 "${BUILD_DIR}"
    docker run --rm \
        -v "${BUILD_DIR}:/pkg" \
        -w /pkg \
        archlinux:latest \
        bash -c "
            pacman -Syu --noconfirm base-devel binutils zstd > /dev/null 2>&1
            useradd -m builder
            chown -R builder:builder /pkg
            su builder -c 'makepkg -f --nodeps'
        "
    find "${BUILD_DIR}" -name "*.pkg.tar.*" -exec cp {} "${OUTPUT_DIR}/" \;
else
    echo "Warning: Neither 'makepkg' nor 'docker' is available. Creating Arch package via bsdtar/tar..."
    # Fallback to tar+zstd package assembly
    (
        cd "${BUILD_DIR}"
        PKG_ROOT="${BUILD_DIR}/pkg"
        mkdir -p "${PKG_ROOT}/usr/bin"
        mkdir -p "${PKG_ROOT}/usr/share/applications"
        mkdir -p "${PKG_ROOT}/usr/share/icons/hicolor/scalable/apps"
        mkdir -p "${PKG_ROOT}/usr/share/licenses/espressomail"
        mkdir -p "${PKG_ROOT}/usr/share/doc/espressomail"

        install -m 755 espressomail "${PKG_ROOT}/usr/bin/espressomail"
        install -m 644 espressomail.desktop "${PKG_ROOT}/usr/share/applications/espressomail.desktop"
        install -m 644 espressomail.svg "${PKG_ROOT}/usr/share/icons/hicolor/scalable/apps/espressomail.svg"
        install -m 644 LICENSE "${PKG_ROOT}/usr/share/licenses/espressomail/LICENSE"
        install -m 644 README.md "${PKG_ROOT}/usr/share/doc/espressomail/README.md"

        cat <<EOF > "${PKG_ROOT}/.PKGINFO"
pkgname = espressomail
pkgver = ${VERSION}-1
pkgdesc = A modern, multi-account terminal email client built on Espresso TUI
url = https://github.com/kimusan/EspressoMail
builddate = $(date +%s)
packager = Kim Schulz <kim@schulz.dk>
size = $(du -sb "${PKG_ROOT}" | cut -f1)
arch = x86_64
license = MIT
depend = glibc
optdepend = chafa: high-resolution inline terminal image rendering
optdepend = gnupg: PGP/GPG email signing and encryption
provides = espressomail
conflict = espressomail
EOF
        cd "${PKG_ROOT}"
        tar -c -f - .PKGINFO usr | zstd -c -T0 > "${OUTPUT_DIR}/espressomail-${VERSION}-1-x86_64.pkg.tar.zst"
    )
fi

echo "Successfully built Arch Linux package in: ${OUTPUT_DIR}/"
