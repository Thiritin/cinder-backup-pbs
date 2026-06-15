"""Gate the integration suite on a live PBS + Ceph environment.

Every test here is skipped unless all PBS_IT_* env vars are set, so the
directory is safe to leave in a default `pytest` run.
"""
import os

import pytest

_REQUIRED = (
    "PBS_IT_REPOSITORY",
    "PBS_IT_FINGERPRINT",
    "PBS_IT_PASSWORD_FILE",
    "PBS_IT_NAMESPACE",
    "PBS_IT_RBD_POOL",
    "PBS_IT_RBD_USER",
)


def _missing():
    return [name for name in _REQUIRED if not os.environ.get(name)]


@pytest.fixture(scope="session")
def it_env():
    missing = _missing()
    if missing:
        pytest.skip(f"integration env not set: {', '.join(missing)}")
    return {
        "repository": os.environ["PBS_IT_REPOSITORY"],
        "fingerprint": os.environ["PBS_IT_FINGERPRINT"],
        "password_file": os.environ["PBS_IT_PASSWORD_FILE"],
        "namespace": os.environ["PBS_IT_NAMESPACE"],
        "rbd_pool": os.environ["PBS_IT_RBD_POOL"],
        "rbd_user": os.environ["PBS_IT_RBD_USER"],
        "rbd_keyring": os.environ.get("PBS_IT_RBD_KEYRING"),
    }
