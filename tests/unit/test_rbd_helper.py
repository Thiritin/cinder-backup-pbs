import subprocess
from unittest.mock import MagicMock, patch

import pytest

from cinder_backup_pbs.exceptions import RbdHelperError
from cinder_backup_pbs.rbd_helper import RbdHelper


@pytest.fixture
def rbd():
    return RbdHelper(pool="cinder-volumes", user="cinder")


def _cp(rc=0, stdout=b"", stderr=b""):
    return subprocess.CompletedProcess(args=[], returncode=rc, stdout=stdout, stderr=stderr)


def test_nbd_map_returns_dev_path(rbd):
    with patch("cinder_backup_pbs.rbd_helper.subprocess.run",
               return_value=_cp(stdout=b"/dev/nbd0\n")):
        dev = rbd.nbd_map("abc-123")
    assert dev == "/dev/nbd0"


def test_nbd_map_rejects_unexpected_stdout(rbd):
    with patch("cinder_backup_pbs.rbd_helper.subprocess.run",
               return_value=_cp(stdout=b"garbage\n")):
        with pytest.raises(RbdHelperError):
            rbd.nbd_map("abc-123")


def test_nbd_map_readonly_flag(rbd):
    fake = MagicMock(return_value=_cp(stdout=b"/dev/nbd0\n"))
    with patch("cinder_backup_pbs.rbd_helper.subprocess.run", fake):
        rbd.nbd_map("abc-123@snap", read_only=True)
    cmd = fake.call_args[0][0]
    assert "--read-only" in cmd
    assert "cinder-volumes/abc-123@snap" in cmd


def test_staged_snapshot_cleans_up_on_success(rbd):
    calls = []

    def record(cmd, *a, **kw):
        calls.append(tuple(cmd))
        return _cp()

    with patch("cinder_backup_pbs.rbd_helper.subprocess.run", side_effect=record):
        with rbd.staged_snapshot("img-1", "stage-x"):
            pass

    # Expect: create, protect, unprotect, rm — in order.
    op_seq = [c[3] if len(c) > 3 else c for c in calls]
    # Extract the snap action verb (positions vary by argv layout)
    verbs = []
    for c in calls:
        if "snap" in c:
            i = c.index("snap")
            verbs.append(c[i + 1])
    assert verbs == ["create", "protect", "unprotect", "rm"]


def test_staged_snapshot_cleans_up_on_body_exception(rbd):
    calls = []

    def record(cmd, *a, **kw):
        calls.append(tuple(cmd))
        return _cp()

    with patch("cinder_backup_pbs.rbd_helper.subprocess.run", side_effect=record):
        with pytest.raises(RuntimeError):
            with rbd.staged_snapshot("img-1", "stage-x"):
                raise RuntimeError("boom")

    verbs = []
    for c in calls:
        if "snap" in c:
            i = c.index("snap")
            verbs.append(c[i + 1])
    # Unprotect + rm should still have run.
    assert "unprotect" in verbs
    assert "rm" in verbs


def test_mapped_unmaps_on_exit(rbd):
    seq = [_cp(stdout=b"/dev/nbd2\n"), _cp()]  # map, unmap

    def step(*a, **kw):
        return seq.pop(0)

    with patch("cinder_backup_pbs.rbd_helper.subprocess.run", side_effect=step):
        with rbd.mapped("img-1") as dev:
            assert dev == "/dev/nbd2"
    assert seq == []  # both calls consumed


def test_mapped_unmaps_on_body_exception(rbd):
    seq = [_cp(stdout=b"/dev/nbd2\n"), _cp()]

    def step(*a, **kw):
        return seq.pop(0)

    with patch("cinder_backup_pbs.rbd_helper.subprocess.run", side_effect=step):
        with pytest.raises(ValueError):
            with rbd.mapped("img-1"):
                raise ValueError("body")
    assert seq == []
