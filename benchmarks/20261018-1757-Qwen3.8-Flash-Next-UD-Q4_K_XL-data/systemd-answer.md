A good way to handle this is to treat it as a **difference-of-context problem**: the binary may be fine, but systemd creates a different execution context from a root shell. Before editing the unit file, gather evidence that shows exactly what context the service runs in and where the first failure occurs.

Below is a troubleshooting strategy you can use on RHEL. Assume the service is named `foo.service`; replace it with your unit name.

---

## 1. Preserve the failed state and collect logs first

Do **not** immediately edit the unit file, change `User=`, add environment variables, disable SELinux, or run repeated `systemctl start` attempts before capturing evidence. If the service has already failed, you may already have start-limit messages hiding the original cause.

Collect:

```bash
systemctl status foo.service --full --no-pager
systemctl show foo.service -p ActiveState -p SubState -p MainPID \
  -p ExecMainPID -p ExecMainCode -p ExecMainStatus -p Result
journalctl -u foo.service -b --no-pager --show-all -n 500
```

Also look at the previous boot if the failure happened before reboot or after repeated attempts:

```bash
journalctl -u foo.service -b -1 --no-pager
```

If the service is already in a start-limit state:

```bash
journalctl -u foo.service --since "1 hour ago" --no-pager -o verbose
```

Look for the **first** failure, not the repeated failures.

What evidence you want:

- Did systemd fail to start the process, or did the application start and exit?
- Was the failure immediate, or was it after a timeout?
- Did systemd report a status code?
- Was the process killed by a signal?
- Did the application produce any stdout/stderr in the journal?
- Were there AVC/SELinux denials around the same timestamp?

Common systemd-level result/status hints:

| Evidence | Meaning |
|---|---|
| `status=203/EXEC` | `ExecStart` path bad, binary missing, not executable, bad interpreter, filesystem mounted `noexec`, or exec denied. |
| `status=200/CHDIR` | `WorkingDirectory=` is missing, unreadable, or cannot be entered. |
| `status=217/USER` | `User=` is unknown or cannot be resolved. |
| `status=216/GROUP` | `Group=` is unknown or cannot be resolved. |
| `status=214/PATH` | Path lookup/execution path problem. |
| `status=211/START_LIMIT_HIT` | Too many fast failures; the real cause is in an earlier attempt. |
| Application exits with non-zero code | The binary ran but failed internally. |
| `Killed`, `dumped core`, `signal` | Killed by signal, possible OOM, core dump, timeout, or `ExecStop`/`KillMode` interaction. |
| `Result=timeout` | Service did not signal readiness, possibly wrong `Type=notify`, `sd_notify` issue, or long startup. |

---

## 2. Capture the effective unit definition, including drop-ins

The file you see in `/etc/systemd/system/foo.service` may not be the whole story. Drop-ins can silently change `ExecStart`, `Environment`, `User`, working directory, dependencies, and sandboxing.

```bash
systemctl cat foo.service --full
systemctl show foo.service -p FragmentPath -p DropPaths
```

If the unit is a symlink:

```bash
readlink -f "$(systemctl show foo.service -p FragmentPath --value)"
systemctl cat foo.service --full
```

Validate the unit file and dependencies without starting it:

```bash
systemd-analyze verify /etc/systemd/system/foo.service
systemd-analyze security foo.service
```

For expanded values and effective properties:

```bash
systemd-analyze dump foo.service | less
```

Important properties to inspect:

```bash
systemctl show foo.service -p Id,LoadState,UnitFileState,ActiveState,SubState
systemctl show foo.service -p ExecStart -p WorkingDirectory
systemctl show foo.service -p Environment -p EnvironmentFiles -p PassEnvironment
systemctl show foo.service -p User -p Group -p SupplementaryGroups
systemctl show foo.service -p Wants -p Requires -p After -p Before
systemctl show foo.service -p RequiresMountsFor -p BindsTo -p PartOf
systemctl show foo.service -p Type -p Restart -p RestartUSec
systemctl show foo.service -p StartLimitIntervalUSec -p StartLimitBurst
systemctl show foo.service -p ProtectSystem -p ProtectHome -p PrivateTmp
systemctl show foo.service -p PrivateDevices -p NoNewPrivileges
systemctl show foo.service -p CapabilityBoundingSet -p AmbientCapabilities
systemctl show foo.service -p StandardOutput -p StandardError
systemctl show foo.service -p TimeoutStartUSec -p TimeoutStopUSec
systemctl show foo.service -p NotifyAccess
```

