Treat this as a **context mismatch** problem: the command works in an interactive root shell, but systemd starts it in a different process context. Before editing the unit file, gather evidence that tells you whether the failure happens:

1. **Before execve()** — systemd cannot start the process: missing path, bad shebang, permission denial, SELinux denial, `noexec` mount, bad executable format.
2. **During pre-exec/start hooks** — `ExecStartPre=`, sandbox/security settings, working-directory failure, etc.
3. **After the process starts** — the program exits nonzero because of missing environment variables, relative paths, permissions, dependencies, etc.
4. **Because of unit semantics** — `Type=` mismatch, dependency ordering, missing daemon notification, incorrect restart behavior.
5. **Because of policy/security** — SELinux, Linux capabilities, systemd sandbox options, cgroup limits.

Below is a practical RHEL-oriented troubleshooting strategy and the evidence to collect before changing the unit file.

---

## 0. Pick the unit name and preserve the current state

Assume the failing unit is:

```bash
UNIT=example.service
```

First capture the current state before making any change.

```bash
systemctl status "$UNIT" -l --full --no-pager
systemctl is-failed "$UNIT"
systemctl is-enabled "$UNIT"
systemctl is-active "$UNIT"
systemctl is-enabled --all | grep -F "$UNIT" || true
```

Also capture the effective unit definition, including drop-ins:

```bash
systemctl cat -a "$UNIT" --no-pager
```

Then capture the effective runtime properties:

```bash
systemctl show "$UNIT" --all --no-pager > /tmp/${UNIT}.show.txt
```

Or capture the most relevant properties:

```bash
systemctl show "$UNIT" \
  --property=Name \
  --property=LoadState \
  --property=ActiveState \
  --property=SubState \
  --property=UnitFileState \
  --property=MainPID \
  --property=ExecMainPID \
  --property=Result \
  --property=ExecMainCode \
  --property=ExecMainStatus \
  --property=ExecStart \
  --property=Type \
  --property=User \
  --property=Group \
  --property=SupplementaryGroups \
  --property=WorkingDirectory \
  --property=Environment \
  --property=EnvironmentFiles \
  --property=NoNewPrivileges \
  --property=ProtectSystem \
  --property=ProtectHome \
  --property=PrivateTmp \
  --property=PrivateDevices \
  --property=PrivateNetwork \
  --property=PrivateUsers \
  --property=CapabilityBoundingSet \
  --property=AmbientCapabilities \
  --property=LimitNOFILE \
  --property=LimitNPROC \
  --property=Restart \
  --property=NRestarts \
  --property=StartLimitRate \
  --property=After \
  --property=Wants \
  --property=Requires \
  --property=BindsTo \
  --property=Conflicts \
  --no-pager > /tmp/${UNIT}.props.txt
```

Why this matters: `systemctl cat` shows files, but `systemctl show` shows the **effective** configuration after drop-ins and defaults. Do not assume the unit file text is the whole story.

---

## 1. Determine exactly where the start failed

The most important question is: did the process ever start?

Check:

```bash
systemctl status "$UNIT" -l --full --no-pager
systemctl show "$UNIT" \
  --property=MainPID \
  --property=ExecMainPID \
  --property=ExecMainCode \
  --property=ExecMainStatus \
  --property=Result \
  --no-pager
```

Typical evidence patterns:

| Evidence | Likely meaning |
|---|---|
| `MainPID=` / `ExecMainPID=` is empty | systemd likely failed to spawn the process |
| `ExecMainCode=CLD_EXITED` and `ExecMainStatus=1` | the program ran and exited with code 1 |
| `ExecMainCode=CLD_KILLED` | process was killed by a signal, possibly OOM, timeout, or external kill |
| `Result=exec-format` | bad binary format, bad shebang, or similar exec problem |
| `Result=exec-permission` or message mentions `EACCES` | permission/SELinux/noexec/exec permission problem |
| `status=203/EXEC` | systemd failed to execute the command |
| `status=127` | command not found, often a PATH or shell interpretation issue |
| `status=126` | command found but not executable/invocable |
| `Result=protocol` | service behavior does not match `Type=` semantics |
| `Result=start-limit-hit` | repeated fast failures triggered restart limiting |
| `Result=resources` | cgroup/resource or OOM issue |
| `Result=timeout` | start/stop timeout or missing daemon notification |

For immediate failure, pay special attention to messages such as:

```text
Failed to execute command: Permission denied
Failed to execute command: No such file or directory
Failed to spawn process: Permission denied
Failed to find working directory ...
Failed to load environment file ...
status=203/EXEC
status=EACCES
```

