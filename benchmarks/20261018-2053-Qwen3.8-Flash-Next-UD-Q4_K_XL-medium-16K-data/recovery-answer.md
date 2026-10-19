# Systematically Validating the Firewall Hypothesis for a PostgreSQL Connection Failure

## The Core Principle: Read the Evidence Before Touching the System

Disabling `firewalld` is a destructive, wide-blunt-instrument change. If the hypothesis is wrong, you've introduced a security regression *and* you still haven't fixed the application. So the first move is to make the firewall hypothesis **falsifiable** with cheap, non-destructive checks.

---

## Phase 1 — Is the Firewall Hypothesis Even Plausible?

Before running a single rule-query, reason about the *failure signature* the firewall would produce versus other causes.

| Observed symptom | Most likely cause |
|---|---|
| Connection **hangs / times out** (e.g. 120 s, then `ETIMEDOUT`) | Packet silently dropped → firewall, routing, or nothing listening at all |
| Immediate **`ECONNREFUSED`** | Nothing is bound to the port (service down, wrong address) |
| Immediate **`ECONNRESET`** | Something is bound but rejects (SELinux, `pg_hba.conf` reject, crash) |
| **"FATAL: no pg_hba.conf entry"** | TCP reached PostgreSQL but auth policy rejects the role/host |
| **"could not connect to server: No such file or directory"** | Unix-socket path wrong, or PG isn't running |

**Action:** Ask the application team (or read the logs) for the *exact* error string and timing. A timeout looks firewall-shaped; a `ECONNREFUSED` almost certainly does not.

---

## Phase 2 — Cheap, Non-Destructive Firewall Checks

Run these **read-only** commands on the DB host. None of them modify any rule.

```bash
# 1. Is firewalld even active and in a state that could drop packets?
systemctl is-active firewalld          # "active", "inactive", "failed"
firewall-cmd --state                   # "running" or "not running"

# 2. What zone does the interface carrying the DB traffic belong to,
#    and what services / ports does that zone explicitly allow?
firewall-cmd --get-active-zones
firewall-cmd --zone=public --list-all   # replace with the real zone

# 3. Inspect the actual netfilter/nftables rules firewalld installed.
#    Look for DROP / REJECT rules targeting 127.0.0.1 or the relevant IP.
nft list ruleset | grep -A5 5432       # nftables backend (RHEL 9+)
iptables -S | grep 5432                # legacy iptables backend
iptables -L -n -v | grep 5432          # hit/miss counters matter

# 4. The single most decisive test: does a packet to 5432 get through
#    the firewall *right now*, without stopping anything?
#    Use a short timeout so you see "dropped" vs "refused".
timeout 3 bash -c 'echo > /dev/tcp/127.0.0.1/5432' \
  && echo "TCP reachable" \
  || echo "TCP unreachable (exit $?)"

# 5. Correlate with the firewall log if one is enabled.
journalctl -u firewalld --since "10 min ago"
dmesg -T | grep -i 'firewall\|DROP\|REJECT' | tail -40
```

### Interpreting the evidence

- **firewalld is `inactive` or the port is already in the active zone** → the hypothesis is **rejected**. Do not disable anything. Move to Phase 3.
- **A `DROP` rule is matching `dpt:5432` and the counters are incrementing** → the hypothesis is **confirmed**. The correct fix is `firewall-cmd --permanent --add-service=postgresql` (or `--add-port=5432/tcp`) in the right zone, *not* a global disable.
- **The `/dev/tcp` test succeeds** → packets are reaching the kernel. The firewall is **not** the blocker, regardless of what other rules exist.

Only after this evidence should you consider any firewall change, and even then it should be a targeted rule addition, not `systemctl disable firewalld`.

---

## Phase 3 — If the Firewall Is Not the Cause: Continue Troubleshooting

Walk the stack outward from the socket.

### 3a. Is PostgreSQL actually running and listening?

```bash
systemctl status postgresql
ss -tlnp | grep 5432          # expect  127.0.0.1:5432  or  0.0.0.0:5432
ss -xlnp | grep 5432          # expect the unix socket path
```