Evidence you are looking for:

- Is `ExecStart=` using an absolute path?
- Are there `ExecStartPre=`, `ExecStartPost=`, or `ExecStopPre=` commands that could fail before or after the main executable?
- Does the unit contain `User=` that does not match root?
- Are there unexpected drop-ins?
- Are relative paths being used in `WorkingDirectory=`, `ExecStart=`, or application configuration?
- Does the unit use `EnvironmentFile=`?
- Does the unit set sandboxing options such as `ProtectSystem=`, `ProtectHome=`, `PrivateTmp=`, `NoNewPrivileges=`, or capability restrictions?
- Is the service `simple`, `forking`, `notify`, or `oneshot`? If the application daemonizes but the unit is wrong, the service can appear to fail immediately.

---

## 3. Compare manual root execution to systemd execution

Running as root manually is a very broad environment. It can have:

- a login shell,
- full root capabilities,
- a current working directory chosen by you,
- environment variables from `/etc/profile`, `/etc/bashrc`, `~/.bashrc`, module files, LDAP/Kerberos, Java/Python wrappers, etc.,
- direct access to `/root`, `/home`, `/tmp`, `/etc`, `/var`,
- an SELinux domain that may be different from the service domain.

The service may have none of those.

Record the manual invocation exactly:

```bash
command -v /path/to/executable
readlink -f /path/to/executable
file /path/to/executable
stat /path/to/executable
ls -lZ /path/to/executable
```

If the manual command was a script, inspect the interpreter:

```bash
head -n 1 /path/to/executable
```

Evidence you want:

- Is the executable really a binary, script, symlink, wrapper, or symbolic link to another mount?
- Does it require a shell, Python, Java, Ruby, Perl, or another interpreter?
- Does it depend on relative file paths?
- Does it require environment variables?
- Does it write to directories that require root, group membership, or special capabilities?

---

## 4. Verify user identity and process credentials

Even if the unit has no `User=`, the effective identity may still be restricted by capabilities, `NoNewPrivileges=`, namespace isolation, or SELinux. If it does have `User=`, this is one of the first things to check.

```bash
systemctl show foo.service -p User -p Group -p SupplementaryGroups
systemctl show foo.service -p NoNewPrivileges -p CapabilityBoundingSet
systemctl show foo.service -p AmbientCapabilities
```

Verify that the user and group actually exist:

```bash
getent passwd "$(systemctl show foo.service -p User --value)"
getent group "$(systemctl show foo.service -p Group --value)"
```

If the unit runs as a non-root user:

```bash
id -u
id
id "$(systemctl show foo.service -p User --value)"
groups "$(systemctl show foo.service -p User --value)"
```

If the service is running or stays alive long enough:

```bash
systemctl show foo.service -p MainPID --value
ps -f -p "$(systemctl show foo.service -p MainPID --value)"
ps -Z -p "$(systemctl show foo.service -p MainPID --value)"
cat "/proc/$(systemctl show foo.service -p MainPID --value)/status"
```

Evidence to gather:

- Manual run: UID 0, full capabilities, often broad access.
- Service run: possibly `User=appuser`, non-root UID, reduced capabilities.
- Does the service user have permission to:
  - execute the binary,
  - traverse parent directories,
  - read configuration files,
  - write logs,
  - bind privileged ports below 1024,
  - access `/etc`, `/var/lib`, `/run`, `/tmp`, `/root`, or mounted storage?
- Does the user exist in `/etc/passwd` or in a directory service that may be slow or unavailable during boot?
- Does the application assume `root` behavior such as writing to `/etc/foo`, using `/root/.ssh`, or accessing `/proc` data?
- Are there `SupplementaryGroups=` that the application relies on?
- Is `NoNewPrivileges=yes` preventing setuid/sudo/pam-style privilege changes?
- Are capabilities expected to be present but not granted?

A useful safe test, if the application has a harmless dry-run or help mode:

```bash
sudo -i -u "$(systemctl show foo.service -p User --value)" \
  /full/path/to/executable --dry-run
```