Gather the unit journal around the failure:

```bash
journalctl -u "$UNIT" --no-pager --full -b
```

Also collect the systemd manager messages:

```bash
journalctl --identifier=systemd --no-pager --full -b | grep -F "$UNIT"
```

If it failed recently:

```bash
journalctl -u "$UNIT" --since "-10 minutes" --no-pager --full
```

If you need a machine-readable log:

```bash
journalctl -u "$UNIT" --output=json-pretty --no-pager
```

Also check whether the unit failed before or after its `ExecStartPre=` or `ExecStartPost=` hooks:

```bash
systemctl show "$UNIT" \
  --property=ExecMainPID \
  --property=ExecMainCode \
  --property=ExecMainStatus \
  --no-pager

journalctl -u "$UNIT" --no-pager --full -b | grep -iE "execstart|execstartpre|execstartpost|failed"
```

---

## 2. Check user identity and privilege context

Manual execution as root is not the same as root under systemd. The service may be running as a different user, or it may have fewer privileges even as root.

Evidence to gather:

```bash
systemctl show "$UNIT" --property=User --property=Group --property=SupplementaryGroups --no-pager
```

If the process starts briefly, check the actual running process:

```bash
systemctl status "$UNIT" -l --full --no-pager
ps -eo pid,ppid,user,args,cmd | grep -F "${UNIT}" || true
```

If the service is short-lived, capture `ps` quickly in a loop while restarting it manually, or reproduce safely with a transient unit if appropriate.

Check whether the unit is a system service or a user service:

```bash
systemctl status "$UNIT" --full --no-pager
systemctl --user status "$UNIT" --full --no-pager 2>/dev/null || true
```

Interpretation:

- If the unit has `User=appuser` or `User=someuser`, then the executable and files it touches must be accessible by that user.
- If it has `Group=` but not `SupplementaryGroups=`, the process may lack group access that root had.
- If `User=` is not set, the service still runs as root, but systemd may apply security restrictions such as `NoNewPrivileges=`, `ProtectSystem=`, `PrivateTmp=`, `PrivateDevices=`, or SELinux constraints.
- If `User=root` works manually but the unit has `User=nonroot`, permission failures become likely.

Also check security-related properties:

```bash
systemctl show "$UNIT" \
  --property=NoNewPrivileges \
  --property=ProtectSystem \
  --property=ProtectHome \
  --property=ProtectKernelModules \
  --property=ProtectKernelTunables \
  --property=ProtectControlGroups \
  --property=PrivateTmp \
  --property=PrivateDevices \
  --property=PrivateNetwork \
  --property=PrivateUsers \
  --property=CapabilityBoundingSet \
  --property=AmbientCapabilities \
  --no-pager
```

Run the systemd security analyzer if useful:

```bash
systemd-analyze security "$UNIT"
```

Evidence of cause:

- `User=` differs from root but executable or dependencies are root-owned.
- Process is root but `ProtectSystem=` makes paths read-only.
- `PrivateTmp=` changes where `/tmp` is located; an app that expects shared `/tmp` files may fail.
- `CapabilityBoundingSet=` removes capabilities that the root shell had.

---

## 3. Compare the environment manually

An interactive shell has an environment inherited from login/profile files. systemd does not automatically inherit your shell environment.

Check the unit’s effective environment:

```bash
systemctl show "$UNIT" --property=Environment --property=EnvironmentFiles --no-pager
```

Check the unit’s working directory:

```bash
systemctl show "$UNIT" --property=WorkingDirectory --no-pager
```

If you need to compare the systemd-managed environment without editing the unit, you can use a transient scope or service. Example using a transient scope:

```bash
systemd-run --scope --quiet env
```

To test a specific user and working directory without editing the original unit:

```bash
systemd-run --scope --quiet \
  --property=WorkingDirectory=/path/to/service/workdir \
  pwd
```

To view environment as systemd would usually present it:

```bash
systemd-run --scope --quiet env > /tmp/systemd-run-env.txt
```

Compare that with your manual root shell:

```bash
env > /tmp/manual-root-env.txt
```

Look for missing variables such as:

```text
PATH
LD_LIBRARY_PATH
PYTHONPATH
PYTHONHOME
JAVA_HOME
CLASSPATH
RUBYLIB
NODE_PATH
DBUS_SESSION_BUS_ADDRESS
DISPLAY
HOME
LANG
LC_*
```

Pay special attention to `PATH`. The systemd default environment may not include all the places where your command lives.

Evidence of cause:

