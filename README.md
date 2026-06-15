# cinder-backup-pbs

> The best\* Backup Server, meet the best\* Open Source Cloud.

This is the duct tape between two pieces of software that were never
supposed to talk to each other and are honestly much happier now that
they do: **Proxmox Backup Server** (the best\* backup server) and
**OpenStack Cinder** (the best\* open source cloud's block storage).

Cinder thinks it's talking to a normal backup target. PBS thinks a
normal client is talking to it. Neither suspects a thing. In the middle
sits an out-of-tree Cinder backup driver that stores deduplicated,
content-addressed, chunked backups in a PBS datastore.

You point `openstack volume backup create/restore/delete` at it like you
always have. Under the hood the driver shells out to
`proxmox-backup-client` and lets PBS do what PBS does disturbingly well:
pretend your fleet is much smaller than it actually is.

## Status

Pre-production. Here be dragons, but well-fed ones. Targets OpenStack
2026.1 with a Ceph RBD volume backend.

## Why bother

Native Cinder backup drivers (S3, Swift, Ceph, NFS) do not deduplicate.
At all. A weekly full + incremental scheme on S3 still costs 1–2× source
volume size per VM, per week. You are paying, repeatedly, to store the
same Ubuntu base image forty times.

PBS deduplicates across the *entire fleet*. Typical ratios land at
**10–30×** for VMs sharing OS bases. Forty Ubuntu boxes? PBS stores the
common chunks once and quietly judges your old backup bill.

## How the sausage is made

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

Translation: snapshot the RBD volume, map it to a block device with
`rbd-nbd`, hand that device to `proxmox-backup-client`, get out of the
way. The driver's main job is to know its place.

## House rules (constraints)

- Cinder backend **must** be Ceph RBD. LVM, NFS, iSCSI need not apply.
- The Cinder volume name in RBD must be the bare UUID (the default for
  newer cinder configs) — the driver does not strip a `volume-` prefix.
  It is lazy on purpose.
- Container runs **privileged** for `rbd-nbd` mapping. Yes, really.
- `/tmp` and `/root/.cache` inside the container must live on tmpfs.
  overlayfs breaks `O_TMPFILE`, which `proxmox-backup-client` leans on.
  This will bite you exactly once and you will never forget it.

## Licensing

Driver code: Apache-2.0. See `NOTICE` for runtime dependencies.

## Install

Easiest: pull the prebuilt `cinder-backup` image from GitHub Container
Registry (published on every tagged release):

```
docker pull ghcr.io/thiritin/cinder-backup-pbs:latest
```

The driver itself is a normal Python package that registers a
`cinder.backup.drivers` entry point named `pbs`. Not on PyPI yet — to
install it standalone (e.g. into your own image), use git:

```
pip install git+https://github.com/Thiritin/cinder-backup-pbs@main
```

It needs `proxmox-backup-client`, `ceph-common`, and `rbd-nbd` present in
the same image as `cinder-backup`. Build options:

- **kolla-ansible** — use `kolla/template-overrides.j2`. See
  [`docs/kolla.md`](docs/kolla.md). (Kolla builds its own image, so it
  does not use the ghcr image.)
- **openstack-helm / airship, custom build** — use `ci/Dockerfile`. See
  [`docs/install.md`](docs/install.md).

## Build

```
docker build -t registry.example.com/cinder-backup-pbs:dev -f ci/Dockerfile .
```

## Configure

In cinder `cinder.conf`:

```ini
[DEFAULT]
backup_driver = cinder_backup_pbs.driver.PbsBackupDriver

[pbs_backup]
repository = cinder@pbs!openstack@pbs.example.com:RAID5
fingerprint = aa:bb:cc:...:ff
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

- No `--incremental` API surface. PBS deduplicates implicitly across
  every backup in a namespace, so cinder `parent_id` is always `None`.
  Every backup is logically a full, but only the changed chunks hit the
  wire and the datastore.
- No backup-of-backup. For off-site copies, use PBS `sync-job` to
  replicate to a second PBS instance.
- Retention is not enforced here. Drive cinder `volume backup delete`
  from your own scheduler (cron, a WHMCS hook, whatever you already run).

---

\* "best" is a quantifiable\*\* part — meaning: it's my opinion. Your
mileage, benchmarks, and religious affiliations may vary.

\*\* "quantifiable" is also my opinion.