But do not treat this as equivalent to systemd; it still uses a login shell environment. Use it only to compare, not to conclude.

---

## 5. Capture the environment variables systemd actually uses

A root shell environment is not the same as a systemd service environment. systemd does **not** source `/etc/profile`, `/etc/bashrc`, `~/.bashrc`, `~/.profile`, login shell scripts, or interactive module files by default.

Capture the manual environment:

```bash
sudo bash -c 'env > /tmp/manual-root.env'
sudo -i -u appuser env > /tmp/manual-user.env
```

Inspect configured service environment:

```bash
systemctl show foo.service -p Environment
systemctl show foo.service -p EnvironmentFiles
systemctl show foo.service -p PassEnvironment
```

Check `EnvironmentFile=` files:

```bash
systemctl show foo.service -p EnvironmentFiles --value
```

If the unit says:

```ini
EnvironmentFile=/etc/sysconfig/foo
```

check that the file exists, is readable, and has the expected values:

```bash
ls -lZ /etc/sysconfig/foo
cat /etc/sysconfig/foo
```

If the file is missing, note whether the unit uses a leading `-`:

```ini
EnvironmentFile=-/etc/sysconfig/foo
```

Without the leading `-`, a missing environment file can cause the unit to fail to start.

To see the actual environment the service would get, run a harmless `env` command inside a transient systemd unit with the same user and working directory. This does not modify your existing unit file.

Example:

```bash
systemd-run --unit=envprobe-foo --collect \
  --property=Type=simple \
  --property=User="$(systemctl show foo.service -p User --value)" \
  --property=WorkingDirectory="$(systemctl show foo.service -p WorkingDirectory --value)" \
  --property=EnvironmentFile="$(systemctl show foo.service -p EnvironmentFiles --value)" \
  --property=StandardOutput=journal \
  --property=StandardError=journal \
  /usr/bin/env
```

Then:

```bash
journalctl -u envprobe-foo.service --no-pager
```

Compare against the manual environment.

Evidence to look for:

- `PATH` missing required directories.
- `HOME` not set or pointing to a different home directory.
- `TMPDIR` changed or private because of `PrivateTmp=yes`.
- `LD_LIBRARY_PATH`, `PYTHONPATH`, `PYTHONHOME`, `JAVA_HOME`, `CLASSPATH`, `PERL5LIB`, `GEM_PATH`, etc., missing.
- Application-specific variables such as `FOO_CONFIG`, `FOO_HOME`, `NODE_ENV`, `SPRING_CONFIG`, `APP_ENV`, `TZ`, `LANG`, `LC_ALL`.
- Kerberos ticket cache, LDAP variables, TLS certificate paths, or user-specific configuration paths.
- Differences caused by running as root vs. service user.

---

## 6. Verify the working directory

A manual run often happens in the application directory. A service may start in a completely different directory.

Check:

```bash
systemctl show foo.service -p WorkingDirectory
```

If `WorkingDirectory=` is missing, the effective working directory may be the root directory `/`, or a user home directory if `User=` is set. Relative paths will not behave like they do in your interactive shell.

Inspect the working directory:

```bash
WD="$(systemctl show foo.service -p WorkingDirectory --value)"
[ -d "$WD" ] || echo "WorkingDirectory is not a directory"
namei -l "$WD"
stat "$WD"
ls -lZ "$WD"
```

If the process stays alive briefly:

```bash
PID="$(systemctl show foo.service -p MainPID --value)"
ls -l "/proc/$PID/cwd"
```

Evidence to gather:

- Does the application require its own directory as the current directory?
- Does it load `./config/app.conf` rather than an absolute path?
- Does it write to `./data`, `./logs`, or `./cache`?
- Is `WorkingDirectory=` missing, wrong, relative, or not readable by the service user?
- Does the journal show `status=200/CHDIR`?

If your manual test was:

```bash
cd /opt/foo
./foo --config config/app.conf
```

but the service has no `WorkingDirectory=/opt/foo`, it may not find `config/app.conf`.

---

## 7. Validate filesystem paths and permissions

Check the exact `ExecStart=` target.

```bash
EXE="$(systemctl show foo.service -p ExecStart --value | awk '{print $1}')"
echo "$EXE"
```

If `EXE` is not absolute, that is already evidence of a problem.

Then inspect it:

```bash
ls -lZ "$EXE"
readlink -f "$EXE"
file "$EXE"
stat "$EXE"
namei -l "$EXE"
```

`namei -l` is especially useful because it shows every parent directory component and catches traversal permission problems.

```bash
namei -l /opt/foo/bin/foo
```

Check permissions and ownership:

```bash
stat -c '%A %U:%G %n' /opt/foo/bin/foo
getfacl /opt/foo/bin/foo
```

Check whether the path is on a filesystem that may be missing, read-only, or `noexec`.

```bash
findmnt -no TARGET,SOURCE,FSTYPE,OPTIONS "$(dirname "$EXE")"
findmnt -no TARGET,SOURCE,FSTYPE,OPTIONS /opt
findmnt -no TARGET,SOURCE,FSTYPE,OPTIONS /var
findmnt -no TARGET,SOURCE,FSTYPE,OPTIONS /usr
findmnt -no TARGET,SOURCE,FSTYPE,OPTIONS /usr/local
```

Evidence to gather:

- Is the executable on `/opt`, `/usr/local`, an NFS mount, an XFS mount, a bind mount, or a separately mounted data filesystem?
- Is the filesystem mounted `ro`, `noexec`, or with restrictive SELinux labels?
- Is the binary a symlink to another filesystem?
- Are parent directories inaccessible to the service user?
- Is the script’s interpreter accessible?
- Does the binary need shared libraries that are not available under the service’s environment?

Check dependencies if appropriate:

```bash
ldd "$EXE"
env -i /usr/bin/ldd "$EXE"
```

The second form removes your interactive environment, which may be closer to the service’s environment.

Also inspect sandbox-related options that affect filesystem visibility:

```bash
systemctl show foo.service -p ProtectSystem
systemctl show foo.service -p ProtectHome
systemctl show foo.service -p PrivateTmp
systemctl show foo.service -p PrivateDevices
systemctl show foo.service -p ReadWritePaths
systemctl show foo.service -p TemporaryFileSystem
```

Evidence:

- `ProtectHome=yes` hides `/home`, `/root`, and `/run/user`.
- `ProtectSystem=strict` makes `/usr`, `/boot`, and `/etc` read-only unless allowed via `ReadWritePaths=`.
- `PrivateTmp=yes` gives the service its own `/tmp`, so `/tmp/foo` in a root shell is not the same as `/tmp/foo` in the service.
- `PrivateDevices=yes` changes device visibility.
- `ProtectKernelModules=`, `ProtectKernelTunables=`, `ProtectControlGroups=` can restrict system behavior.

---

## 8. Inspect SELinux context and denials

On RHEL, SELinux is a common difference between a manual root run and a systemd-started service. A manual shell may run in a different SELinux domain than the service process. The service may also run with an unexpected file label or a label that does not permit execute/read/write operations.

Check SELinux mode:

```bash
getenforce
sestatus
```

Check labels:

```bash
ls -Z "$(systemctl show foo.service -p ExecStart --value | awk '{print $1}')"
ls -Z "$(systemctl show foo.service -p WorkingDirectory --value)"
ls -Z /etc/systemd/system/foo.service
```

Check for mismatches without changing anything:

```bash
restorecon -n -v /opt/foo/bin/foo
restorecon -n -v /etc/systemd/system/foo.service
```

Check for AVC denials:

```bash
ausearch -m avc -ts recent -i
ausearch -m avc -ts recent -sv no -i
journalctl -t setroubleshoot -b --no-pager
journalctl --since "1 hour ago" -k -t audit -t setroubleshoot --no-pager
```

If the process stays alive briefly:

```bash
PID="$(systemctl show foo.service -p MainPID --value)"
ps -Z -p "$PID"
```

Evidence you want:

- Was SELinux enforcing, permissive, or disabled?
- Are AVC denials present around the failure timestamp?
- What denied access is requested?
  - `execute` on the executable,
  - `read` on configuration,
  - `write` on logs,
  - `open` on sockets or files,
  - `search` on parent directories,
  - `getattr` on files.
- What is the source SELinux type of the process?
- What is the target type of the file?
- Is the executable labeled unexpectedly, for example `httpd_sys_content_t`, `var_t`, `unlabeled_t`, `user_home_t`, or another non-executable context?
- Did the manual root run occur in an unconfined domain while the service runs in a confined service domain?
- Does the service need a custom SELinux policy module or file context adjustment?