- The unit uses a bare command such as `ExecStart=myapp`.
- The command works in your shell because `PATH` contains it.
- systemd’s effective `PATH` does not contain it.
- The unit’s `Environment=` or `EnvironmentFile=` is missing required variables.
- The app needs `HOME`, config paths, or library paths that are only set in a login shell.

Also check whether the service needs an environment file:

```bash
systemctl show "$UNIT" --property=EnvironmentFiles --no-pager
```

If an `EnvironmentFile=` is configured, verify that the file exists, has correct permissions, and is not empty:

```bash
envfile=$(systemctl show "$UNIT" --property=EnvironmentFiles --value | cut -d' ' -f1)
[ -n "$envfile" ] && ls -lZ "$envfile"
```

Be careful: the exact parsing of `EnvironmentFile=` can include multiple entries; use `systemctl status` logs to confirm whether systemd failed to load it.

---

## 4. Check the working directory

A common difference between manual execution and systemd is the current working directory.

Manual test:

```bash
pwd
```

Check the unit’s working directory:

```bash
systemctl show "$UNIT" --property=WorkingDirectory --no-pager
```

If `WorkingDirectory=` is missing, systemd may use `/` as the default working directory depending on configuration.

Test in a systemd-managed context:

```bash
systemd-run --scope --quiet --property=WorkingDirectory=/app pwd
```

Check the directory itself:

```bash
stat -c '%n %A %U:%G' /app
ls -ldZ /app
namei -lx /app
```

Interpretation:

- If the program uses relative paths for config files, logs, templates, plugins, certificates, or data files, it may fail under systemd if the working directory is different.
- If `WorkingDirectory=` is set to a directory that does not exist, systemd may fail the service.
- If the working directory is on a filesystem with restrictive permissions, the service user may not be able to enter it.

Evidence of cause:

- Service log mentions files like `config.yaml`, `./data/foo`, or relative paths.
- It works when you run it from `/opt/app` but fails when started by systemd.
- `WorkingDirectory=` is not set or points to a path inaccessible to the service user.

---

## 5. Verify the exact executable path and filesystem details

Check the effective command:

```bash
systemctl show "$UNIT" --property=ExecStart --no-pager
systemctl status "$UNIT" --full --no-pager
```

Important detail: **systemd normally does not use a shell to run `ExecStart=`**. It uses `execve()`-style execution. Therefore, shell syntax such as:

```bash
ExecStart=command arg > /var/log/foo
ExecStart=command | tee output
ExecStart=command &
ExecStart=~user/bin/command
ExecStart=$HOME/bin/command
```

may behave differently from a manual shell invocation.

Evidence to gather for the executable path:

```bash
execpath=$(systemctl show "$UNIT" --property=ExecStart --value | awk '{print $1}')
echo "$execpath"
```

If `ExecStart=` starts with a shell:

```bash
systemctl show "$UNIT" --property=ExecStart --no-pager
```

Then inspect the command string separately.

Check the file itself:

```bash
stat -c '%n %A %U:%G %s %i' "$execpath"
ls -lZ "$execpath"
namei -lx "$execpath"
readlink -f "$execpath"
file "$execpath"
head -c 200 "$execpath"
```

If the executable is a script, verify the interpreter in the shebang:

```bash
head -n 1 "$execpath"
```

Example:

```bash
#!/usr/bin/env python3
```

Then verify the interpreter:

```bash
ls -lZ /usr/bin/env
ls -lZ "$(command -v python3)"
```

Check for a bad shebang such as:

```bash
#!/usr/bin/python
```

when only `python3` exists.

Check dynamic library dependencies if it is a binary:

```bash
ldd "$execpath"
```

Check for missing interpreter or missing libraries.

Check mount options:

```bash
findmnt -T "$execpath" -o TARGET,SOURCE,FSTYPE,OPTIONS
```

Look for `noexec`.

Also check the working directory and data directories:

```bash
findmnt -T "$(systemctl show "$UNIT" --property=WorkingDirectory --value)" -o TARGET,SOURCE,FSTYPE,OPTIONS
```

Interpretation:

- If `ExecStart=` is a relative path, the result depends on how systemd resolves it and the service’s working directory. Use an absolute path for clarity, but do not change it until evidence supports that.
- If the path resolves through a symlink, confirm the final target.
- If the filesystem is mounted `noexec`, execution will fail.
- If the executable is a script with a bad interpreter, systemd may fail before the script logic runs.
- If `ldd` reports missing libraries, the manual root environment may differ from the service environment.

---

## 6. Check POSIX permissions and file ownership

Manual root execution can hide permission problems because root bypasses many normal file checks.

Check the executable:

```bash
stat -c '%n %A %U:%G' "$execpath"
ls -lZ "$execpath"
```

