# cinder-backup-pbs

Out-of-tree Cinder backup driver that stores deduplicated backups in a
Proxmox Backup Server (PBS) instance.

Cinder receives standard `openstack volume backup create/restore/delete`
API calls. The driver shells out to `proxmox-backup-client` (AGPLv3,
unmodified, process boundary) and writes chunked, deduplicated,
content-addressed backups to a PBS datastore.

## Status

Pre-production. Targets OpenStack 2026.1 with Ceph RBD volume backend.

## Why

Native Cinder backup drivers (S3, Swift, Ceph, NFS) do not deduplicate.
A weekly full + incremental scheme on S3 still costs 1-2x source volume
size per VM, per week. PBS deduplicates across the entire fleet — typical
ratios 10-30x for VMs sharing OS bases.

## Architecture

```
┌─ cinder-backup pod (custom image) ───────────────┐
│                                                  │
│  cinder-backup (Python)                          │
│   │                                              │
│   ↓ load entry point                             │
│  cinder_backup_pbs.driver.PbsBackupDriver        │
│   │                                              │
│   ├─ rbd-nbd map ──→ /dev/nbdN (RBD snapshot)    │
│   └─ subprocess  ──→ proxmox-backup-client       │
│                                                  │
└──────────────────────────────────────────────────┘
                              │
                              ↓ HTTPS, chunks
                       ┌──────────────┐
                       │  PBS server  │
                       └──────────────┘
```

## Constraints

- Cinder backend must be Ceph RBD. LVM, NFS, iSCSI not supported.
- Cinder volume name in RBD must be the bare UUID (default for newer
  cinder configs) — driver does not strip a `volume-` prefix.
- Container must run privileged for `rbd-nbd` mapping.
- `/tmp` and `/root/.cache` inside the container must be on tmpfs
  (overlayfs breaks `O_TMPFILE` which `proxmox-backup-client` uses).

## Licensing

Driver code: Apache-2.0.
Runtime dependency `proxmox-backup-client`: AGPLv3, installed via Proxmox
apt repository, not modified, invoked as a separate process. No combined
work is produced. See `NOTICE` for details.

## Build

```
docker build -t registry.example/cinder-backup-pbs:dev -f ci/Dockerfile .
```

## Configure

In cinder `cinder.conf`:

```
[DEFAULT]
backup_driver = cinder_backup_pbs.driver.PbsBackupDriver

[pbs_backup]
repository = tin@pbs!openstack@10.100.72.80:RAID5
fingerprint = e6:8e:9d:...:2d
password_file = /etc/cinder/pbs-token
namespace_prefix = openstack
rbd_pool = cinder-volumes
rbd_user = cinder
rbd_keyring = /etc/ceph/ceph.client.cinder.keyring
```

## Testing

```
pip install -e '.[test]'
pytest tests/unit
```

Integration tests need a reachable PBS plus a Ceph RBD pool. See
`tests/integration/README.md`.

## Limitations

- No `--incremental` API surface: PBS deduplicates implicitly across all
  backups in a namespace; cinder `parent_id` is always `None`.
- No backup-of-backup. Use PBS `sync-job` for off-site replication.
- Backup retention is **not** enforced by this driver. Drive cinder
  `volume backup delete` from a scheduler (e.g. cron, WHMCS hook).