Important: collect this evidence before running tools like `restorecon`, changing SELinux booleans, setting permissive mode, or creating policy modules.

---

## 9. Check dependencies, ordering, and mount readiness

If the service depends on network, storage, databases, mounts, targets, or other units, the first failure may be caused by missing ordering or a dependency failing before the executable even gets a chance to start.

Inspect dependencies:

```bash
systemctl list-dependencies foo.service
systemctl show foo.service -p Requires -p Wants -p After -p Before
systemctl show foo.service -p RequiresMountsFor -p BindsTo -p PartOf
systemd-analyze verify /etc/systemd/system/foo.service
```

Look for failed or inactive dependency units:

```bash
systemctl list-units --type=service --state=failed --no-pager
systemctl list-units --type=mount --state=failed --no-pager
systemctl list-units --type=target --state=failed --no-pager
```

Check relevant target and mount units:

```bash
systemctl status network.target
systemctl status network-online.target
systemctl status remote-fs.target
systemctl status "$(findmnt -o TARGET | grep -F /opt)" || true
```

Evidence to gather:

- Does the unit use `After=network.target` when the application actually needs `network-online.target`?
- Does it depend on a mount that is not ready?
- Does `RequiresMountsFor=` cover the filesystem containing the binary or data directory?
- Is the service starting before LDAP, Kerberos, DNS, a database, a container runtime, or a socket unit?
- Are there `Requires=` dependencies that fail first?
- Are there circular dependencies or jobs that systemd reports?
- Is the failure caused by `ExecStartPre=` failing before `ExecStart=`?

---

## 10. Capture application-specific evidence without editing the unit

If the process starts but fails immediately, systemd may show only a generic failure. You need the application’s own failure reason.

First, check where its output goes:

```bash
systemctl show foo.service -p StandardOutput -p StandardError
```

If the application normally logs to stderr, the journal should contain it. If it logs to a file, syslog, a database, or another sink, check that file too.

Look for:

```bash
journalctl -u foo.service -b --no-pager
journalctl -u foo.service --since "1 hour ago" -o verbose --no-pager
```

If safe, run the executable with increased verbosity using its own supported flags, for example:

```bash
systemd-run --unit=runprobe-foo --collect \
  --property=Type=simple \
  --property=User="$(systemctl show foo.service -p User --value)" \
  --property=WorkingDirectory="$(systemctl show foo.service -p WorkingDirectory --value)" \
  --property=StandardOutput=journal \
  --property=StandardError=journal \
  /full/path/to/executable --dry-run
```

Then inspect:

```bash
journalctl -u runprobe-foo.service --no-pager
```

If the application is simple enough, use `strace` under a transient systemd unit to capture the first failed `open()` or `execve()` call:

```bash
systemd-run --unit=straceprobe-foo --collect \
  --property=Type=simple \
  --property=WorkingDirectory="$(systemctl show foo.service -p WorkingDirectory --value)" \
  --property=StandardOutput=journal \
  --property=StandardError=journal \
  /usr/bin/strace -f -tt -o /tmp/foo.strace \
  /full/path/to/executable --dry-run
```

Then inspect:

```bash
tail -n 200 /tmp/foo.strace
```

This can reveal missing files, wrong relative paths, permission failures, or denied sockets before the application exits.

Evidence to gather:

- First failed `openat()` path.
- Whether the path exists.
- Whether it is being opened relative to the wrong working directory.
- Whether it is being denied by DAC permissions.
- Whether it is being denied by SELinux.
- Whether the binary is trying to access `/tmp`, `/run`, `/proc`, `/sys`, `/dev`, `/etc`, or a home directory that systemd has restricted.

---

## 11. Decide what kind of failure you are dealing with

Based on evidence, classify the problem.

### Case A: systemd could not launch the process

Evidence:

- `status=203/EXEC`
- `status=200/CHDIR`
- `status=217/USER`
- `status=216/GROUP`
- `status=214/PATH`
- No application output at all
- `ExecStart=` points to a missing or bad file

Likely evidence to focus on:

- `ExecStart=` absolute path,
- `WorkingDirectory=`,
- file permissions,
- `namei -l`,
- mount options,
- `file`,
- interpreter path,
- SELinux exec label.

