# Troubleshooting a RHEL systemd Service That Fails Under `systemctl` but Succeeds as Root

## Guiding Principle

The service binary works when you type it at a root shell. That tells you the binary, its code, and its core dependencies are fine. **The entire failure lives in the execution context that systemd provides.** Your job is to catalogue every dimension in which a systemd-spawned process differs from a login-shell-spawned process, and inspect each one in the service's actual runtime environment. Resist the urge to edit the unit file until you have hard evidence pointing at a specific dimension.

---

## Phase 0 — Freeze a Reproduction and Capture a Baseline

Before you touch anything:

```bash
# Reproduce the failure and capture the exit code
systemctl start myservice
systemctl status myservice --no-pager --full
echo "Main PID exit: $?"

# Grab the raw journal for the last start attempt (± 30 s)
journalctl -u myservice -n 200 --no-pager -o short-precise > /tmp/journal_first.txt

# Also grab the kernel ring buffer for SELinux denials or OOM kills
dmesg -T | tail -200 > /tmp/dmesg_tail.txt
```

Write down the **exact** error string, PID, and timestamp. You will compare every subsequent test against this baseline.

---

## Phase 1 — Inspect the Unit File Itself (Read-Only)

```bash
systemctl cat myservice.service
systemctl show myservice -p User,Group,WorkingDirectory,ExecStart,Environment,
    EnvironmentFile,Path,RootDirectory,Namespace,SyslogIdentifier,
    Restart,RestartSec,TimeoutStartSec,After,Wants,Requires,BindsTo,
    Type,DynamicUser,PrivateTmp,ProtectSystem,ProtectHome,
    ProtectKernelModules,ProtectControlGroups,ReadWritePaths,
    NoNewPrivileges,CapabilityBoundingSet,AmbientCapabilities,
    SELinuxContextFromNet,SELinuxContextFromNet
```

Note especially:

- **`Type=`** — is it `simple`, `notify`, `forking`, `exec`? A `notify` unit that never sends `sd_notify` will time out and be killed. A `forking` unit where the daemon does *not* fork will appear to "fail immediately."
- **`ExecStart=`** — is it an absolute path? Does it rely on shell expansion (`$HOME`, `~`, `*`)? `systemd` does **not** invoke a shell unless you explicitly use `ExecStart=/bin/bash -c '…'`.
- **`After=` / `Requires=`** — are the dependencies actually running? A network service started before `network-online.target` will fail.

---

## Phase 2 — User Identity, Groups, and Capabilities

A root shell is `uid=0, gid=0, groups=0(root)` plus any supplementary groups and **full capabilities**. systemd may alter this:

```bash
# What does the unit declare?
systemctl show myservice -p User,Group,SupplementaryGroups,
    DynamicUser,NoNewPrivileges,CapabilityBoundingSet,AmbientCapabilities

# If the service actually started briefly, inspect its real identity:
# (run while the process is still alive, or use a wrapper script)
cat /proc/<PID>/status | grep -E '^(Uid|Gid|Groups|CapEff|CapBnd|Seccomp)'
cat /proc/<PID>/capability

# Compare with your manual root shell:
id
cat /proc/$$/status | grep -E '^(Uid|Gid|CapEff)'
cat /proc/$$/capability
```

**Evidence to look for:**

- `DynamicUser=yes` allocates a random UID — your binary may `chown` a file owned by root and then lose access on the next restart.
- `NoNewPrivileges=yes` blocks `setuid` helpers.
- A stripped `CapabilityBoundingSet` removes `CAP_NET_ADMIN`, `CAP_SYS_PTRACE`, etc. that a root shell implicitly has.
- If `User=` is set to a non-root user, the service runs as that user regardless of "I ran it as root in the terminal."

---

## Phase 3 — Environment Variables

This is the single most common cause of "works in terminal, fails in systemd." systemd starts services with a **minimal** environment (typically just `PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin` on RHEL 9, plus a handful of `systemd`-injected vars). No `.bashrc`, no `.bash_profile`, no `/etc/profile.d/` scripts, no `LD_LIBRARY_PATH`, no `JAVA_HOME`, no `PYTHONPATH`, no `LD_PRELOAD`.

```bash
# Show what systemd actually passes:
systemctl show myservice -p Environment,EnvironmentFile

# If the service is running, dump its real environment:
tr '\0' '\n' < /proc/<PID>/environ | sort > /tmp/svc_env.txt

# Compare against your interactive root shell:
env | sort > /tmp/shell_env.txt
diff /tmp/svc_env.txt /tmp/shell_env.txt

# If the process died too fast, temporarily wrap ExecStart in a unit override
# that captures the environment BEFORE the real binary runs:
mkdir -p /etc/systemd/system/myservice.service.d
cat > /etc/systemd/system/myservice.service.d/99-debug-env.conf <<'EOF'
[Service]
ExecStart=
ExecStart=/bin/bash -c 'env > /tmp/svc_env_debug.txt; exec /usr/local/bin/mybinary "$@"' _
EOF
systemctl daemon-reload && systemctl start myservice
# Then read /tmp/svc_env_debug.txt
```

