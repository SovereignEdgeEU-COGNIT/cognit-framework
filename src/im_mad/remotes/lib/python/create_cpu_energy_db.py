#!/usr/bin/env python3
"""
Initialize the CPU energy hardware specs SQLite database.

This database is used by the cpu_energy probe to compute CPU_ENERGY
breakpoints for each host based on its CPU model.

Usage:
    python3 create_cpu_energy_db.py [--db-path PATH]

The database is created at the OpenNebula remotes config directory by default.
After creation, run 'onehost sync' to distribute it to all compute nodes.
"""

import argparse
import os
import sqlite3

DEFAULT_DB_PATH = "/var/lib/one/remotes/etc/im/kvm-probes.d/cpu_energy.db"

SERVERS = [
    ("Intel(R) Xeon(R) E-2386G CPU @ 3.50GHz", 6, 12, 95.0),
    ("AMD Ryzen 5 5600X 6-Core Processor", 6, 12, 65.0),
    ("AMD Ryzen 7 9700X 8-Core Processor", 8, 16, 65.0),
]


def create_database(db_path):
    os.makedirs(os.path.dirname(db_path), exist_ok=True)

    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS servers (
            model_name    TEXT PRIMARY KEY,
            physical_cpus INTEGER NOT NULL,
            cores         INTEGER NOT NULL,
            tdp           REAL    NOT NULL
        )
        """
    )

    cur.execute("DELETE FROM servers")

    cur.executemany(
        "INSERT INTO servers (model_name, physical_cpus, cores, tdp) VALUES (?, ?, ?, ?)",
        SERVERS,
    )

    conn.commit()
    conn.close()

    print(f"Database created at: {db_path}")
    print(f"Inserted {len(SERVERS)} server model(s):")
    for model, pcpus, cores, tdp in SERVERS:
        print(f"  {model}  |  physical_cpus={pcpus}  |  cores={cores}  |  tdp={tdp}W")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Create CPU energy hardware specs database")
    parser.add_argument("--db-path", default=DEFAULT_DB_PATH, help="Path to the SQLite database")
    args = parser.parse_args()
    create_database(args.db_path)
