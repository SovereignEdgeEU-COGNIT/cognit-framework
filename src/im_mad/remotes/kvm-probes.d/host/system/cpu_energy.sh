#!/bin/bash

# -------------------------------------------------------------------------- #
# OpenNebula host/system probe: CPU_ENERGY                                   #
#                                                                            #
# Computes CPU_ENERGY breakpoints from a hardware specs database (TDP,       #
# physical cores, logical cores) and current power monitoring data.          #
# Output is stored as a HOST/TEMPLATE attribute for the DRS optimizer.       #
# -------------------------------------------------------------------------- #

STDIN=$(cat -)

PYTHON_PATH=/var/tmp/one/im/lib/python

PYTHON_VERSION=$(python3 --version 2>/dev/null | cut -d ' ' -f2)

MAJOR=$(echo "$PYTHON_VERSION" | cut -d. -f1)
MINOR=$(echo "$PYTHON_VERSION" | cut -d. -f2)

if [[ "$MAJOR" -lt 3 ]] || [[ "$MAJOR" -eq 3 && "$MINOR" -lt 9 ]]; then
    if command -v python3.9 &>/dev/null; then
        PYTHON=python3.9
    else
        exit 0
    fi
else
    PYTHON=python3
fi

HOST_ID=$(echo "${STDIN}" | xmllint --xpath 'string(//HOST_ID)' - 2>/dev/null)

$PYTHON "$PYTHON_PATH/cpu_energy.py" "$HOST_ID"
