# Custom cinder-backup image with PBS client + RBD tooling
# baked in, plus the cinder-backup-pbs driver.
#
# Build:
#   docker build -t registry.example/cinder-backup-pbs:<tag> -f ci/Dockerfile .
#
# Runtime requirements (set by Helm values, not this Dockerfile):
#   * privileged: true  (for rbd-nbd to /dev/nbd*)
#   * hostPath /dev, /sys, /lib/modules mounted
#   * emptyDir{medium: Memory} mounted at /pbs-tmp  (overlayfs breaks O_TMPFILE)

ARG CINDER_IMAGE=quay.io/airshipit/cinder:2026.1-ubuntu_noble
FROM ${CINDER_IMAGE}

USER root

# Proxmox apt key + repo (Debian Bookworm builds run fine on Ubuntu Noble)
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
        curl ca-certificates gpg \
 && curl -fsSL https://enterprise.proxmox.com/debian/proxmox-release-bookworm.gpg \
        -o /etc/apt/trusted.gpg.d/proxmox-release-bookworm.gpg \
 && echo "deb http://download.proxmox.com/debian/pbs-client bookworm main" \
        > /etc/apt/sources.list.d/pbs-client.list \
 && apt-get update \
 && apt-get install -y --no-install-recommends \
        proxmox-backup-client \
        ceph-common \
        rbd-nbd \
 && rm -rf /var/lib/apt/lists/*

# Install the driver into the cinder venv.
# The upstream image's venv has no `pip`, so bootstrap via `ensurepip`
# (same trick as the horizon-ironic image; see docs/custom-images.md
# in the cluster repo).
COPY pyproject.toml /opt/cinder-backup-pbs/
COPY cinder_backup_pbs /opt/cinder-backup-pbs/cinder_backup_pbs
RUN /var/lib/openstack/bin/python -m ensurepip --upgrade \
 && /var/lib/openstack/bin/python -m pip install --no-cache-dir /opt/cinder-backup-pbs

# Pre-create the tmpfs mountpoint and cache subdir.
# Actual tmpfs mount happens at pod startup via emptyDir.
RUN mkdir -p /pbs-tmp/cache

USER cinder
