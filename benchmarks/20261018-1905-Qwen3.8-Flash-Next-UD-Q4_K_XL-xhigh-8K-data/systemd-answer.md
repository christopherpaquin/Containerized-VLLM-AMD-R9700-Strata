## Strategy: characterize the failure before editing the unit file

When an executable works interactively as root but fails immediately under systemd, the unit file is not necessarily wrong. A systemd service normally runs in a much more constrained environment than an interactive root shell: it may have a different user, different environment variables, a different working directory, different file permissions/SELinux labeling expectations, and sandboxing restrictions.

The goal is to gather enough evidence to identify whether the failure is caused by:

1. The executable/path being invalid in the service context.
2. Permissions/DAC or SELinux/MAC restrictions.
3. A wrong `User=`, `Group=`, `WorkingDirectory=`, or sandboxing option.
4. Missing environment variables or environment files.
5. A failed `ExecStartPre=` / dependency / filesystem/mount precondition.
6. An application-level failure due to configuration, cwd, logs, sockets, temp files, etc.

Do not start by rewriting `ExecStart`, disabling sandboxing options, adding `SELinux=0`-style workarounds, or running in permissive mode as a permanent fix. First collect evidence.

---

# 1. Record the exact failure

Start or restart the service and immediately capture the failure details.

```bash
systemctl restart custom.service
systemctl status custom.service --full --no-pager --lines=200
journalctl -u custom.service --no-pager --since "-5 minutes" -o short-iso
```

Record from `systemctl status`:

```text
Loaded: ... enabled/disabled/masked/static
Active: failed/result/dead/...
Process: ... ExecStart=... code=... status=...
Main PID: ...
```

Then get structured details:

```bash
systemctl show custom.service -p ActiveState -p SubState -p Result
systemctl show custom.service -p ExecMainCode -p ExecMainStatus -p StatusText
systemctl show custom.service -p MainPID -p ExecMainPID
systemctl show custom.service -p LoadState -p UnitFileState -p UnitFilePresets
```

Important clues:

| Evidence | Likely meaning |
|---|---|
| `code=exited status=0` but unit still failed | Another command failed, e.g. `ExecStartPre=`, `ExecStartPost=`, dependency, or unit condition |
| `code=exited status=1` | The program returned a normal application error |
| `code=exited status=203/EXEC` | systemd could not execute the command: missing path, bad interpreter, bad permissions, missing script interpreter, `noexec` mount, etc. |
| `code=exited status=217/USER` | `User=` or `Group=` cannot be resolved, or the service cannot change to that user |
| `code=exited status=200/CHDIR` | `WorkingDirectory=` is missing or inaccessible |
| `code=killed status=9` | Killed by `SIGKILL`, possibly OOM killer, limit, or external kill |
| `code=killed status=6` | Core dump / signal |
| `Result=dependency` | A required dependency failed |
| `Result=start-limit` | Too many start attempts too quickly |
| `Result=timeout` | Service did not start/readiness signal within `TimeoutStartSec=` |
| `Unit is masked` | The unit is masked; start will fail regardless of executable correctness |

Also check whether the unit is actually enabled and not masked:

```bash
systemctl is-enabled custom.service
systemctl status custom.service | grep -i masked
```

If it is masked, that alone can explain immediate failure.

---

# 2. Capture the unit definition and effective runtime properties

Before editing anything, save the installed unit and overrides.

```bash
systemctl cat --full custom.service
systemd-analyze verify custom.service
```

Capture effective properties that are commonly relevant:

```bash
systemctl show custom.service -p Type
systemctl show custom.service -p ExecStart -p ExecStartPre -p ExecStartPost
systemctl show custom.service -p User -p Group -p UserGroup -p SupplementaryGroups
systemctl show custom.service -p WorkingDirectory -p Path -p Environment -p EnvironmentFiles
systemctl show custom.service -p StandardInput -p StandardOutput -p StandardError
systemctl show custom.service -p TimeoutStartUSec -p TimeoutStopUSec
systemctl show custom.service -p Restart -p RestartUSec -p StartLimitIntervalUSec -p StartLimitBurst
systemctl show custom.service -p LimitNOFILE -p LimitNPROC -p LimitCORE
```

Also inspect sandboxing/security settings:

```bash
systemctl show custom.service \
  -p NoNewPrivileges \
  -p ProtectSystem \
  -p ProtectHome \
  -p PrivateTmp \
  -p PrivateDevices \
  -p PrivateMounts \
  -p ProtectKernelModules \
  -p ProtectKernelTunables \
  -p CapabilityBoundingSet \
  -p AmbientCapabilities \
  -p RestrictAddressFamilies \
  -p RestrictNamespaces \
  -p RestrictRealtime \
  -p RestrictSUIDSGID \
  -p SystemCallFilter \
  -p MountFlags \
  -p SELinuxContext
```

A good overview command:

```bash
systemd-analyze security custom.service
```

This helps determine whether the service is more restricted than the interactive root shell.

Evidence to collect:

- Is `User=root` actually set, or is the service running as `nobody`, a dedicated service user, or some other account?
- Is `ProtectSystem=strict` set? Then even root may be prevented from writing many filesystem paths.
- Is `PrivateTmp=yes` set? Then `/tmp` and `/var/tmp` are private namespaces and may appear empty or inaccessible.
- Is `ProtectHome=yes` set? Then `/root`, `/home`, `/home/*` may be unavailable.
- Is `NoNewPrivileges=yes` set? Then setuid or capability-based behavior may be blocked.
- Are `ProtectKernelTunables=` or `ProtectKernelModules=` set? Then kernel/system paths may be read-only or hidden.
- Are `EnvironmentFile=` paths present and readable? A missing `EnvironmentFile=/path/that/does/not/exist` can make the unit fail immediately.
- Are `ExecStartPre=` commands present? A failing pre-command can cause immediate failure even if the main executable works.

---

# 3. Examine user identity

The manual test was as root. The service may not run as root, even if the unit file was written assuming it would.

Gather:

```bash
systemctl show custom.service -p User -p Group -p UserGroup -p SupplementaryGroups
getent passwd "$(systemctl show custom.service -p User= --value custom.service | awk '{print $1}')"
getent group "$(systemctl show custom.service -p Group= --value custom.service | awk '{print $1}')"
```

If `User=` is set to a non-root account:

```bash
service_user="$(systemctl show custom.service -p User= --value custom.service)"
sudo -u "$service_user" id
sudo -u "$service_user" env
sudo -u "$service_user" pwd
```

Also check whether the service user can access the executable and its dependencies:

```bash
namei -l /path/to/executable
ls -lZ /path/to/executable
ls -ldZ /path/to /path/to/executable
```

Evidence to look for:

- `User=` missing or invalid.
- `status=217/USER` in journal/status.
- The service runs as a user that cannot traverse parent directories because a parent directory lacks execute permission.
- The service user’s home directory is missing while the application expects `HOME`.
- `ProtectHome=yes` prevents access to files under `/root`, `/home`, etc.
- The executable or config files are owned by root with restrictive permissions, so they work as root but not as the service user.
- `NoNewPrivileges=yes` prevents a setuid executable or capability acquisition from working.

Important: “works as root” is not a valid test for a service running as a dedicated user.

If possible, reproduce as the service user in a minimal environment:

```bash
sudo -u service_user env -i PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin /path/to/executable
```

But prefer systemd-like testing described later.

---

# 4. Examine the environment

A root shell usually has a rich environment loaded from:

```text
/etc/profile
/etc/profile.d/*
~/.bash_profile
~/.bashrc
/etc/environment
```

A systemd service does not automatically inherit that shell environment. It uses:

- The systemd manager environment.
- `Environment=` and `EnvironmentFile=` from the unit.
- A default environment set by systemd.
- Drop-in environment settings.
- Some global configuration.

Gather evidence:

```bash
systemctl show custom.service -p Environment -p EnvironmentFiles -p Path -p WorkingDirectory
systemctl show-environment
systemctl cat custom.service
```

Check the global systemd environment:

```bash
systemctl show-environment
```

Compare it to a root login environment:

```bash
sudo -i env | sort
```

But do not assume they should match. The service environment is intentionally different.

Look specifically for:

- `PATH`: Scripts often rely on `/usr/bin`, `/usr/local/bin`, etc. systemd’s default `PATH` may be enough, but a custom unit or shell profile may not be replicated.
- `HOME`: Application may expect `/root` or the service user’s home.
- `LANG` / `LC_ALL` / `LOCALE`: Some applications or scripts behave differently or fail if locale is unset.
- `JAVA_HOME`, `NODE_PATH`, `PYTHONPATH`, `LD_LIBRARY_PATH`, `XDG_RUNTIME_DIR`, `DB_HOST`, `REDIS_URL`, `CONFIG_FILE`, etc.
- `EnvironmentFile=` paths: If the unit has `EnvironmentFile=/etc/custom/config` without a leading `-`, a missing file can cause immediate failure.

Check environment file existence and readability:

```bash
systemctl show custom.service -p EnvironmentFiles --value
sudo cat /etc/custom/config
```

If the unit has something like:

```ini
EnvironmentFile=/etc/custom/config
```

and the file does not exist, systemd may fail before executing the main process. A leading dash makes it optional:

```ini
EnvironmentFile=-/etc/custom/config
```

But do not change it until you confirm the missing file is actually the cause.

---

# 5. Examine the working directory

The working directory can determine how the application behaves. An executable may work from `/root` manually but fail when started from `/`, `/var/lib/custom`, or a temporary directory.

Gather:

```bash
systemctl show custom.service -p WorkingDirectory
```

If a working directory is configured:

```bash
wd="$(systemctl show custom.service -p WorkingDirectory= --value custom.service)"
ls -ld "$wd"
namei -l "$wd"
```

Check whether it exists:

```bash
test -d "$wd" && echo exists || echo missing
```

Look for:

- `status=200/CHDIR`.
- Application expects relative paths like `./config`, `./data`, or `./logs`.
- Application writes logs or PID files using relative paths.
- Service user cannot write to the configured working directory.
- Working directory is on a filesystem that is not yet mounted when the service starts.
- `ProtectSystem=strict` or `ProtectHome=yes` makes the expected directory unavailable.

If no `WorkingDirectory=` is configured, determine the effective default before assuming it is what the application needs.

---

# 6. Examine executable path, interpreter, and filesystem permissions

A common immediate failure is `status=203/EXEC`. That usually means systemd could not successfully `execve()` the command.

Get the exact command:

```bash
systemctl show custom.service -p ExecStart --value
systemctl cat custom.service
```

Check the exact path:

```bash
exec_path="$(systemctl show custom.service -p ExecStart= --value custom.service | awk '{print $1}')"
echo "$exec_path"
realpath "$exec_path"
readlink -f "$exec_path"
ls -lZ "$exec_path"
stat "$exec_path"
```

Check execute permission and parent-directory traversal:

```bash
namei -l "$(readlink -f "$exec_path")"
```

If it is a script:

```bash
file "$exec_path"
head -n 5 "$exec_path"
```

If the shebang is:

```bash
#!/usr/bin/env python3
```

then `/usr/bin/env` and `python3` must be available and executable in the service context.

If it is a dynamically linked binary:

```bash
file "$exec_path"
ldd "$exec_path"
readelf -h "$exec_path"
```

Check for:

- Missing binary.
- Wrong architecture.
- Missing shared libraries.
- Missing interpreter.
- Missing shebang.
- Wrong interpreter path.
- File not executable.
- Parent directory not traversable.
- SELinux context denying execution.
- Mount options containing `noexec`, `nodev`, `nosuid`.

Check mount options:

```bash
findmnt -T "$(readlink -f "$exec_path")"
mount | grep -E "noexec|nodev|nosuid"
```

If the executable is under `/tmp`, `/var/tmp`, `/opt`, `/data`, `/mnt`, `/nfs`, `/home`, etc., verify the filesystem is mounted and not `noexec`.

Also check whether the path uses variables:

```bash
systemctl show custom.service -p ExecStart --value
```

Make sure variables expand to the expected absolute path.

---

# 7. Examine SELinux evidence on RHEL

RHEL commonly runs with SELinux enforcing. A program that works manually as root may still be denied in a service context, especially if the service has a different SELinux context or accesses unexpected paths.

Collect SELinux state:

```bash
getenforce
sestatus
```

Check recent AVC denials:

```bash
ausearch -m avc -ts recent
ausearch -m avc -ts today
journalctl -t audit --since "-10 minutes"
```

If the service failed at a known time:

```bash
ausearch -m avc -ts "2026-06-16 10:12:00"
```

If the process name is known:

```bash
ausearch -c "custom-executable" -m avc
```