Check the directory hierarchy:

```bash
namei -lx "$execpath"
```

Check ACLs:

```bash
getfacl "$execpath"
```

Check directories used by the service:

```bash
wd=$(systemctl show "$UNIT" --property=WorkingDirectory --value)
if [ -n "$wd" ]; then
  ls -ldZ "$wd"
  getfacl "$wd"
fi
```

If the service user is non-root:

```bash
serviceuser=$(systemctl show "$UNIT" --property=User --value)
serviceuser=${serviceuser:-root}
id "$serviceuser"
```

Then test access as that user if safe:

```bash
sudo -u "$serviceuser" test -x "$execpath" && echo "executable" || echo "not executable"
sudo -u "$serviceuser" test -d "$wd" && echo "cwd readable" || echo "cwd not readable"
```

Be careful running arbitrary commands as another user. Prefer `test` checks rather than launching the program as that user unless needed.

Evidence of cause:

- Executable lacks `x` bit for the service user.
- Parent directory lacks `x`/`r` for the service user.
- Configuration or log files are root-owned and unwritable.
- Service user is not in the required supplementary group.
- File system is exported with restrictive options, for example NFS root squash or `noexec`.

---

## 7. Check SELinux on RHEL

SELinux is a major candidate on RHEL. The same executable can run from a manual root shell because your shell is usually in a less confined domain than a systemd service process. A custom service may be in `unconfined_service_t`, `httpd_t`, `usr_t`, `bin_t`, or another domain, and SELinux can deny access even to root.

Evidence to collect:

```bash
getenforce
sestatus
```

Check SELinux context of the executable:

```bash
ls -Z "$execpath"
```

Check SELinux context of relevant directories:

```bash
ls -ldZ /opt
ls -ldZ /opt/app
ls -ldZ /var/log
ls -ldZ /var/lib/app
```

If the process starts briefly:

```bash
ps -eZ | grep -F "${UNIT}" || true
```

Check for recent AVC denials:

```bash
ausearch -m avc -ts recent
```

Also check:

```bash
journalctl -k --no-pager -b | grep -iE 'avc|selinux|denied'
journalctl -b --no-pager --full | grep -iE 'avc|selinux|denied'
```

Check a specific executable or path:

```bash
ausearch -m avc -ts recent --file "$execpath"
ausearch -m avc -ts recent -i
```

If AVC denials exist, examine the source and target contexts, the object class, and the denied permission. Example evidence would be:

```text
type=AVC msg=... avc: denied { read } for pid=... comm="example" name="foo.conf" scontext=system_u:system_r:unconfined_service_t:s0 tcontext=unconfined_u:object_r:etc_t:s0 tclass=file
```

This is stronger evidence than simply assuming the service needs `PermissiveMode`.

Also check SELinux booleans that may matter, depending on the service domain:

```bash
getsebool -a | grep -i -E 'httpd|nfs|ssh|http' || true
```

Check relevant policy for the type if needed:

```bash
sesearch -A -s unconfined_service_t -t etc_t -c file -p read || true
```

Do not disable SELinux as a diagnostic step unless this is a controlled lab environment. If SELinux is the cause, the fix should be labels, booleans, or policy, not blindly editing the systemd unit.

Evidence of cause:

- AVC denials occur exactly when the service starts.
- The executable or data directory has an incorrect SELinux type.
- Manual root runs as `unconfined_t` while the service runs in a more restricted domain.
- The file has an unexpected context such as `httpd_sys_content_t` or `bin_t` when the service expects another type.
- SELinux is `Enforcing`, and the failure disappears only if temporarily put into `Permissive` during controlled testing — but even then the evidence is the AVCs.

---

## 8. Check dependencies and ordering

Even an immediate failure can be caused by a missing dependency. The executable may exist, but its required runtime environment may not be ready.

Check dependencies:

```bash
systemctl list-dependencies "$UNIT"
systemctl list-dependencies --reverse "$UNIT"
```

Check effective ordering and requirements:

```bash
systemctl show "$UNIT" \
  --property=After \
  --property=Wants \
  --property=Requires \
  --property=BindsTo \
  --property=Conflicts \
  --no-pager
```

Check critical chain:

```bash
systemd-analyze critical-chain "$UNIT"
systemd-analyze blame
```

Check whether required targets or mounts are active:

```bash
systemctl status network.target --full --no-pager
systemctl status network-online.target --full --no-pager
systemctl status remote-fs.target --full --no-pager
```

If the service depends on a mount:

```bash
systemctl status mnt-data.mount --full --no-pager
```

Evidence to capture:

- The service reads a file under `/data`, but `/data` is an NFS or remote mount that is not ready.
- The service requires networking but only `After=network.target` is configured; `network.target` does not mean the network is fully online.
- A database or message broker service is not active, but the custom service exits immediately.
- The service requires a socket activation file descriptor but the socket unit is not active.

Important distinction:

- `After=` controls ordering, not availability.
- `Requires=` or `BindsTo=` can enforce dependency, but if the dependency fails or is missing, the dependent unit may fail or be stopped.
- `Wants=` requests a dependency but does not force failure if it is absent.

Evidence of cause:

- `systemctl list-dependencies` shows missing required units.
- `systemd-analyze critical-chain` shows dependency delay or failure.
- Journal shows another service failed just before this one.
- The program fails because a socket, database, mount, or network path is unavailable.

---

## 9. Check unit type and start semantics

Immediate failure can also come from the service type being wrong.

Check:

```bash
systemctl show "$UNIT" --property=Type --no-pager
systemctl cat -a "$UNIT" --no-pager
```

Interpretation:

- `Type=simple`: systemd assumes the process started by `ExecStart=` is the main process. If it exits quickly, systemd marks failure.
- `Type=notify`: the program is expected to send readiness notification via `sd_notify`. If it does not, start can time out or fail.
- `Type=forking`: parent is expected to exit and leave a child; main PID detection may be wrong.
- `Type=oneshot`: the process is expected to exit, often successfully.
- `Type=dbus`: service is expected to appear on D-Bus.
- `Type=exec`: stricter than `simple`; it can fail if `ExecStart=` cannot be executed, without the same exec failure semantics as `simple`.

Check restart behavior:

```bash
systemctl show "$UNIT" --property=Restart --property=NRestarts --property=StartLimitRate --no-pager
```

Evidence of cause:

- A daemonizing program is configured as `Type=simple` and exits immediately after forking.
- A program using `sd_notify()` is configured as `Type=simple` instead of `Type=notify`.
- The service restarts rapidly and hits `StartLimit`.
- The program is expected to stay alive but exits quickly due to missing config or environment.

---

## 10. Collect kernel and resource evidence

Some immediate failures are not visible in the unit log alone.

Check kernel messages:

```bash
journalctl -k --no-pager -b
journalctl -k --no-pager --since "-10 minutes"
```

Look for:

```text
Out of memory
oom-kill
killed process
segfault
core dumped
```

Check for core dumps:

```bash
coredumpctl list "$UNIT"
coredumpctl info "$UNIT"
```

Check cgroup resource properties:

```bash
systemctl show "$UNIT" --property=MemoryLimit --property=MemoryCurrent --property=MemoryMax --property=MemoryHigh --property=TasksMax --property=LimitNOFILE --property=LimitNPROC --no-pager
```

On newer systemd, resource limits may appear under different properties such as `MemoryMax`, `MemoryHigh`, `TasksMax`, etc.

Evidence of cause:

- Kernel logs show OOM kill.
- `systemctl status` reports signal 9.
- Journal shows watchdog timeout.
- Program segfaults immediately.
- `coredumpctl` finds a crash from the service.

---

## 11. Check whether `ExecStartPre=` or sandbox setup fails

If the unit has pre-exec hooks, the failure may happen before the main executable.

Look in the journal:

```bash
journalctl -u "$UNIT" --no-pager --full -b | grep -iE "execstartpre|execstart|failed|exit"
```

Check the configuration:

```bash
systemctl show "$UNIT" --property=ExecStartPre --property=ExecStartPost --no-pager
```

Also check security-related failures:

```bash
systemd-analyze security "$UNIT"
```

Evidence of cause:

- `ExecStartPre=` command fails.
- `WorkingDirectory=` does not exist.
- `EnvironmentFile=` cannot be read.
- `ProtectSystem=` or `ProtectHome=` makes a required path read-only.
- A required file descriptor or socket is not present before main execution.

---

## 12. Compare manual execution with a safe systemd-like reproduction

Before editing the unit, you may want to reproduce the systemd context without changing the original unit. Do this only in a safe test environment or when the command is harmless.

First inspect the effective command:

```bash
systemctl show "$UNIT" --property=ExecStart --no-pager
```

Then test the working directory and environment under systemd:

```bash
wd=$(systemctl show "$UNIT" --property=WorkingDirectory --value)
[ -z "$wd" ] && wd="/"

systemd-run --scope --quiet --property=WorkingDirectory="$wd" env
```

If the unit uses a specific user:

```bash
user=$(systemctl show "$UNIT" --property=User --value)
user=${user:-root}
systemd-run --scope --quiet --property=User="$user" id
```