**Evidence to look for:**

- Missing `LD_LIBRARY_PATH` → `error while loading shared libraries: libfoo.so`.
- Missing `PATH` entries → the binary invokes `sh -c "which foo"` and gets nothing.
- Missing application-specific variables (`DB_HOST`, `APP_CONFIG`, `TNS_ADMIN`, etc.).

---

## Phase 4 — Working Directory

A login shell sets `$PWD` to the user's home or wherever `cd`'d. systemd defaults to `/` unless `WorkingDirectory=` is set.

```bash
systemctl show myservice -p WorkingDirectory,RootDirectory,RootImage

# If running:
ls -la /proc/<PID>/cwd

# The application might open "config/app.yaml" relative to CWD.
# In a shell: CWD = /opt/myapp  → finds it.
# In systemd: CWD = /           → ENOENT.
```

Check whether the binary or any of its config files uses **relative paths**:

```bash
strace -f -e trace=openat -o /tmp/strace_relpaths.txt -- \
  /usr/local/bin/mybinary   # run manually to see which files it opens
```

Then compare those relative paths against what `WorkingDirectory=/` would resolve them to.

---

## Phase 5 — Filesystem Permissions and Path Resolution

Even as root, a service can be denied if the unit imposes sandboxing or the path is wrong:

```bash
# Verify the ExecStart binary is executable by the service user:
ls -la /usr/local/bin/mybinary
getenforce   # (see SELinux below)

# Check every path the unit references (ExecStart, EnvironmentFile, PIDFile,
# StandardOutput, StandardError, ReadWritePaths):
for p in /usr/local/bin/mybinary /opt/myapp/conf/*.yaml /var/run/myservice; do
  echo "=== $p ===" ; stat "$p" 2>&1
done

# If ProtectSystem=, ProtectHome=, PrivateTmp=, or RootDirectory= is set,
# the namespace may hide or make-read-only paths the binary expects:
systemctl show myservice -p ProtectSystem,ProtectHome,PrivateTmp,
    PrivateNetwork,ProtectKernelModules,ProtectControlGroups,
    PrivateDevices,RestrictAddressFamilies,RootDirectory,ReadWritePaths
```

**Evidence to look for:**

- `Permission denied` on a config file or socket path.
- `ProtectSystem=strict` makes `/usr`, `/boot`, `/etc` read-only; a binary that writes a cache under `/usr/local` will crash.
- `PrivateTmp=yes` gives a private `/tmp`; a PID file or lock file written to `/tmp` by a prior non-systemd invocation won't be visible.

---

## Phase 6 — SELinux (RHEL-Specific, Frequently Overlooked)

RHEL ships with SELinux in `enforcing` by default. A manual root shell runs as `unconfined_t`. systemd services run as `unconfined_service_t` (or a custom domain). **The same root user, same binary, different SELinux type → different access decisions.**

```bash
# Confirm SELinux mode and context:
getenforce
ls -Z /usr/local/bin/mybinary
matchpathcon /usr/local/bin/mybinary

# Check for denials from the last failure:
ausearch -m avc -ts recent | tail -50
# or:
grep -i 'avc.*denied' /var/log/audit/audit.log | tail -50
journalctl -u myservice --since "2 minutes ago" | grep -i 'selinux\|avc'

# See what domain the service *would* run in:
# (read-only; do not change enforcing)
seinfo -t unconfined_service_t | head
# Compare with what it expects:
seinfo -r system_u -t unconfined_service_t

# Test: temporarily set permissive to confirm SELinux is the culprit
# (ONLY on a test/staging box, restore immediately):
# setenforce 0; systemctl start myservice; setenforce 1
```

**Evidence to look for:**

- `avc: denied { execute } … scontext=unconfined_service_t tcontext=unconfined_service_t tclass=file` → the binary was not labelled correctly (`restorecon` or `semanage fcontext`).
- `avc: denied { name_connect } … tclass=tcp_socket` → SELinux policy blocks the socket even though iptables would allow it.
- `avc: denied { read } … path="/opt/myapp/config.yaml" tcontext=file_t` → context is wrong for the new install directory.

If denials appear, **do not blanket-`audit2allow`** in production. Generate a targeted policy module or fix the file contexts.

---

## Phase 7 — Dependencies and Startup Ordering

```bash
# Which units must be up first?
systemctl show myservice -p After,Requires,Wants,BindsTo,Conflicts

# Are those units actually active?
for u in $(systemctl show myservice -p After --value | tr ' ' '\n' | grep -v '^$'); do
  systemctl is-active "$u"
done

# Did the dependency fail?
systemctl list-dependencies --after myservice
systemctl status network-online.target nfs-server.target postgresql.service  # examples

# Check for a race: the service starts before DNS/network/mount is ready.
# Look at timestamps:
journalctl -u myservice --since "5 minutes ago" --no-pager
journalctl -u network-online.target --since "5 minutes ago" --no-pager
```

**Evidence to look for:**