Use SELinux analysis tools if available:

```bash
sealert -a /var/log/audit/audit.log
ausearch -m avc -ts recent | audit2allow -R
```

But treat `audit2allow` output as investigative evidence, not an automatic fix. Do not blindly load generated policy.

Check contexts of relevant files:

```bash
ls -Z "$exec_path"
ls -Z /etc/custom/
ls -Z /var/lib/custom/
ls -Z /var/log/custom/
```

Check expected labels:

```bash
restorecon -nv /path/to/executable
restorecon -nv /var/lib/custom
```

The `-n` option shows what would change without changing anything.

If the process managed to start briefly:

```bash
ps -eZ | grep -i custom
```

Check whether the service specifies an SELinux context:

```bash
systemctl show custom.service -p SELinuxContext
```

Evidence to look for:

- AVC denial for `execute`, `read`, `write`, `open`, `getattr`, `search`, `create`, `transition`, etc.
- Incorrect file context on the binary, script, config files, logs, sockets, PID file, or runtime directory.
- SELinux boolean preventing expected behavior.
- Service transitioning into a confined type that lacks needed permissions.
- Application writing to unexpected directories that are not labeled correctly.

If SELinux is likely involved, the correct next step is usually to examine the denial and determine why the service is accessing a path or performing an operation not allowed for its SELinux context. Temporarily setting SELinux permissive may confirm the theory in a controlled diagnostic window, but it is not evidence of the root cause and should not be left enabled.

---

# 8. Examine dependencies and pre-execution commands

Immediate failure may not be caused by `ExecStart=` at all. A pre-exec command, dependency, mount, or condition may fail first.

Gather dependency data:

```bash
systemctl list-dependencies --after --before custom.service
systemctl show custom.service -p Requires -p Wants -p BindsTo -p After -p Before
systemctl show custom.service -p RequiresMountsFor -p PropagatesReloadTo
systemctl show custom.service -p ExecStartPre -p ExecStartPost
```

Check dependency states:

```bash
systemctl is-active network.target
systemctl is-active network-online.target
systemctl is-active local-fs.target
systemctl is-active remote-fs.target
```

If the executable lives on a mounted filesystem:

```bash
findmnt -T /path/to/executable
systemctl status "$(findmnt -o SOURCE --noheadings /path/to/executable)" 2>/dev/null || true
```

Look for:

- `ExecStartPre=` returning nonzero.
- A required database, network, filesystem, or mount unit failing.
- `Requires=` pointing to a failed unit.
- `After=` without `Requires=` or `Wants=` if the unit must wait for something.
- `NetworkManager-wait-online` or `network-online.target` behavior mismatch.
- Database or cache not ready when the service starts.
- Executable on a filesystem that is not mounted yet.
- A condition such as `ConditionPathExists=` or `ConditionVirtualization=` causing the unit to fail or remain inactive.

Check unit conditions and pre/post commands from the journal:

```bash
journalctl -u custom.service --since "-5 minutes" -o verbose
```

Look for lines containing:

```text
ConditionPathExists=
ExecStartPre=
Dependency failed
Starting custom.service...
Failed to start custom.service.
```

If `ExecStartPre=` fails, the main executable may never run.

---

# 9. Examine filesystem paths, libraries, and configuration access

The manual root test may have used files available in a way that the service cannot use.

Check the executable and its dependencies:

```bash
exec_path="$(systemctl show custom.service -p ExecStart= --value custom.service | awk '{print $1}')"

file "$exec_path"
ldd "$exec_path"
readelf -h "$exec_path"
findmnt -T "$exec_path"
```

Check library dependencies and missing libraries:

```bash
ldd "$exec_path" | grep "not found"
```

If missing libraries exist under a custom directory, compare the manual environment with the service environment:

```bash
sudo -i env | grep -E 'LD_LIBRARY_PATH|PATH'
systemctl show custom.service -p Environment -p EnvironmentFiles
```

But do not add `LD_LIBRARY_PATH` merely because the manual shell had it. First determine whether the library path is actually the cause.

Check application configuration paths. If the application has known config or log paths:

```bash
ls -lZ /etc/custom/
ls -lZ /var/lib/custom/
ls -lZ /var/log/custom/
ls -lZ /run/custom/
```

Check for permissions and SELinux labels.

Also check whether the service writes temporary files:

```bash
systemctl show custom.service -p PrivateTmp
```

If `PrivateTmp=yes`, the service may not see files manually created in `/tmp` or `/var/tmp`.

---

# 10. Examine journal logs in detail

The basic journal command may be insufficient. Use verbose output to capture hidden fields and exact exit codes.

```bash
journalctl -u custom.service --since "-10 minutes" --no-pager -o verbose
```

JSON output is useful:

```bash
journalctl -u custom.service --since "-10 minutes" --no-pager -o json
```

Filter important fields:

```bash
journalctl -u custom.service --since "-10 minutes" -o json | jq -r '
  select(._EXIT_CODE? or .SERVICE_RESULT? or .SERVICE_EXIT_CODE? or .SERVICE_EXIT_STATUS? or .STATUS_TEXT? or .MESSAGE)
'
```

Or simply look for:

```bash
journalctl -u custom.service --since "-10 minutes" --no-pager | grep -Ei 'failed|denied|exec|permission|selinux|avc|timeout|dependency|condition|exit|status='
```

Also check kernel messages for OOM kills, I/O errors, or filesystem issues:

```bash
journalctl -k --since "-10 minutes"
```

Check audit-related messages:

```bash
journalctl -t audit --since "-10 minutes"
```

If journal output is empty, do not assume the program produced no output. Check the unit’s I/O settings:

```bash
systemctl show custom.service -p StandardInput -p StandardOutput -p StandardError
```

If stdout/stderr are redirected to a file, syslog, null, or a custom target, inspect that destination.

---

# 11. Use safe reproduction methods before modifying the original unit

Instead of editing the original unit, create a transient unit or scope that mimics the service. This gives you a controlled place to test.

Example:

```bash
UNIT=custom.service

systemctl show "$UNIT" \
  -p ExecStart \
  -p User \
  -p Group \
  -p WorkingDirectory \
  -p Environment \
  -p EnvironmentFiles \
  -p Type
```

Then create a temporary oneshot unit:

```bash
sudo systemd-run \
  --unit=custom-dbg \
  --service-type=oneshot \
  --property=WorkingDirectory=/var/lib/custom \
  --property=User=customuser \
  --property=Environment=PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin \
  /path/to/executable
```

Inspect it:

```bash
systemctl status custom-dbg
journalctl -u custom-dbg --since "-5 minutes" -o verbose
```

Remove it afterward:

```bash
sudo systemctl stop custom-dbg
sudo systemctl reset-failed
```

This is not editing the original unit, but it still creates a transient unit, so clean it up.

If you want to trace the exact commands and file accesses, use a transient unit running under `strace`:

```bash
sudo systemd-run \
  --unit=custom-strace \
  --service-type=oneshot \
  --property=WorkingDirectory=/var/lib/custom \
  --property=User=customuser \
  /usr/bin/strace -f -s 200 -e trace=execve,openat,access,stat \
    -o /var/tmp/custom-strace.log \
    /path/to/executable
```

Then inspect:

```bash
sudo journalctl -u custom-strace -o verbose
sudo cat /var/tmp/custom-strace.log
```

Use `strace` sparingly and carefully, because the trace may reveal sensitive configuration values. Remove artifacts afterward.

A simpler controlled test for user identity and minimal environment:

```bash
service_user="$(systemctl show custom.service -p User= --value custom.service)"

sudo -u "$service_user" env -i \
  PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin \
  HOME=/nonexistent \
  /path/to/executable
```

This tests whether the failure is caused by:

- Running as a non-root user.
- A minimal environment.
- A missing home directory or environment variable.

Again, do not treat this as the final test; it is an experiment based on evidence.

---

# 12. Hypothesis matrix

Use the collected evidence to choose likely causes.

## `status=203/EXEC`

Likely causes:

- Wrong `ExecStart=` path.
- Missing file.
- Not executable.
- Parent directory not traversable.
- Missing script interpreter.
- Bad shebang.
- Missing shared libraries.
- Wrong architecture.
- `noexec` mount.
- SELinux denial.

Evidence to collect:

```bash
systemctl show custom.service -p ExecStart --value
namei -l "$(systemctl show custom.service -p ExecStart= --value custom.service | awk '{print $1}')"
file "$(systemctl show custom.service -p ExecStart= --value custom.service | awk '{print $1}')"
ldd "$(systemctl show custom.service -p ExecStart= --value custom.service | awk '{print $1}')"
findmnt -T "$(systemctl show custom.service -p ExecStart= --value custom.service | awk '{print $1}')"
ausearch -m avc -ts recent
```

---

## `status=217/USER`

Likely causes:

- `User=` account does not exist.
- `Group=` group does not exist.
- User account cannot be used for login/service.
- NSS or LDAP lookup failing.
- Account disabled or expired.
- Home directory requirements.

Evidence:

```bash
systemctl show custom.service -p User -p Group
getent passwd customuser
getent group customgroup
sudo -u customuser id
```

---

## `status=200/CHDIR`

Likely causes:

- `WorkingDirectory=` missing.
- Directory not accessible.
- Parent directory lacks execute permission.
- Directory on filesystem not mounted.
- `ProtectSystem=` or `ProtectHome=` hides the directory.

Evidence:

```bash
systemctl show custom.service -p WorkingDirectory --value
namei -l /configured/working/directory
findmnt -T /configured/working/directory
```

---

## `EnvironmentFile` failure

Likely causes:

- `EnvironmentFile=/path/to/file` points to a missing file.
- File is not readable by root/systemd or service context.
- File contains invalid environment syntax.

Evidence:

```bash
systemctl show custom.service -p EnvironmentFiles --value
systemctl status custom.service --lines=50
journalctl -u custom.service --since "-5 minutes" -o verbose
```

Check for messages mentioning environment file read failure.

---

## Application exits with `status=1` or another non-zero code

Likely causes:

- Missing configuration.
- Wrong working directory.
- Missing environment variables.
- Log directory not writable.
- PID file path not writable.
- Socket path inaccessible.
- Database/network unavailable.
- Application rejects the user identity.
- SELinux denies file/network/system call access.

Evidence:

- Full journal output.
- Application-specific log files.
- `strace` under transient unit.
- Compare environment manually vs service.
- Check permissions on config/log/PID/socket directories.
- Check SELinux denials.

---

## `Result=dependency`

Likely causes:

- Required unit failed.
- Mount not available.
- Network service not ready.
- Database/cache service failed.
- Filesystem automount not ready.

Evidence:

```bash
systemctl show custom.service -p Requires -p BindsTo -p After -p Before
systemctl list-dependencies custom.service
systemctl status custom.service
journalctl -u custom.service --since "-5 minutes"
journalctl -u failed-dependency.service --since "-5 minutes"
```

---

## No output in the journal

Likely causes:

- `StandardOutput=` or `StandardError=` redirected elsewhere.
- Application logs to its own file.
- Application starts but does not write to stdout/stderr.
- Unit uses `StandardOutput=blackhole` or similar.

Evidence:

```bash
systemctl show custom.service -p StandardOutput -p StandardError
systemctl cat custom.service
```

Then inspect configured output destinations.

---

# 13. Recommended evidence bundle to collect before changing the unit

Before modifying anything, gather this into a file or notes:

```bash
UNIT=custom.service

{
echo "=== Date ==="
date

echo "=== systemctl status ==="
systemctl status "$UNIT" --full --no-pager --lines=200

echo "=== systemctl cat ==="
systemctl cat --full "$UNIT"

echo "=== systemd-analyze verify ==="
systemd-analyze verify "$UNIT"

echo "=== Core properties ==="
systemctl show "$UNIT" \
  -p LoadState -p UnitFileState -p ActiveState -p SubState -p Result \
  -p ExecMainCode -p ExecMainStatus -p StatusText -p MainPID \
  -p Type -p ExecStart -p ExecStartPre -p ExecStartPost \
  -p User -p Group -p UserGroup -p SupplementaryGroups \
  -p WorkingDirectory -p Path -p Environment -p EnvironmentFiles \
  -p StandardInput -p StandardOutput -p StandardError \
  -p TimeoutStartUSec -p TimeoutStopUSec \
  -p Restart -p RestartUSec -p StartLimitIntervalUSec -p StartLimitBurst \
  -p LimitNOFILE -p LimitNPROC -p LimitCORE

echo "=== Security/sandboxing ==="
systemd-analyze security "$UNIT"

echo "=== Dependencies ==="
systemctl show "$UNIT" \
  -p Requires -p Wants -p BindsTo -p After -p Before \
  -p RequiresMountsFor -p PropagatesReloadTo

echo "=== System journal ==="
journalctl -u "$UNIT" --no-pager --since "-10 minutes" -o short-iso

echo "=== Verbose journal ==="
journalctl -u "$UNIT" --no-pager --since "-10 minutes" -o verbose

echo "=== Kernel messages ==="
journalctl -k --no-pager --since "-10 minutes"

echo "=== SELinux ==="
getenforce
sestatus
ausearch -m avc -ts recent
journalctl -t audit --no-pager --since "-10 minutes"
} > /tmp/${UNIT}.systemd-evidence.txt
```