If you need to test a read-only diagnostic, such as checking PATH or cwd, you can run:

```bash
systemd-run --scope --quiet --property=WorkingDirectory="$wd" sh -c 'pwd; env | sort'
```

Do not run the actual application under arbitrary conditions unless you understand its side effects.

Evidence of cause:

- The command works manually from `/opt/app` but fails under `systemd-run` because the working directory or environment differs.
- The user used by systemd cannot access a required file.
- The PATH under systemd does not find the binary.

---

## 13. Check for unit-file quoting and shell interpretation issues

systemd does not run `ExecStart=` through a shell by default. If the unit contains shell constructs, the behavior can differ.

Check:

```bash
systemctl show "$UNIT" --property=ExecStart --no-pager
systemctl cat -a "$UNIT" --no-pager
```

Look for:

```text
>
|
&
~
$
`
*
?
&&
||
;
```

Examples:

```ini
ExecStart=/bin/myapp > /var/log/myapp.log
ExecStart=/bin/myapp | tee /var/log/myapp.log
ExecStart=/usr/bin/python3 script.py --config ~/.config/app.conf
```

These require a shell such as:

```ini
ExecStart=/bin/sh -c '/bin/myapp > /var/log/myapp.log'
```

Evidence of cause:

- The command works manually because your shell interprets redirection, globbing, `~`, or `$VAR`.
- systemd reports `status=203/EXEC`, file-not-found, or permission denied because it is trying to exec a literal token.
- The command string contains shell metacharacters but no `sh -c` wrapper.

Do not add `sh -c` before collecting evidence; just record that the command uses shell syntax and the journal indicates an execution failure.

---

## 14. Filesystem path and relative-path evidence

If the service starts but exits, it may be failing to open relative files.

Evidence to collect:

```bash
wd=$(systemctl show "$UNIT" --property=WorkingDirectory --value)
execpath=$(systemctl show "$UNIT" --property=ExecStart --value | awk '{print $1}')
systemctl status "$UNIT" --full --no-pager
journalctl -u "$UNIT" --no-pager --full -b
```

Look for missing paths in logs such as:

```text
config not found
No such file or directory
Unable to open ...
Permission denied
Failed to open ...
```

Check likely relative paths:

```bash
pwd
ls -la
find . -maxdepth 1 -type f
```

If the service uses templates, plugins, certificates, or configuration files, inspect those paths as well.

Evidence of cause:

- Manual execution happens from the application directory.
- systemd uses `/` or another working directory.
- The program fails because it cannot find relative files.
- The service uses absolute paths manually, but the unit or app expects a different base directory.

---

## 15. Gather SELinux audit evidence specifically before considering unit changes

On RHEL, this deserves its own focus.

Use:

```bash
getenforce
ausearch -m avc -ts recent
journalctl -k --no-pager --since "-15 minutes" | grep -i avc
```

If you know the executable path:

```bash
ausearch -m avc -ts recent --file /path/to/executable
ausearch -m avc -ts recent -i
```

If the service writes logs:

```bash
ausearch -m avc -ts recent --dir /var/log/app
```

If the service reads config:

```bash
ausearch -m avc -ts recent --file /etc/app.conf
```

Check contexts:

```bash
ls -Z /path/to/executable
ls -Z /etc/app.conf
ls -ldZ /var/log/app
ls -ldZ /var/lib/app
```

Check the service domain when running:

```bash
ps -eZ | grep -F "${UNIT}"
```

Evidence of cause:

- AVC denial occurs exactly at service start.
- `Permissive` mode temporarily hides the failure, but the real evidence should be the AVC record.
- A file has an unexpected SELinux type, for example:

```text
-rwxr-xr-x. root root unconfined_u:object_r:usr_t:s0 /opt/app/bin/app
```

when the service or policy expects something else.

Important: If SELinux is the cause, the appropriate evidence-backed fix may be:

- correct file context,
- correct service type/domain,
- SELinux boolean,
- policy module,
- label the unit file correctly.

It may not require changing `User=`, `WorkingDirectory=`, or `Environment=`.

---

## 16. Use journal logs to correlate failure with dependencies

The journal can show whether another unit failed immediately before this service.

Collect:

```bash
journalctl -b --no-pager --full --since "-10 minutes" | less
journalctl --identifier=systemd --no-pager --full -b | grep -F "$UNIT"
journalctl -u "$UNIT" --no-pager --full -b
journalctl -u network.target --no-pager --full -b
journalctl -u remote-fs.target --no-pager --full -b
```

Also check previous boot if relevant:

```bash
journalctl -b -1 -u "$UNIT" --no-pager --full
```

Evidence of cause:

- Another required unit is in a failed state.
- A mount is missing.
- A network dependency is not active.
- The failure is preceded by messages like:

```text
Dependency failed for ...
```

---

## 17. Evidence summary by category

### User identity evidence

Collect:

```bash
systemctl show "$UNIT" --property=User --property=Group --property=SupplementaryGroups --no-pager
systemctl status "$UNIT" --full --no-pager
ps -eo pid,user,args,cmd | grep -F "$UNIT"
id root
id appuser 2>/dev/null
```

Use this evidence to determine whether the service is root, non-root, missing group access, or root but restricted.

---

### Environment variables evidence

Collect:

```bash
systemctl show "$UNIT" --property=Environment --property=EnvironmentFiles --no-pager
systemd-run --scope --quiet env > /tmp/systemd-run-env.txt
env > /tmp/manual-env.txt
diff /tmp/manual-env.txt /tmp/systemd-run-env.txt
```

Look for missing:

```text
PATH
HOME
PYTHONPATH
LD_LIBRARY_PATH
JAVA_HOME
DBUS_SESSION_BUS_ADDRESS
```

---

### Working directory evidence

Collect:

```bash
systemctl show "$UNIT" --property=WorkingDirectory --no-pager
pwd
systemd-run --scope --quiet --property=WorkingDirectory=/app pwd
ls -ldZ /app
```

Look for relative-path failures in the journal.

---

### Permissions evidence

Collect:

```bash
stat -c '%n %A %U:%G' /path/to/executable
ls -lZ /path/to/executable
namei -lx /path/to/executable
getfacl /path/to/executable
findmnt -T /path/to/executable -o TARGET,OPTIONS
```

Look for:

- missing execute bit,
- directory traversal denial,
- wrong owner/group,
- noexec mount,
- ACL issues,
- service user mismatch.

---

### SELinux evidence

Collect:

```bash
getenforce
ausearch -m avc -ts recent
journalctl -k --no-pager -b | grep -iE 'avc|selinux|denied'
ls -Z /path/to/executable
ps -eZ | grep -F "$UNIT"
```

The key evidence is an AVC denial matching the failing PID, path, and operation.

---

### Dependency evidence

Collect:

```bash
systemctl list-dependencies "$UNIT"
systemd-analyze critical-chain "$UNIT"
systemctl show "$UNIT" --property=After --property=Wants --property=Requires --no-pager
systemctl status network.target --full --no-pager
systemctl status remote-fs.target --full --no-pager
```

Use this to determine whether the failure is ordering-related or availability-related.

---

### Filesystem path evidence

Collect:

```bash
systemctl show "$UNIT" --property=ExecStart --no-pager
readlink -f /path/to/executable
file /path/to/executable
head -n 1 /path/to/executable
ldd /path/to/executable
findmnt -T /path/to/executable
```

Use this to determine whether the failure is caused by a bad path, symlink, interpreter, missing library, or mount option.

---

### Journal evidence

Collect:

```bash
journalctl -u "$UNIT" --no-pager --full -b
journalctl -u "$UNIT" --since "-10 minutes" --no-pager --full
journalctl --identifier=systemd --no-pager --full -b | grep -F "$UNIT"
journalctl -k --no-pager --since "-10 minutes" | grep -iE 'avc|oom|segfault|killed'
```

Use this to determine whether the failure is before exec, during exec, after exec, caused by a signal, or caused by another unit.

---

## 18. Decision rules before changing the unit file

Only after collecting the evidence above, map the failure to a category:

| Evidence | Most likely cause | Appropriate next action |
|---|---|---|
| `status=203/EXEC`, bad file format, missing interpreter | Executable cannot be executed | Fix path/shebang/SELinux/mount/executable itself |
| `status=127`, command not found in systemd context | PATH or relative command issue | Use absolute path after verifying; or add `Environment=PATH=...` only if evidence requires it |
| `status=EACCES`, no AVCs | POSIX permissions/noexec | Fix ownership, mode, mount options, service user/group |
| AVC denial appears at service start | SELinux policy/label issue | Fix SELinux context/boolean/policy, not necessarily the unit |
| `WorkingDirectory=` missing or wrong, relative file missing | Wrong cwd | Add/fix `WorkingDirectory=` only after confirming app depends on cwd |
| Missing required env vars in `systemd-run`/unit environment | Environment mismatch | Add `Environment=` or `EnvironmentFile=` only if needed |
| Service user cannot read file or execute command | User/group/permissions mismatch | Fix permissions or change `User=`/`Group=` only with evidence |
| Another required service/target/mount is failed | Dependency issue | Fix dependency or ordering only with evidence |
| Service exits immediately after start but systemd cannot spawn logs | App configuration/env/paths problem | Investigate program logs and environment first |
| `Type=notify` but no notify from process | Unit type mismatch | Fix program or `Type=` only after confirming semantics |
| Kernel OOM kill or signal 9 | Resource limit/OOM | Adjust memory/resource limits or investigate app memory use |
| `StartLimit` messages | Restart storm caused by earlier failure | Fix root cause of fast exit, do not just tune restart settings |

---

## Recommended immediate evidence-gathering bundle

For a failing unit, I would collect all of the following into a text file:

```bash
UNIT=example.service
OUT=/tmp/${UNIT}-evidence.txt

