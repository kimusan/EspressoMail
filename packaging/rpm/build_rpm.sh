#!/usr/bin/env bash
set -euo pipefail

# Build RPM (.rpm) package for EspressoMail
# Usage: ./build_rpm.sh <version> <binary_path> <output_dir>

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

if ! command -v rpmbuild &>/dev/null; then
    echo "Error: 'rpmbuild' is required but not installed." >&2
    exit 1
fi

mkdir -p "${OUTPUT_DIR}"
RPM_DIR="$(mktemp -d /tmp/espressomail-rpm.XXXXXX)"
trap 'rm -rf "${RPM_DIR}"' EXIT

mkdir -p "${RPM_DIR}/"{BUILD,RPMS,SOURCES,SPECS,SRPMS,tmp,rpmdb}

# Copy sources for RPM
cp "${BINARY_PATH}" "${RPM_DIR}/SOURCES/espressomail"
cp "${REPO_ROOT}/packaging/espressomail.desktop" "${RPM_DIR}/SOURCES/espressomail.desktop"
cp "${REPO_ROOT}/packaging/icons/espressomail.svg" "${RPM_DIR}/SOURCES/espressomail.svg"
cp "${REPO_ROOT}/README.md" "${RPM_DIR}/SOURCES/README.md"
cp "${REPO_ROOT}/LICENSE" "${RPM_DIR}/SOURCES/LICENSE"
cp "${SCRIPT_DIR}/espressomail.spec" "${RPM_DIR}/SPECS/espressomail.spec"

rpmbuild -bb \
    --define "_topdir ${RPM_DIR}" \
    --define "_tmppath ${RPM_DIR}/tmp" \
    --define "_dbpath ${RPM_DIR}/rpmdb" \
    --define "pkg_version ${VERSION}" \
    "${RPM_DIR}/SPECS/espressomail.spec"

find "${RPM_DIR}/RPMS" -name "*.rpm" -exec cp {} "${OUTPUT_DIR}/" \;
echo "Successfully built RPM package(s) in: ${OUTPUT_DIR}/"
