#!/usr/bin/env bash
set -euo pipefail

if [[ -t 1 && -z ${NO_COLOR+x} ]]; then
    color_match=$'\033[32m'
    color_mismatch=$'\033[31m'
    color_not_found=$'\033[33m'
    color_reset=$'\033[0m'
else
    color_match=''
    color_mismatch=''
    color_not_found=''
    color_reset=''
fi

print_result() {
    local iso=$1 release=$2 checksum=$3 expected=$4 status=$5
    local status_color

    case "$status" in
        MATCH) status_color=$color_match ;;
        MISMATCH) status_color=$color_mismatch ;;
        *) status_color=$color_not_found ;;
    esac

    printf 'ISO=%s RELEASE=%s LOCAL_CRC=%s EXPECTED_CRC=%s STATUS=%b%s%b\n' \
        "$iso" "$release" "$checksum" "$expected" "$status_color" "$status" "$color_reset"
}

if [[ $# -ne 1 ]]; then
    echo "Usage: $0 DIRECTORY" >&2
    exit 2
fi

folder=$1
if [[ ! -d "$folder" ]]; then
    echo "Directory not found: $folder" >&2
    exit 1
fi

mapfile -t files < <(find "$folder" -type f -iname '*.iso' -print | sort)
if [[ ${#files[@]} -eq 0 ]]; then
    echo "No .iso files found in: $folder" >&2
    exit 1
fi

for iso in "${files[@]}"; do
    if ! checksum=$(python3 - "$iso" <<'PY'
import sys
import zlib
import os

crc = 0
path = sys.argv[1]
size = os.path.getsize(path)
read = 0
with open(path, "rb") as file:
    while chunk := file.read(1024 * 1024):
        crc = zlib.crc32(chunk, crc)
        read += len(chunk)
        percent = read * 100 // size if size else 100
        width = 40
        filled = percent * width // 100
        bar = "#" * filled + "-" * (width - filled)
        print(f"\r{os.path.basename(path)} [{bar}] {percent:3d}%", end="", file=sys.stderr, flush=True)

if size == 0:
    print(f"\r{os.path.basename(path)} [{'#' * 40}] 100%", end="", file=sys.stderr, flush=True)
print(file=sys.stderr)

print(f"{crc & 0xffffffff:08x}")
PY
    ); then
        print_result "$iso" UNAVAILABLE UNAVAILABLE UNAVAILABLE 'NOT FOUND'
        continue
    fi

    if ! local_size=$(stat -c '%s' -- "$iso"); then
        print_result "$iso" UNAVAILABLE "$checksum" UNAVAILABLE 'NOT FOUND'
        continue
    fi

    release=$(basename "$(dirname "$iso")")
    encoded_release=$(python3 -c 'import sys; from urllib.parse import quote; print(quote(sys.argv[1], safe=""))' "$release")
    url="https://api.srrdb.com/v1/details/$encoded_release"

    if ! response=$(curl --fail --silent --show-error --location --max-time 60 "$url"); then
        printf 'API error for %s; expected CRC unavailable\n' "$iso" >&2
        print_result "$iso" "$release" "$checksum" UNAVAILABLE 'NOT FOUND'
        continue
    fi

    if ! expected=$(python3 -c '
import json
import os
import re
import sys

iso_name = os.path.basename(sys.argv[1]).casefold()
local_size = int(sys.argv[2])
data = json.load(sys.stdin)

for key in ("files", "archived-files"):
    for entry in data.get(key, []) or []:
        name = entry.get("name")
        if isinstance(name, str) and re.split(r"[\\/]", name)[-1].casefold() == iso_name:
            crc = entry.get("crc")
            if isinstance(crc, str) and re.fullmatch(r"[0-9a-fA-F]{8}", crc):
                print(crc.lower())
                raise SystemExit(0)

size_matches = []
for entry in data.get("archived-files", []) or []:
    name = entry.get("name")
    entry_size = entry.get("size")
    if (isinstance(name, str) and name.casefold().endswith(".iso") and
            isinstance(entry_size, (int, float)) and entry_size == local_size):
        size_matches.append(entry)

if len(size_matches) == 1:
    crc = size_matches[0].get("crc")
    if isinstance(crc, str) and re.fullmatch(r"[0-9a-fA-F]{8}", crc):
        print(crc.lower())
        raise SystemExit(0)

print("UNAVAILABLE")
' "$iso" "$local_size" <<< "$response"
    ); then
        printf 'JSON error for %s; expected CRC unavailable\n' "$iso" >&2
        expected=UNAVAILABLE
    fi

    if [[ "$expected" == "UNAVAILABLE" ]]; then
        status='NOT FOUND'
    elif [[ "${checksum,,}" == "${expected,,}" ]]; then
        status='MATCH'
    else
        status='MISMATCH'
    fi

    print_result "$iso" "$release" "$checksum" "$expected" "$status"
done