- `Requires=postgresql.service` but Postgres is still starting → connection refused.
- Missing `After=network-online.target` → DNS lookup fails.
- `BindsTo=mnt-myshare.mount` but the NFS mount is absent → `systemctl start` waits or fails.

---

## Phase 8 — Service Type Mismatch and Timing

```bash
systemctl show myservice -p Type,TimeoutStartSec,ExecStartPre,ExecStartPost,
    Restart,RestartSec,SuccessExitStatus

# If Type=notify: does the binary send sd_notify(1)?
# strace the manual run for the notify socket write:
strace -f -e trace=connect,sendto -p <PID>   # attach to a running manual instance
# If no sd_notify call exists, systemd kills the service after TimeoutStartSec.

# If Type=forking: does the binary actually fork+exit the parent?
# A binary that stays in the foreground will cause systemd to
# report "Main process exited" the moment the foreground process ends.
```

**Evidence to look for:**

- `Job for myservice.service failed because a timeout was exceeded` → `Type=notify` without `sd_notify`.
- `Main process exited, code=exited, status=0/SUCCESS` but the unit is `inactive` → `Type=forking` but the daemon stayed in the foreground (systemd reaped the "parent" PID immediately).

---

## Phase 9 — Kernel-Level and Resource Evidence

```bash
# Was the process OOM-killed?
dmesg -T | grep -iE 'killed process|oom|out of memory'

# File-descriptor or ulimit differences:
systemctl show myservice -p LimitNOFILE,LimitNPROC,LimitCORE,LimitAS
bash -c 'ulimit -a'

# Strace the systemd-launched process for the first few seconds:
# (use a one-shot override with strace wrapping)
cat > /etc/systemd/system/myservice.service.d/99-strace.conf <<'EOF'
[Service]
ExecStart=
ExecStart=/usr/bin/strace -f -tt -o /tmp/svc_strace.txt -- /usr/local/bin/mybinary
EOF
systemctl daemon-reload && systemctl start myservice
# Read /tmp/svc_strace.txt for the first ENOENT, EACCES, ECONNREFUSED
```

---

## Phase 10 — Reproduce the systemd Context Exactly (The "Diff Test")

The definitive experiment: replicate systemd's environment **in your shell** and run the binary manually.

```bash
# Pull the exact env, CWD, and identity systemd would use:
SVC_ENV=$(systemctl show myservice -p Environment --value)
SVC_CWD=$(systemctl show myservice -p WorkingDirectory --value)
SVC_USER=$(systemctl show myservice -p User --value)

# Minimal env, correct CWD, correct user:
env -i $(echo "$SVC_ENV" | tr ' ' '\n' | grep -v '^$') \
  sh -c "cd '${SVC_CWD:-/}' && exec /usr/local/bin/mybinary"
```

If the binary now **fails the same way**, the problem is environmental (Phase 2–5). If it still **succeeds**, the problem is namespace, SELinux, capability, or dependency-related (Phase 6–8).

---

## Evidence Checklist Before Editing the Unit File

| Dimension | Command / Artefact | What you record |
|---|---|---|
| Unit semantics | `systemctl cat`, `systemctl show` | Type, ExecStart path, all sandboxing flags |
| Identity / caps | `/proc/<pid>/status`, `id`, `capsh --print` | UID, GID, groups, CapEff, CapBnd |
| Environment | `/proc/<pid>/environ` or debug wrapper | diff vs shell `env` |
| Working directory | `/proc/<pid>/cwd`, `WorkingDirectory` | Is it `/` instead of the app dir? |
| Permissions / paths | `stat`, `ls -la`, `strace -e openat` | Which file returns EACCES / ENOENT? |
| SELinux | `ausearch -m avc`, `getenforce`, `ls -Z` | Exact denied scontext/tcontext/tclass |
| Dependencies | `systemctl is-active`, `journalctl` timestamps | Missing or late dependency |
| Type / timing | `systemctl status`, `strace` for sd_notify | Timeout? Premature "exit"? |
| Kernel / resource | `dmesg`, `LimitNOFILE` | OOM kill, fd exhaustion |

**Only after filling this table with concrete output** should you modify the unit file, a drop-in, a file context, or a policy module. Each change should map to exactly one row of evidence. If a change does not resolve the failure, you can revert a single, well-scoped drop-in rather than untangling a multi-line edit to the main unit.

```bash
# Preferred method for any unit-file change:
systemctl edit myservice          # creates /etc/systemd/system/myservice.service.d/override.conf
# or a named drop-in:
mkdir -p /etc/systemd/system/myservice.service.d
cat > /etc/systemd/system/myservice.service.d/50-fix-env.conf <<'EOF'
[Service]
Environment="LD_LIBRARY_PATH=/opt/myapp/lib"
WorkingDirectory=/opt/myapp
EOF
systemctl daemon-reload && systemctl restart myservice
```

This evidence-first, change-one-thing-at-a-time discipline keeps the unit file's intent clear and prevents the "shotgun edit" that hides the real cause for the next engineer.