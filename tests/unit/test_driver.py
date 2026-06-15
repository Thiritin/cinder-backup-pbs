"""Driver-level tests with PbsClient + RbdHelper mocked.

These run without cinder installed: ``driver.backup_driver`` falls back
to ``None`` and the driver inherits ``object``, which is fine for unit
tests that only call backup/restore/delete via the mocked deps.
"""
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from cinder_backup_pbs import driver as driver_mod
from cinder_backup_pbs.exceptions import PbsBackupError


@pytest.fixture
def cfg_setup():
    # Seed the oslo config group used by the driver
    from oslo_config import cfg
    CONF = cfg.CONF
    CONF.set_override("repository", "tok@h:store", group="pbs_backup")
    CONF.set_override("fingerprint", "aa:bb", group="pbs_backup")
    CONF.set_override("password_file", "/dev/null", group="pbs_backup")
    CONF.set_override("namespace_prefix", "openstack", group="pbs_backup")
    CONF.set_override("rbd_pool", "cinder-volumes", group="pbs_backup")
    CONF.set_override("rbd_user", "cinder", group="pbs_backup")
    CONF.set_override("pbs_tmpdir", "/pbs-tmp", group="pbs_backup")
    CONF.set_override("pbs_cache_dir", "/pbs-tmp/cache", group="pbs_backup")
    yield CONF
    CONF.clear_override("repository", group="pbs_backup")
    CONF.clear_override("fingerprint", group="pbs_backup")


@pytest.fixture
def drv(cfg_setup):
    d = driver_mod.PbsBackupDriver(context=None)
    d.pbs = MagicMock()
    d.rbd = MagicMock()
    # Stub the context managers
    d.rbd.staged_snapshot.return_value.__enter__ = MagicMock(return_value=None)
    d.rbd.staged_snapshot.return_value.__exit__ = MagicMock(return_value=False)
    d.rbd.mapped.return_value.__enter__ = MagicMock(return_value="/dev/nbd0")
    d.rbd.mapped.return_value.__exit__ = MagicMock(return_value=False)
    return d


def _fake_backup_row(volume_id="vol-uuid", project_id="proj-uuid",
                     created=datetime(2026, 5, 24, 18, 14, 19, tzinfo=timezone.utc)):
    return SimpleNamespace(
        id="bk-uuid",
        volume_id=volume_id,
        project_id=project_id,
        created_at=created,
        service_metadata=None,
    )


def test_backup_invokes_pbs_with_correct_namespace_and_id(drv):
    drv.pbs.backup_image.return_value = "vm/vol-vol-uuid/2026-05-24T18:14:19Z"
    backup = _fake_backup_row()

    out = drv.backup(backup, volume_file=None)

    drv.pbs.ensure_namespace.assert_called_once_with("openstack/proj-uuid")
    args, kwargs = drv.pbs.backup_image.call_args
    assert kwargs["namespace"] == "openstack/proj-uuid"
    assert kwargs["backup_id"] == "vol-vol-uuid"
    assert kwargs["backup_time"] == int(backup.created_at.timestamp())
    assert kwargs["source_device"] == "/dev/nbd0"

    assert "service_metadata" in out
    assert out["parent_id"] is None


def test_backup_persists_snapshot_path_in_metadata(drv):
    drv.pbs.backup_image.return_value = "vm/vol-vol-uuid/2026-05-24T18:14:19Z"
    backup = _fake_backup_row()

    out = drv.backup(backup, volume_file=None)

    import json
    meta = json.loads(out["service_metadata"])
    assert meta["snapshot_path"] == "vm/vol-vol-uuid/2026-05-24T18:14:19Z"
    assert meta["namespace"] == "openstack/proj-uuid"
    assert meta["repository"] == "tok@h:store"


def test_backup_uses_readonly_mapping(drv):
    drv.pbs.backup_image.return_value = "vm/vol-x/2026-01-01T00:00:00Z"
    backup = _fake_backup_row()
    drv.backup(backup, volume_file=None)
    args, kwargs = drv.rbd.mapped.call_args
    assert kwargs.get("read_only") is True


