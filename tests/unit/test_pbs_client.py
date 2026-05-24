import subprocess
from unittest.mock import MagicMock, patch

import pytest

from cinder_backup_pbs.exceptions import PbsBackupError
from cinder_backup_pbs.pbs_client import PbsClient


@pytest.fixture
def client(tmp_path):
    pwfile = tmp_path / "tok"
    pwfile.write_text("supersecret\n")
    return PbsClient(
        repository="test@pbs!ci@host:store",
        fingerprint="aa:bb:cc",
        password_file=str(pwfile),
        tmpdir="/pbs-tmp",
        cache_dir="/pbs-tmp/cache",
    )


def _fake_run(rc=0, stderr=b"", stdout=b""):
    cp = subprocess.CompletedProcess(args=[], returncode=rc, stdout=stdout, stderr=stderr)
    return MagicMock(return_value=cp)


def test_env_sets_required_vars(client):
    env = client._env()
    assert env["PBS_REPOSITORY"] == "test@pbs!ci@host:store"
    assert env["PBS_FINGERPRINT"] == "aa:bb:cc"
    assert env["PBS_PASSWORD"] == "supersecret"
    assert env["TMPDIR"] == "/pbs-tmp"
    assert env["XDG_CACHE_HOME"] == "/pbs-tmp/cache"


def test_backup_image_returns_iso_snapshot_path(client):
    with patch("cinder_backup_pbs.pbs_client.subprocess.run", _fake_run()):
        snap = client.backup_image(
            source_device="/dev/nbd0",
            namespace="openstack/proj-1",
            backup_id="vol-abc",
            backup_time=1779646459,  # 2026-05-24T18:14:19Z
        )
    assert snap == "vm/vol-abc/2026-05-24T18:14:19Z"


def test_backup_image_argv(client):
    fake = _fake_run()
    with patch("cinder_backup_pbs.pbs_client.subprocess.run", fake):
        client.backup_image(
            source_device="/dev/nbd0",
            namespace="openstack/proj-1",
            backup_id="vol-abc",
            backup_time=1779646459,
        )
    args, kwargs = fake.call_args
    cmd = args[0]
    assert cmd[0] == PbsClient.BIN
    assert cmd[1] == "backup"
    assert "vm.img:/dev/nbd0" in cmd
    assert "openstack/proj-1" in cmd
    assert "vol-abc" in cmd
    assert "1779646459" in cmd


def test_non_zero_rc_raises(client):
    fake = _fake_run(rc=2, stderr=b"boom")
    with patch("cinder_backup_pbs.pbs_client.subprocess.run", fake):
        with pytest.raises(PbsBackupError) as exc:
            client.backup_image(
                source_device="/dev/nbd0",
                namespace="ns",
                backup_id="vol-x",
                backup_time=1,
            )
    assert "rc=2" in str(exc.value)
    assert "boom" in str(exc.value)


def test_forget_swallows_not_found(client):
    fake = _fake_run(rc=2, stderr=b"removing backup snapshot ... No such file or directory")
    with patch("cinder_backup_pbs.pbs_client.subprocess.run", fake):
        # Should NOT raise
        client.forget("vm/vol-x/2026-01-01T00:00:00Z", "openstack/proj-1")


def test_forget_raises_other_errors(client):
    fake = _fake_run(rc=2, stderr=b"permission denied")
    with patch("cinder_backup_pbs.pbs_client.subprocess.run", fake):
        with pytest.raises(PbsBackupError):
            client.forget("vm/vol-x/2026-01-01T00:00:00Z", "openstack/proj-1")


def test_ensure_namespace_swallows_already_exists(client):
    fake = _fake_run(rc=2, stderr=b"namespace 'openstack/proj-1' already exists")
    with patch("cinder_backup_pbs.pbs_client.subprocess.run", fake):
        client.ensure_namespace("openstack/proj-1")


def test_timeout_raises(client):
    def raiser(*a, **kw):
        raise subprocess.TimeoutExpired(cmd="x", timeout=1)

    with patch("cinder_backup_pbs.pbs_client.subprocess.run", side_effect=raiser):
        with pytest.raises(PbsBackupError) as exc:
            client.backup_image("/dev/nbd0", "ns", "vol", 1, timeout=1)
    assert "timed out" in str(exc.value)


def test_restore_uses_stdout_dash(client):
    fake = _fake_run()
    sink = MagicMock()
    with patch("cinder_backup_pbs.pbs_client.subprocess.run", fake):
        client.restore_image_to_stdout(
            snapshot_path="vm/vol-x/2026-01-01T00:00:00Z",
            namespace="openstack/proj-1",
            target_stdout=sink,
        )
    args, _ = fake.call_args
    cmd = args[0]
    assert cmd[1] == "restore"
    assert "-" in cmd  # stdout target
    assert "vm.img" in cmd
