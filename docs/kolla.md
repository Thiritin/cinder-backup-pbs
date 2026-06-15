# Install on kolla-ansible

This driver is a normal Python package (`cinder-backup-pbs`) that
registers a `cinder.backup.drivers` entry point named `pbs`. Not on PyPI
yet — the kolla override installs it from git by default. On
kolla-ansible you do three things:

1. Bake the driver + `proxmox-backup-client` + `rbd-nbd` into the
   `cinder-backup` image (kolla-build template-override).
2. Point `cinder.conf` at the driver and the `[pbs_backup]` group.
3. Give the `cinder_backup` container the privileges + tmpfs it needs.

## 1. Build the image

Use the provided `kolla/template-overrides.j2`.

`/etc/kolla/kolla-build.conf`:

```ini
[DEFAULT]
base = ubuntu
template_override = /path/to/cinder-backup-pbs/kolla/template-overrides.j2
```

```bash
kolla-build --config-file /etc/kolla/kolla-build.conf cinder-backup
```

The override installs the driver from git (`@main`) by default. Override
`CINDER_BACKUP_PBS_SPEC` to pin a tag, a local sdist/wheel, or — once
published — `cinder-backup-pbs` from PyPI:

```bash
kolla-build --config-file /etc/kolla/kolla-build.conf cinder-backup \
    --build-args CINDER_BACKUP_PBS_SPEC=git+https://github.com/Thiritin/cinder-backup-pbs@v0.1.0
```

Push the resulting image to your registry and set its tag in `globals.yml`
(or rely on `kolla-ansible`'s push step).

## 2. Configure cinder

kolla-ansible merges any operator config under `/etc/kolla/config/`. Drop
a file at `/etc/kolla/config/cinder/cinder-backup.conf`:

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
pbs_tmpdir = /pbs-tmp
pbs_cache_dir = /pbs-tmp/cache
```

> `backup_driver` uses the full dotted path. The published entry-point
> short name `pbs` also works on cinder releases that resolve backup
> drivers via stevedore; the dotted path works everywhere, so prefer it.

Ship the PBS token file. Put the secret at
`/etc/kolla/config/cinder/pbs-token` and bind-mount it — kolla-ansible
mounts `/etc/kolla/config/cinder/` into the cinder containers at
`/etc/cinder/`, so `password_file = /etc/cinder/pbs-token` resolves.

## 3. Container privileges + mounts

`rbd-nbd` needs `/dev/nbd*` and the `nbd` kernel module; `pbc` needs a
real tmpfs (overlayfs breaks its `O_TMPFILE`). In `globals.yml` /
`cinder.conf` overrides this means the `cinder_backup` container must run
privileged with host `/dev`, `/sys`, `/lib/modules`, and a tmpfs at
`/pbs-tmp`. kolla-ansible exposes this via
`cinder_backup_dimensions` / custom `docker` volume + privileged settings;
if your kolla version does not template privileged backup containers,
override the `cinder-backup` service definition in
`/etc/kolla/config/cinder/` or run the backup agent on a dedicated host.

Load the `nbd` module on the backup hosts and persist it:

```bash
modprobe nbd nbds_max=64
echo 'nbd' > /etc/modules-load.d/nbd.conf
```

## Verify

```bash
openstack volume create --size 1 pbs-smoke
openstack volume backup create --name smoke pbs-smoke
openstack volume backup list
openstack volume backup restore <backup-id> <new-vol-id>
openstack volume backup delete <backup-id>
```

See `docs/operations.md` for GC, retention, verify jobs, and failure
modes.