def test_delete_backup_calls_forget_with_stored_metadata(drv):
    backup = _fake_backup_row()
    backup.service_metadata = (
        '{"snapshot_path": "vm/vol-x/2026-01-01T00:00:00Z", '
        '"namespace": "openstack/proj-1", "repository": "tok@h:store"}'
    )
    drv.delete_backup(backup)
    drv.pbs.forget.assert_called_once_with(
        "vm/vol-x/2026-01-01T00:00:00Z", "openstack/proj-1"
    )


def test_delete_backup_no_metadata_is_noop(drv):
    backup = _fake_backup_row()
    backup.service_metadata = None
    drv.delete_backup(backup)
    drv.pbs.forget.assert_not_called()


def test_restore_pipes_pbc_stdout_into_dd(drv):
    backup = _fake_backup_row()
    backup.service_metadata = (
        '{"snapshot_path": "vm/vol-x/2026-01-01T00:00:00Z", '
        '"namespace": "openstack/proj-1", "repository": "tok@h:store"}'
    )

    dd_proc = MagicMock()
    dd_proc.stdin = MagicMock()
    dd_proc.stdin.closed = False
    dd_proc.wait.return_value = 0

    with patch("cinder_backup_pbs.driver.subprocess.Popen", return_value=dd_proc) as popen:
        drv.restore(backup, "vol-target", volume_file=None)

    # dd was invoked with the mapped device as its of=
    popen_args, _ = popen.call_args
    assert popen_args[0][0] == "dd"
    assert "of=/dev/nbd0" in popen_args[0]

    # pbc restore was given dd.stdin as the stdout sink
    args, kwargs = drv.pbs.restore_image_to_stdout.call_args
    assert kwargs["target_stdout"] is dd_proc.stdin


def test_restore_raises_when_metadata_missing_snapshot_path(drv):
    backup = _fake_backup_row()
    backup.service_metadata = "{}"
    with pytest.raises(PbsBackupError):
        drv.restore(backup, "vol-x", volume_file=None)


def test_restore_raises_when_dd_exits_nonzero(drv):
    backup = _fake_backup_row()
    backup.service_metadata = (
        '{"snapshot_path": "vm/vol-x/2026-01-01T00:00:00Z", '
        '"namespace": "openstack/proj-1", "repository": "tok@h:store"}'
    )

    dd_proc = MagicMock()
    dd_proc.stdin = MagicMock()
    dd_proc.stdin.closed = False
    dd_proc.wait.return_value = 1

    with patch("cinder_backup_pbs.driver.subprocess.Popen", return_value=dd_proc):
        with pytest.raises(PbsBackupError):
            drv.restore(backup, "vol-target", volume_file=None)


def test_check_for_setup_error_passes(drv, tmp_path):
    pw = tmp_path / "tok"
    pw.write_text("secret")
    drv.cfg = SimpleNamespace(
        repository="tok@h:store",
        fingerprint="aa:bb",
        password_file=str(pw),
        pbs_tmpdir=str(tmp_path),
    )
    with patch("cinder_backup_pbs.driver.os.path.exists", return_value=True):
        drv.check_for_setup_error()  # no raise


def test_check_for_setup_error_missing_repository(drv, tmp_path):
    drv.cfg = SimpleNamespace(
        repository=None,
        fingerprint="aa:bb",
        password_file=str(tmp_path / "tok"),
        pbs_tmpdir=str(tmp_path),
    )
    with pytest.raises(PbsBackupError):
        drv.check_for_setup_error()


def test_check_for_setup_error_binary_missing(drv, tmp_path):
    pw = tmp_path / "tok"
    pw.write_text("secret")
    drv.cfg = SimpleNamespace(
        repository="tok@h:store",
        fingerprint="aa:bb",
        password_file=str(pw),
        pbs_tmpdir=str(tmp_path),
    )
    with patch("cinder_backup_pbs.driver.os.path.exists", return_value=False):
        with pytest.raises(PbsBackupError):
            drv.check_for_setup_error()


def test_export_and_import_record_roundtrip(drv):
    backup = _fake_backup_row()
    backup.service_metadata = '{"snapshot_path": "x"}'
    exported = drv.export_record(backup)
    assert exported == {"pbs_metadata": '{"snapshot_path": "x"}'}

    new_backup = _fake_backup_row()
    new_backup.service_metadata = None
    drv.import_record(new_backup, exported)
    assert new_backup.service_metadata == '{"snapshot_path": "x"}'