---

### Case B: the process started but exited immediately

Evidence:

- Application output appears in journal,
- exit code is non-zero,
- application-specific log entries exist,
- process may appear briefly in `systemctl status` or journal.

Likely evidence to focus on:

- environment variables,
- current working directory,
- relative config paths,
- missing files,
- permission denied errors,
- SELinux denials,
- service user group membership.

---

### Case C: the service times out or is killed

Evidence:

- `Result=timeout`
- `Result=signal`
- `Killed`
- service starts but never reports readiness
- `Type=notify` with no `sd_notify`
- `Type=forking` but binary behavior is not standard fork behavior

Likely evidence to focus on:

- `Type=`,
- `TimeoutStartUSec=`,
- `NotifyAccess=`,
- `ExecStartPost=`,
- whether the application daemonizes,
- whether the application waits for network/storage,
- whether the application sends readiness notification.

---

### Case D: SELinux denies the application

Evidence:

- AVC messages,
- `setroubleshoot` entries,
- `denied { read write execute open search }`,
- unusual file labels,
- service works in permissive mode or as a different SELinux domain.

Do not conclude SELinux is safe to disable. Gather:

```bash
getenforce
ls -Z /path/to/executable
ausearch -m avc -ts recent -i
journalctl -t setroubleshoot -b --no-pager
```

The key evidence is the denial record, source process type, target file type, and requested permission.

---

## 12. Evidence package to collect before changing the unit file

Before touching `/etc/systemd/system/foo.service`, gather this information:

### Service status and failure result

```bash
systemctl status foo.service --full --no-pager
systemctl show foo.service -p ActiveState -p SubState -p MainPID \
  -p ExecMainPID -p ExecMainCode -p ExecMainStatus -p Result
```

### Journal

```bash
journalctl -u foo.service -b --no-pager
journalctl -u foo.service -b -1 --no-pager
journalctl --since "1 hour ago" -u foo.service --no-pager
journalctl -k -t audit -t setroubleshoot --since "1 hour ago" --no-pager
```

### Unit definition and effective properties

```bash
systemctl cat foo.service --full
systemctl show foo.service -p FragmentPath -p DropPaths
systemd-analyze verify /etc/systemd/system/foo.service
systemd-analyze security foo.service
systemd-analyze dump foo.service | less
```

### Runtime context

```bash
systemctl show foo.service -p User -p Group -p SupplementaryGroups
systemctl show foo.service -p WorkingDirectory
systemctl show foo.service -p Environment -p EnvironmentFiles
systemctl show foo.service -p ProtectSystem -p ProtectHome -p PrivateTmp
systemctl show foo.service -p NoNewPrivileges -p CapabilityBoundingSet
systemctl show foo.service -p StandardOutput -p StandardError
```

### Filesystem and permissions

```bash
EXE="$(systemctl show foo.service -p ExecStart --value | awk '{print $1}')"
ls -lZ "$EXE"
namei -l "$EXE"
findmnt -no TARGET,SOURCE,FSTYPE,OPTIONS "$(dirname "$EXE")"
findmnt -no TARGET,SOURCE,FSTYPE,OPTIONS /opt 2>/dev/null || true
```

### SELinux

```bash
getenforce
sestatus
ls -Z "$(systemctl show foo.service -p ExecStart --value | awk '{print $1}')"
ls -Z "$(systemctl show foo.service -p WorkingDirectory --value)"
restorecon -n -v "$(systemctl show foo.service -p ExecStart --value | awk '{print $1}')"
ausearch -m avc -ts recent -i
journalctl -t setroubleshoot -b --no-pager
```

### Dependencies

```bash
systemctl list-dependencies foo.service
systemctl show foo.service -p Requires -p Wants -p After -p Before
systemctl show foo.service -p RequiresMountsFor
systemctl list-units --state=failed --no-pager
```

---

## Practical rule

Before changing the unit file, you should be able to write a statement like:

> The unit starts `/absolute/path/to/executable` as user `appuser`, with working directory `/some/dir`, with this environment and these sandbox restrictions. The first failure is evidenced by journal entry X / status Y / AVC denial Z / permission error W. The difference from the manual root launch is A, B, and C.

Only then should you make a targeted unit-file change.