- **No listener at all** → PG isn't running. Check `journalctl -u postgresql -b`.
- **Listening on `127.0.0.1` only but the app connects from a different namespace / container** → that's a config issue, not a firewall.
- **Listening on `0.0.0.0` but you still can't connect** → keep going.

### 3b. Is `listen_addresses` correct in `postgresql.conf`?

```bash
sudo -u postgres psql -c "SHOW listen_addresses;"
# or
grep -n listen_addresses /var/lib/pgsql/*/data/postgresql.conf
```

The default is `'localhost'`. If the application uses a hostname that resolves to `127.0.0.1`, fine. If it uses the machine's LAN IP, you need `listen_addresses = '*'` or an explicit address, followed by a reload.

### 3c. Is `pg_hba.conf` permitting this connection type?

```bash
sudo -u postgres psql -c "SHOW hba_file;"
cat <the reported path>
```

Check for a line matching the connection method the app uses:

- **Unix socket (`host=127.0.0.1` or omitted)** → look for a `local` line.
- **TCP from `127.0.0.1`** → look for a `host ... 127.0.0.1/32 ... md5|scram-sha-256` line.
- Missing line → the app gets `no pg_hba.conf entry`. Add the appropriate line and `SELECT pg_reload_conf();`.

### 3d. Unix-socket path mismatch (very common for "could not connect to server")

```bash
psql -c "SHOW unix_socket_directories;"
# Application's libpq may be compiled to expect /tmp, while PG uses /var/run/postgresql
```

Fix via `PGHOST` env-var or `unix_socket_directories` in `postgresql.conf`.

### 3e. SELinux / AppArmor

```bash
getenforce
ausearch -m avc -ts recent | grep postgres   # SELinux denials
journalctl -t audit | grep denied | tail
```

A SELinux denial will produce an `ECONNREFUSED`-like symptom while the firewall is perfectly innocent.

### 3f. DNS / hostname resolution

```bash
getent hosts $(hostname)
cat /etc/hosts
```

If the app uses a hostname that resolves to an IPv6 loopback (`::1`) but PostgreSQL only listens on IPv4, the connection will hang or fail.

### 3g. Application-side details

- Is it using `libpq` with the right `PGHOST`, `PGPORT`, `PGUSER`?
- Is it connecting from inside a **container** (Docker, Podman, LXC)? The "local" loopback of the container is not the host's loopback. You'd need `--network host` or an explicit host address *and* an appropriate `pg_hba` entry.
- Check the app's own error log; PostgreSQL's log (`log_destination`, `log_connections`) may show whether the connection even arrived.

```bash
tail -f /var/lib/pgsql/*/data/log/*.log   # watch for incoming connection attempts
```

---

## Phase 4 — Decision Gate: What to Actually Change

| Evidence collected | Correct action |
|---|---|
| `nft` / `iptables` shows a DROP on 5432 with incrementing counters | `firewall-cmd --permanent --add-port=5432/tcp --zone=<zone>` → reload |
| PG not listening on the address the app targets | Fix `listen_addresses`, reload |
| Missing `pg_hba.conf` entry | Add the correct `host`/`local` line, reload |
| SELinux denial in audit log | `setsebool` or custom policy module; do **not** setenforce 0 permanently |
| Socket path mismatch | Align `unix_socket_directories` or app's `PGHOST` |
| Container networking mismatch | Adjust container network or target address |

At no point is the correct fix "disable firewalld" unless the evidence explicitly shows an over-broad rule *and* the business context genuinely requires it (which is almost never the case).

---

## Summary

The discipline is: **observe the error shape → query the firewall read-only → confirm or reject with a single `timeout`/`nc` probe → follow the evidence down the stack (process → listener → bind address → HBA → socket → SELinux → DNS → container) → apply the narrowest fix the evidence supports.** The firewall is one hypothesis among many, and it is only the right one if the packet-distribution counters say it is. Everything else is guesswork dressed up as a solution.