Then add path-specific evidence once you know the paths:

```bash
exec_path="$(systemctl show custom.service -p ExecStart= --value custom.service | awk '{print $1}')"
real_exec="$(readlink -f "$exec_path")"

{
echo "=== Executable path ==="
echo "$exec_path"
echo "$real_exec"

echo "=== File info ==="
ls -lZ "$real_exec"
stat "$real_exec"
file "$real_exec"
ldd "$real_exec"
namei -l "$real_exec"

echo "=== Mount info ==="
findmnt -T "$real_exec"

echo "=== SELinux context check ==="
restorecon -nv "$real_exec"
} >> /tmp/custom.service.systemd-evidence.txt
```

---

# 14. Decision logic before editing the unit

Only edit or add a drop-in after evidence maps to a specific problem.

| Evidence found | Next step |
|---|---|
| `status=203/EXEC` and binary missing/not executable | Correct path, ownership, or mount issue; verify interpreter/libraries |
| `status=203/EXEC` and `noexec` mount | Move executable or adjust mount policy according to environment standards |
| `status=217/USER` | Correct `User=`/`Group=` or create/repair the service account |
| `status=200/CHDIR` | Create/fix `WorkingDirectory=` or app cwd assumptions |
| `EnvironmentFile=` missing and no `-` | Decide whether file should exist, be optional, or be removed; gather evidence first |
| Application exits with error and logs indicate missing config | Fix config path/permissions or add correct `EnvironmentFile=`/`WorkingDirectory=` based on evidence |
| AVC denial for file/socket/write/execute | Fix SELinux labeling, boolean, or policy; do not disable SELinux |
| Dependency failed | Fix required unit or dependency ordering/requirement; do not blindly add `After=` |
| `ProtectSystem=`, `PrivateTmp=`, `ProtectHome=` conflict with app behavior | Understand why the app needs those paths; add narrowly scoped exceptions if necessary |
| `ExecStartPre=` failed | Fix pre-command or remove only if evidence shows it is invalid |
| Unit masked | Unmask only after confirming it should not be masked |

---

# 15. Key conclusion

Before changing the unit file, collect evidence that answers these questions:

1. What exact failure did systemd record?  
   Use `systemctl status`, `systemctl show`, and `journalctl`.

2. What command does systemd try to execute?  
   Use `systemctl cat` and `systemctl show -p ExecStart`.

3. Which user/group does it use?  
   Use `systemctl show -p User -p Group`.

4. What environment does it receive?  
   Use `systemctl show -p Environment -p EnvironmentFiles` and `systemctl show-environment`.

5. Where does it start?  
   Use `systemctl show -p WorkingDirectory`.

6. Can that user access the executable and its dependencies?  
   Use `namei`, `ls -lZ`, `stat`, `ldd`, `findmnt`.

7. Is SELinux denying anything?  
   Use `getenforce`, `ausearch`, `journalctl -t audit`, `sealert`, and file context checks.

8. Are dependencies or pre-exec commands failing?  
   Use `systemctl show`, `systemctl list-dependencies`, and the verbose journal.

9. Is the filesystem path available and executable?  
   Use `realpath`, `file`, `ldd`, `findmnt`, and mount-option inspection.

10. Is there no output because logs are redirected?  
   Use `systemctl show -p StandardOutput -p StandardError`.

With that evidence, you can change only the specific unit directive needed, preferably through a drop-in:

```bash
systemctl edit --runtime custom.service   # temporary for testing
```

or, after diagnosis:

```bash
systemctl edit custom.service             # persistent drop-in
```

But the diagnostic discipline is: first characterize the failure, then reproduce it safely, then change the smallest supported setting.
