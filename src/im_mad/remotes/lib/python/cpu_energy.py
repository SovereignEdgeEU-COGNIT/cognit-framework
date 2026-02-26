#!/usr/bin/env python3
"""
OpenNebula host/system probe: compute CPU_ENERGY breakpoints.

Reads the host's CPU model from /proc/cpuinfo, looks up hardware specs
(physical cores, logical cores, TDP) from a SQLite database, reads the
latest power and CPU usage from the monitoring database, and outputs a
piecewise-linear CPU_ENERGY profile as a host template attribute.

The profile has the form:
    CPU_ENERGY="x1,y1;x2,y2;x3,y3"

Where x = CPU count (TOTALCPU / 100) and y = power in Watts.
Always exactly 3 breakpoints:
  - (0, idle_power)            baseline at zero CPU
  - (physical_cores, TDP)      full physical core load
  - (logical_cores, TDP)       full thread capacity (flat)
"""

import os
import re
import sqlite3
import sys

SPECS_DB_PATH = "/var/tmp/one/etc/im/kvm-probes.d/cpu_energy.db"
MONITOR_DB_PATH = "/var/tmp/one_db/host.db"


def get_model_name():
    """Read CPU model name from /proc/cpuinfo."""
    try:
        with open("/proc/cpuinfo", "r", encoding="utf-8") as f:
            for line in f:
                for key in ("model name", "cpu"):
                    if re.match(rf"^{key}\s*:", line):
                        return line.split(":", 1)[1].strip()
    except OSError:
        pass
    return None


def get_specs(model_name):
    """Query the hardware specs database for a given CPU model."""
    if not os.path.exists(SPECS_DB_PATH):
        return None
    try:
        conn = sqlite3.connect(SPECS_DB_PATH)
        cur = conn.cursor()
        cur.execute(
            "SELECT physical_cpus, cores, tdp FROM servers WHERE model_name = ?",
            (model_name,),
        )
        row = cur.fetchone()
        conn.close()
        if row:
            return {"physical_cpus": row[0], "cores": row[1], "tdp": row[2]}
    except sqlite3.Error as e:
        print(f"Error reading specs DB: {e}", file=sys.stderr)
    return None


def get_latest_monitoring(host_id):
    """Read the latest power and usedcpu values from the host monitoring DB."""
    if not os.path.exists(MONITOR_DB_PATH):
        return None, None

    if not str(host_id).isdigit():
        return None, None

    try:
        conn = sqlite3.connect(MONITOR_DB_PATH)
        cur = conn.cursor()

        power = None
        cur.execute(
            f"SELECT VALUE FROM host_{host_id}_power_monitoring "
            "ORDER BY TIMESTAMP DESC LIMIT 1"
        )
        row = cur.fetchone()
        if row and row[0] is not None:
            power = float(row[0])

        usedcpu = None
        cur.execute(
            f"SELECT VALUE FROM host_{host_id}_usedcpu_monitoring "
            "ORDER BY TIMESTAMP DESC LIMIT 1"
        )
        row = cur.fetchone()
        if row and row[0] is not None:
            usedcpu = float(row[0])

        conn.close()
        return power, usedcpu
    except sqlite3.Error as e:
        print(f"Error reading monitoring DB: {e}", file=sys.stderr)
    return None, None


def compute_breakpoints(physical_cpus, cores, tdp, power_uw, usedcpu):
    """
    Compute CPU_ENERGY breakpoints.

    Returns a list of (cpu, power_watts) tuples.
    cpu is in units of TOTALCPU/100 (i.e. logical CPU count).
    power is in Watts.
    """
    tdp_uw = tdp * 1e6

    # Estimate idle power (power at 0 CPU) from current measurements.
    if power_uw is not None and usedcpu is not None and usedcpu > 0:
        physical_cpus_units = physical_cpus * 100
        if usedcpu < physical_cpus_units:
            # slope = watts gained per CPU unit between measured point and TDP ceiling
            slope = (tdp_uw - power_uw) / (physical_cpus_units - usedcpu)
            # idle = current power minus what the active CPUs are adding
            idle_power_uw = power_uw - slope * usedcpu
            idle_power_uw = max(0.0, idle_power_uw)
        else:
            idle_power_uw = 0.0
    else:
        # Fallback when no monitoring data: assume idle is 10% of TDP.
        idle_power_uw = tdp_uw * 0.1

    idle_watts = idle_power_uw / 1e6
    tdp_watts = tdp

    # Always 3 points: (0, baseline), (physical_cores, TDP), (logical_cores, TDP).
    breakpoints = [
        (0, idle_watts),
        (physical_cpus, tdp_watts),
        (cores, tdp_watts),
    ]

    return breakpoints


def main():
    host_id = None
    if len(sys.argv) > 1:
        host_id = sys.argv[1]

    model_name = get_model_name()
    if not model_name:
        sys.exit(0)

    specs = get_specs(model_name)
    if not specs:
        print(f"No entry in cpu_energy.db for model: {model_name}", file=sys.stderr)
        sys.exit(0)

    physical_cpus = specs["physical_cpus"]
    cores = specs["cores"]
    tdp = specs["tdp"]

    power_uw, usedcpu = None, None
    if host_id is not None:
        power_uw, usedcpu = get_latest_monitoring(host_id)

    breakpoints = compute_breakpoints(physical_cpus, cores, tdp, power_uw, usedcpu)

    parts = [f"{cpu},{power}" for cpu, power in breakpoints]
    print(f'CPU_ENERGY="{";".join(parts)}"')


if __name__ == "__main__":
    main()
