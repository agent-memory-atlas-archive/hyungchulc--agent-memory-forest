# WSL2 portability

Memory Forest v0.3 targets POSIX filesystems with Unix ownership and permission
modes. The documented Windows-facing posture is to run the CLI in WSL2 and keep
the private forest inside the Linux filesystem of the WSL distribution. It is
not supported to operate a live forest directly from native Windows.

## Keep the live forest in the Linux filesystem

Choose a private directory under your WSL Linux account's home directory. Do
not keep the live forest under `/mnt/c`, another `/mnt/<drive>` path, or any
other Windows-mounted path.
Those paths use Windows-backed filesystem behavior and are not equivalent to
the POSIX ownership and mode contract enforced by this release.

Use WSL tools for initialization, validation, indexing, maintenance, and
backup. Avoid Windows-host tools that can rewrite the live forest or change its
ACLs. A Windows application may be able to see a WSL file, but that does not
make native Windows ACL semantics or direct Windows-host operation supported.

Before maintaining a forest, set one exact root and check the mount and root
metadata from WSL:

```sh
FOREST_ROOT="/absolute/path/to/private-forest"

test -d "$FOREST_ROOT" && test ! -L "$FOREST_ROOT"
findmnt -T "$FOREST_ROOT" -no FSTYPE,TARGET
stat -c '%A %a %U:%G %n' "$FOREST_ROOT"
```

The mount report should identify the WSL Linux filesystem, not a Windows drive
mount. Treat command output as private: it can reveal the selected root or
route names.

## Ownership and modes

The v0.3 private-mode checks expect directories to be `0700` and files to be
`0600`, owned by the intended WSL Linux account. Inspect the root first, then
inspect any unexpected entries without following symlinks:

```sh
find "$FOREST_ROOT" -xdev -type d ! -perm 0700 -print
find "$FOREST_ROOT" -xdev -type f ! -perm 0600 -print
find "$FOREST_ROOT" -xdev \( ! -user "$(id -u)" -o ! -group "$(id -g)" \) -print
```

An empty result means that check found no exception. Investigate a non-empty
result before changing it. Do not use a broad `sudo chown -R` or permission
repair across an unverified path. If a specific real directory or regular file
is confirmed to belong only to the current account, repair that exact item:

```sh
chown -- "$(id -u):$(id -g)" "$FOREST_ROOT/relative/path"
chmod u=rwx,go= -- "$FOREST_ROOT/relative/directory"
chmod u=rw,go= -- "$FOREST_ROOT/relative/file"
```

Do not apply these commands to a symlink. If ownership or mode differences are
unexpected, stop automation, review the affected item, and run the normal
forest validation before continuing.

## Local snapshots and backups

Keep snapshots outside the live forest when practical, on a private Linux
filesystem that preserves the same POSIX controls. Do not put backup archives
inside the forest or its derived-state directories. Backups can contain
canonical bodies and derived indexes, so protect them as private data.

Stop or fence every writer before copying. A filesystem copy is not a
transactional snapshot of a changing forest, and the sibling maintenance lock
is outside the forest root. Validate and audit the copied forest before treating
it as recovery evidence.

For a local copy snapshot, use distinct operator-selected roots and a private
destination directory:

```sh
FOREST_ROOT="/absolute/path/to/private-forest"
BACKUP_ROOT="/absolute/path/to/private-memory-forest-snapshots"
SNAPSHOT_PATH="$BACKUP_ROOT/snapshot-<timestamp>"

umask 077
mkdir -p -m 700 "$BACKUP_ROOT"
test ! -e "$SNAPSHOT_PATH"
cp -a -- "$FOREST_ROOT" "$SNAPSHOT_PATH"
stat -c '%A %a %U:%G %n' "$SNAPSHOT_PATH"
```

Replace the placeholders with local paths before running the commands. Confirm
the snapshot destination is outside `FOREST_ROOT` and stays on a Linux
filesystem; copying a snapshot to a Windows-mounted path does not preserve the
supported safety contract. Test recovery against a separate disposable local
directory, never by overwriting the live forest.

## What WSL2 does and does not establish

WSL2 supplies a Linux-facing operating posture for a Windows host; it does not
make Windows ACL behavior equivalent to POSIX modes, nor does it make a
Windows-mounted path safe for the live forest. This guide does not add native
Windows or WSL2 to the tested platform matrix. Before relying on a WSL
distribution, filesystem, backup process, or maintenance wrapper, run the
repository checks and exercise the workflow against a disposable local forest
in that target environment.

All guidance on this page is local and network-free. It does not require a
hosted service, remote backup target, or network connection.
