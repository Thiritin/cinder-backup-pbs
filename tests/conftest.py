import os
import sys
from pathlib import Path

# Make the package importable without `pip install -e .`
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def pytest_configure(config):
    # Default oslo.config to known values so opts.* attributes resolve
    # without anyone having read a cinder.conf.
    os.environ.setdefault("OS_TEST_PBS_REPOSITORY", "test@pbs!ci@localhost:store")
    os.environ.setdefault("OS_TEST_PBS_FINGERPRINT", "aa:bb:cc")
