"""Cinder backup driver that targets a Proxmox Backup Server.

Flow per backup:

1. Cinder calls ``backup(backup, volume_file, backup_metadata=False)``.
   We ignore ``volume_file``: it is opaque (could be /dev/loopX, an
   open file, an iSCSI device) and would not give us a crash-consistent
   point-in-time. We talk to RBD directly.
2. Create + protect an RBD snapshot of the source volume.
3. Map that snapshot read-only via rbd-nbd as ``/dev/nbdN``.
4. Spawn ``proxmox-backup-client backup vm.img:/dev/nbdN ...``.
5. Unmap, unprotect, remove the staging snapshot.
6. Persist the PBS snapshot path in ``backup.service_metadata`` so
   restore/delete can find it back.

Flow per restore:

1. Cinder calls ``restore(backup, volume_id, volume_file)``.
2. Map the target volume read-write via rbd-nbd.
3. Spawn ``proxmox-backup-client restore <snap> vm.img -`` piped into
   ``dd of=/dev/nbdN`` (pbc refuses to write directly to existing block
   devices: it uses O_CREAT|O_EXCL and gets EEXIST).
4. Unmap.

Flow per delete:

1. Read snapshot path + namespace from ``backup.service_metadata``.
2. ``proxmox-backup-client snapshot forget <path> --ns <ns>``.
3. PBS chunk reclamation happens later via a separate scheduled
   ``garbage-collect`` on the PBS server. Not driver's job.
"""
from __future__ import annotations

import json
import subprocess
import uuid
from typing import Any

from oslo_config import cfg
from oslo_log import log as logging

try:
    # Real environment: cinder is on the python path.
    from cinder.backup import driver as backup_driver
except ImportError:  # pragma: no cover - unit tests stub this
    backup_driver = None  # type: ignore[assignment]

from cinder_backup_pbs import opts
from cinder_backup_pbs.pbs_client import PbsClient
from cinder_backup_pbs.rbd_helper import RbdHelper

LOG = logging.getLogger(__name__)
CONF = cfg.CONF
opts.register_opts(CONF)


_BASE_CLS: Any = (
    backup_driver.BackupDriver if backup_driver is not None else object
)


