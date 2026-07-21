#!/bin/bash
set -euo pipefail
################################################################################
# Name:		perf-sbatch.sh
# Author:	Rodney Marable <rodney.marable@gmail.com>
# Created On:	May 8, 2019
# Last Changed:	May 11, 2019
# Purpose:	Wrapper for submitting multiple sbatch-Axb_random cluster jobs
################################################################################

shopt -s nullglob
_glob=(sbatch-Axb_random.[0-9]*)
shopt -u nullglob

if [[ ${#_glob[@]} -eq 0 ]]; then
    echo "No sbatch-Axb_random.[0-9]* files found — nothing to submit." >&2
    exit 0
fi

mapfile -t sbatch_files < <(printf '%s\n' "${_glob[@]}" | sort -t. -k2,2n)
for sbatchfile in "${sbatch_files[@]}"; do
    [[ -n "$sbatchfile" ]] || continue
    sbatch "$sbatchfile"
done
