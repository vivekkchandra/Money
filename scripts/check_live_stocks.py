"""Read-only acceptance selection for the qualified Trading 212 GBX stock universe.

The original command remains a compatibility entry point; neither asserts ISA
eligibility. Both enforce the same manifest, rights and freshness requirements.
"""

import runpy
from pathlib import Path

if __name__ == "__main__":
    runpy.run_path(str(Path(__file__).with_name("check_live_isa.py")), run_name="__main__")