class PbsBackupDriver(_BASE_CLS):
    """Stores cinder volume backups in a Proxmox Backup Server."""

    def __init__(self, context, db=None):
        # cinder's BackupDriver.__init__ takes only `context`. The legacy
        # `db` parameter is kept on our signature for callers that still
        # pass it but it's ignored.
        del db
        if backup_driver is not None:
            super().__init__(context)
        self.cfg = CONF.pbs_backup
        self.pbs = PbsClient(
            repository=self.cfg.repository,
            fingerprint=self.cfg.fingerprint,
            password_file=self.cfg.password_file,
            tmpdir=self.cfg.pbs_tmpdir,
            cache_dir=self.cfg.pbs_cache_dir,
        )
        self.rbd = RbdHelper(pool=self.cfg.rbd_pool, user=self.cfg.rbd_user)

    # -- helpers --------------------------------------------------------

    def _namespace(self, project_id: str) -> str:
        return f"{self.cfg.namespace_prefix}/{project_id}"

    @staticmethod
    def _backup_id(volume_id: str) -> str:
        # PBS backup-id is alphanumeric + dash/underscore. UUID dashes are OK.
        return f"vol-{volume_id}"

    @staticmethod
    def _stage_snap(backup_id: str) -> str:
        # Short-lived RBD snapshot name. Random suffix avoids collisions
        # when a previous run crashed before cleanup.
        return f"pbs-stage-{backup_id}-{uuid.uuid4().hex[:8]}"

    def _rbd_image(self, volume_id: str) -> str:
        """In this cluster's cinder config the RBD image name == bare UUID.

        If a deployment uses the upstream default ``volume-<uuid>`` prefix
        instead, override this method via subclass or extend with a config
        option.
        """
        return volume_id

    def _encode_meta(self, snapshot_path: str, namespace: str) -> str:
        return json.dumps(
            {
                "snapshot_path": snapshot_path,
                "namespace": namespace,
                "repository": self.cfg.repository,
            }
        )

    @staticmethod
    def _decode_meta(raw: str) -> dict:
        return json.loads(raw)

    # -- backup ---------------------------------------------------------

    def backup(self, backup, volume_file, backup_metadata=False):
        del volume_file, backup_metadata  # see module docstring

        volume_id = backup.volume_id
        project_id = backup.project_id
        rbd_image = self._rbd_image(volume_id)
        backup_id = self._backup_id(volume_id)
        ns = self._namespace(project_id)
        stage_snap = self._stage_snap(backup_id)
        backup_time = int(backup.created_at.timestamp())

        LOG.info(
            "pbs backup start: volume=%s project=%s ns=%s backup_id=%s",
            volume_id, project_id, ns, backup_id,
        )

        self.pbs.ensure_namespace(ns)

        with self.rbd.staged_snapshot(rbd_image, stage_snap):
            with self.rbd.mapped(f"{rbd_image}@{stage_snap}", read_only=True) as dev:
                snapshot_path = self.pbs.backup_image(
                    source_device=dev,
                    namespace=ns,
                    backup_id=backup_id,
                    backup_time=backup_time,
                    timeout=self.cfg.backup_timeout,
                )

        LOG.info("pbs backup done: snapshot=%s ns=%s", snapshot_path, ns)
        return {
            "service_metadata": self._encode_meta(snapshot_path, ns),
            # PBS deduplicates implicitly across the whole namespace.
            # We never produce a child backup that depends on a parent
            # backup row in the cinder DB.
            "parent_id": None,
        }

    # -- restore --------------------------------------------------------

    def restore(self, backup, volume_id, volume_file, volume_is_new=True):
        del volume_file, volume_is_new  # we go straight to RBD

        meta = self._decode_meta(backup.service_metadata or "{}")
        if not meta.get("snapshot_path"):
            from cinder_backup_pbs.exceptions import PbsBackupError
            raise PbsBackupError(
                f"backup {backup.id} has no PBS snapshot_path in service_metadata"
            )

        rbd_image = self._rbd_image(volume_id)
        LOG.info(
            "pbs restore start: backup=%s -> volume=%s snapshot=%s",
            backup.id, volume_id, meta["snapshot_path"],
        )

        with self.rbd.mapped(rbd_image, read_only=False) as dev:
            # pbc refuses to overwrite an existing target file (O_CREAT|O_EXCL).
            # Pipe its stdout into dd which is happy to write to a block dev.
            dd = subprocess.Popen(
                ["dd", f"of={dev}", "bs=4M", "conv=fsync", "status=none"],
                stdin=subprocess.PIPE,
            )
            try:
                self.pbs.restore_image_to_stdout(
                    snapshot_path=meta["snapshot_path"],
                    namespace=meta["namespace"],
                    target_stdout=dd.stdin,
                    timeout=self.cfg.restore_timeout,
                )
            finally:
                if dd.stdin and not dd.stdin.closed:
                    dd.stdin.close()
            rc = dd.wait(timeout=300)
            if rc != 0:
                from cinder_backup_pbs.exceptions import PbsBackupError
                raise PbsBackupError(f"dd failed with rc={rc}")

        LOG.info("pbs restore done: volume=%s", volume_id)

    # -- delete ---------------------------------------------------------

    def delete_backup(self, backup):
        if not backup.service_metadata:
            LOG.warning(
                "delete_backup(%s): no service_metadata, nothing to forget",
                backup.id,
            )
            return
        meta = self._decode_meta(backup.service_metadata)
        self.pbs.forget(meta["snapshot_path"], meta["namespace"])
        LOG.info("pbs forget done: snapshot=%s", meta["snapshot_path"])

    # -- record import/export (for cinder backup migration) ------------

    def export_record(self, backup):
        return {"pbs_metadata": backup.service_metadata or ""}

    def import_record(self, backup, backup_record):
        backup.service_metadata = backup_record.get("pbs_metadata", "")
        # Caller persists via cinder DB layer; do not call backup.save() here.


def get_backup_driver(context):
    """Cinder loader entry point (legacy API). Modern cinder uses the
    setuptools entry point declared in pyproject.toml."""
    return PbsBackupDriver(context)
