# Operations

## Garbage collection

This driver only forgets snapshot manifests. Chunk reclamation happens
on the PBS server via its scheduled `garbage-collect` job. Configure it
when you create the datastore:

```bash
proxmox-backup-manager datastore update RAID5 \
    --gc-schedule 'sat 03:00' \
    --notification-mode notification-system
```

After every prune cycle (cinder `volume backup delete`), expect chunks
to stay on disk until the next GC pass. Do not size the datastore based
on logical sizes — size on chunk store size (`proxmox-backup-manager
datastore status RAID5`).

## Retention

The driver does not implement retention. Drive deletes via your own
scheduler. For WHMCS-driven hosting, this is the daily
`PbsBackupManager::pruneDr()` hook documented in the `whmcs-newdev`
repo.

## Backup verification

PBS supports periodic `verify` jobs that re-hash chunks against
manifests. Enable per datastore:

```bash
proxmox-backup-manager verify-job create job-vmonly \
    --store RAID5 \
    --ns openstack \
    --schedule 'mon 04:00' \
    --ignore-verified true \
    --outdated-after 30
```

`--ignore-verified` skips chunks verified within `--outdated-after` days
to bound runtime.

## Off-site replication

Use PBS `sync-job` between two PBS instances; this driver has nothing to
do with replication.

```bash
proxmox-backup-manager sync-job create job-dr \
    --store RAID5 \
    --remote dr-pbs \
    --remote-store backups \
    --schedule 'daily 05:00'
```

## Failure modes + recovery

| Symptom | Likely cause | Recovery |
|---------|--------------|----------|
| Backup fails with `Operation not supported (os error 95)` | `/pbs-tmp` not mounted as tmpfs | Fix Helm volume mounts; restart pod |
| Backup fails with `namespace not found` | Token lacks namespace-create perm; `ensure_namespace` failed silently due to wrong error string match | Pre-create per-project namespace from provisioning hook; grant `DatastoreModify` or `DatastorePowerUser` |
| Backup hangs | `rbd map` (kernel) was used instead of `rbd-nbd`; check the driver still uses `RbdHelper.nbd_map` | Kill pod, verify driver code path |
| `rbd-nbd` map fails: `nbd module not loaded` | Host kernel missing `nbd` module | Load on node: `modprobe nbd nbds_max=64`; persist in `/etc/modules-load.d` |
| Restore writes wrong data | Snapshot manifest corruption (rare) — run `verify-job` to detect | Restore from prior snapshot in chain |
| `forget` returns `No such file or directory` | Snapshot already deleted by GC or out-of-band | Driver swallows; safe |
| Stale staging RBD snapshots (`pbs-stage-...`) | Driver pod killed mid-backup | `rbd snap unprotect && snap rm` manually; harmless until cleanup |

## Monitoring metrics

Worth scraping (PBS exposes via API):

* `/api2/json/status/datastore-usage` — chunk store size, dedup ratio
* `/api2/json/admin/datastore/<store>/snapshots` — backup count per namespace
* GC + verify job task status via `/api2/json/admin/datastore/<store>/tasks`

Alert on:

* No new snapshots in namespace for >36h (nightly cron broken)
* Dedup ratio drops sharply (encryption flip, new fleet, or bug)
* GC failing for >3 cycles
* Datastore usage >80%

## Token rotation

```bash
proxmox-backup-manager user delete-token cinder@pbs nightly
proxmox-backup-manager user generate-token cinder@pbs nightly
# Update /etc/cinder/pbs-token in the k8s Secret, rolling-restart cinder-backup.
```

In-flight backups will fail mid-stream; cinder will mark them error
and the next cron pass will retry cleanly.