{
  echo "=== date ==="
  date

  echo "=== systemd status ==="
  systemctl status "$UNIT" --full --no-pager

  echo "=== systemctl cat ==="
  systemctl cat -a "$UNIT" --no-pager

  echo "=== important properties ==="
  systemctl show "$UNIT" \
    --property=Name \
    --property=LoadState \
    --property=ActiveState \
    --property=SubState \
    --property=UnitFileState \
    --property=MainPID \
    --property=ExecMainPID \
    --property=ExecMainCode \
    --property=ExecMainStatus \
    --property=Result \
    --property=Type \
    --property=ExecStart \
    --property=ExecStartPre \
    --property=ExecStartPost \
    --property=User \
    --property=Group \
    --property=SupplementaryGroups \
    --property=WorkingDirectory \
    --property=Environment \
    --property=EnvironmentFiles \
    --property=NoNewPrivileges \
    --property=ProtectSystem \
    --property=ProtectHome \
    --property=PrivateTmp \
    --property=PrivateDevices \
    --property=PrivateNetwork \
    --property=PrivateUsers \
    --property=After \
    --property=Wants \
    --property=Requires \
    --property=BindsTo \
    --property=Conflicts \
    --property=Restart \
    --property=NRestarts \
    --property=StartLimitRate \
    --property=LimitNOFILE \
    --property=LimitNPROC \
    --no-pager

  echo "=== dependencies ==="
  systemctl list-dependencies "$UNIT" --no-pager
  systemctl list-dependencies --reverse "$UNIT" --no-pager
  systemd-analyze critical-chain "$UNIT" --no-pager

  echo "=== unit journal ==="
  journalctl -u "$UNIT" --no-pager --full -b

  echo "=== recent systemd messages ==="
  journalctl --identifier=systemd --no-pager --full -b | grep -F "$UNIT"

  echo "=== SELinux status ==="
  getenforce
  sestatus

  echo "=== SELinux executable context ==="
  execpath=$(systemctl show "$UNIT" --property=ExecStart --value | awk '{print $1}')
  [ -n "$execpath" ] && ls -lZ "$execpath"
  [ -n "$execpath" ] && file "$execpath"
  [ -n "$execpath" ] && head -n 1 "$execpath"
  [ -n "$execpath" ] && namei -lx "$execpath"
  [ -n "$execpath" ] && findmnt -T "$execpath" -o TARGET,SOURCE,FSTYPE,OPTIONS
  [ -n "$execpath" ] && ldd "$execpath" || true

  echo "=== working directory ==="
  wd=$(systemctl show "$UNIT" --property=WorkingDirectory --value)
  echo "WorkingDirectory=$wd"
  [ -n "$wd" ] && ls -ldZ "$wd"
  [ -n "$wd" ] && findmnt -T "$wd" -o TARGET,SOURCE,FSTYPE,OPTIONS

  echo "=== SELinux AVCs ==="
  ausearch -m avc -ts recent -i
  journalctl -k --no-pager --since "-1 hour" | grep -iE 'avc|selinux|denied' || true

  echo "=== kernel/resource evidence ==="
  journalctl -k --no-pager --since "-1 hour" | grep -iE 'oom|out of memory|killed process|segfault|core dumped' || true
  coredumpctl list "$UNIT" || true

} > "$OUT"

echo "Evidence written to $OUT"
```

This gives a baseline that you can inspect without having guessed a fix in the unit file.

---

## Final principle

Do not change the unit file until you can point to concrete evidence such as:

- an exact missing path,
- an exact permission denial,
- an exact AVC,
- an exact missing environment variable,
- an exact wrong user,
- an exact wrong working directory,
- an exact failed dependency,
- an exact `Type=`/notify mismatch.

That way, when you eventually edit the unit, the change is evidence-based and does not hide the original problem.
