I would not disable `firewalld` first. “Disable the firewall” is a blunt test: it may hide the real problem, change security posture, and if the firewall was not blocking anything, the application will still fail. I would first try to prove or disprove the firewall hypothesis with read-only evidence, then continue based on what the evidence says.

## 1. Capture the exact failure before changing anything

The exact error message is often more informative than the hypothesis.

I would collect:

- The application’s exact connection string or database host/port/socket.
- The full application error, for example:
  - `Connection refused`
  - `Connection timed out` / `No route to host`
  - `Permission denied`
  - `Authentication failed`
  - `No pg_hba entry`
  - `Could not connect to server: FATAL`
- PostgreSQL logs:

```bash
sudo journalctl -u postgresql --since "-15 min" -l
```

- Application logs:

```bash
sudo journalctl -u <app-service> --since "-15 min" -l
```

Why this matters:

- `connection refused` usually means the TCP connection reached the host but nothing was listening, or something explicitly rejected the connection.
- `connection timed out` can suggest a `DROP` rule, routing issue, or namespace problem.
- `permission denied` usually means file/socket permissions, SELinux/AppArmor, or application sandboxing, not `firewalld`.
- `FATAL: password authentication failed` or `FATAL: no pg_hba entry` means the request reached PostgreSQL; firewall is not the cause.

## 2. Determine whether the application is using TCP or a Unix socket

This is especially important for “local” PostgreSQL.

If the application and PostgreSQL are on the same host, the app may connect through a Unix-domain socket, for example:

```bash
/var/run/postgresql/.s.PGSQL.5432
```

or by omitting `-h` in `psql`:

```bash
psql
```

A Unix socket connection does not go through TCP/IP and is not blocked by `firewalld`, `iptables`, or `nftables`. In that case, the firewall hypothesis can be rejected almost immediately, and the problem is likely socket path, permissions, PostgreSQL configuration, user mismatch, or SELinux/AppArmor.

For TCP, the app may connect to:

```bash
127.0.0.1:5432
::1:5432
localhost:5432
0.0.0.0:5432
```

I would inspect the application config and, if necessary, trace what the application actually tries to connect to.

Example checks:

```bash
ss -ltnp | grep 5432
ss -xlnp | grep postgresql
lsof -Pni -sTCP:LISTEN | grep 5432
```

If safe and reproducible, trace the application’s network behavior:

```bash
sudo strace -f -e trace=connect -p <app-pid>
```

That can show whether the app is connecting to:

```text
connect(... sin_addr=127.0.0.1 ... sin_port=htons(5432) ...)
```

or to a Unix socket path, or to `::1`, or to some remote IP.

## 3. Confirm that PostgreSQL is actually listening on the expected address

A very common cause is not a firewall but PostgreSQL not listening on the address/port the app expects.

Check:

```bash
sudo ss -ltnp | grep 5432
```

Possible results:

### Case A: No listener on 5432

If there is no listener, then the firewall is not the primary problem.

I would check:

```bash
systemctl status postgresql --no-pager
sudo journalctl -u postgresql --since "-30 min" -l
```

Then check `postgresql.conf`:

```ini
listen_addresses = 'localhost'
port = 5432
```

If `listen_addresses` is:

```ini
listen_addresses = ''
```

then PostgreSQL will not accept TCP connections. Socket connections may still work.

If it is:

```ini
listen_addresses = 'localhost'
```

then it should accept TCP on loopback addresses.

If it is:

```ini
listen_addresses = '*'
```

then it listens on all interfaces, which may be an unnecessary exposure if only local access is needed.

Changing `listen_addresses` usually requires restarting PostgreSQL:

```bash
sudo systemctl restart postgresql
```

But before restarting, I would capture logs and configuration so the actual cause is documented.

### Case B: Listener exists, but only on the wrong address

Examples:

```bash
LISTEN 0 244 127.0.0.1:5432
```

but the app connects to `::1`, or

```bash
LISTEN 0 244 ::1:5432
```

but the app connects to `127.0.0.1`.

Or PostgreSQL listens on `127.0.0.1:5432`, but the app resolves `localhost` to `::1`.

I would test both:

```bash
nc -vz 127.0.0.1 5432
nc -vz ::1 5432
```

Also:

```bash
getent hosts localhost
```

If one works and the other does not, the issue is address-family or PostgreSQL binding configuration, not necessarily `firewalld`.

### Case C: PostgreSQL listens correctly

Then I continue testing reachability and authorization.

## 4. Test the connection with a simple client, as the same user as the application

The application itself may be failing due to its user, environment, sandbox, or connection string. I would reproduce the failure with a simpler client, ideally as the same user.

TCP over IPv4:

```bash
sudo -u <appuser> psql -h 127.0.0.1 -p 5432 -U <dbuser> -d <dbname> -c "select 1"
```

TCP over IPv6:

```bash
sudo -u <appuser> psql -h ::1 -p 5432 -U <dbuser> -d <dbname> -c "select 1"
```

Using `localhost`:

```bash
sudo -u <appuser> psql -h localhost -p 5432 -U <dbuser> -d <dbname> -c "select 1"
```

Unix socket, if applicable:

```bash
sudo -u <appuser> psql -h /var/run/postgresql -U <dbuser> -d <dbname> -c "select 1"
```

The socket path may also be:

```bash
/tmp
```

depending on PostgreSQL configuration and distribution.

If a simple client works but the application fails, the firewall hypothesis is usually not correct, and the next step is to compare:

- Application host/port/socket.
- Application user.
- Application environment variables such as `PGHOST`, `PGPORT`, `PGUSER`, `PGDATABASE`.
- Application sandboxing.
- SELinux/AppArmor context.
- TLS/SSL settings.
- Connection pooling configuration.

If a simple client fails the same way as the application, then the issue is in the lower connection path: service listening, routing, namespace, firewall, SELinux, PostgreSQL authentication, or socket permissions.

## 5. Validate or reject the firewall hypothesis without disabling it

I would inspect the firewall configuration and packet counters rather than turning it off.

### Check whether `firewalld` is even running

```bash
sudo firewall-cmd --state
sudo systemctl status firewalld --no-pager
```

If `firewalld` is not active, the hypothesis is already weak.

### Inspect active zones and rules

```bash
sudo firewall-cmd --get-active-zones
sudo firewall-cmd --list-all
sudo firewall-cmd --list-all-zones
```

Check for rules involving port `5432` or loopback interface.

Then inspect the actual kernel rules:

```bash
sudo iptables -L -n -v --line-numbers
sudo iptables -L INPUT -n -v
sudo iptables -L OUTPUT -n -v
sudo iptables -L FORWARD -n -v
```

On modern systems using nftables:

```bash
sudo nft list ruleset
```

I would look for:

- A rule matching TCP port `5432`.
- A rule dropping or rejecting traffic from the application source or zone.
- A rule that does not explicitly allow loopback traffic.
- Container or network namespace rules that might intercept traffic.

Important: for local connections on `127.0.0.1` or `::1`, `firewalld` is usually not the cause because loopback traffic is generally trusted. It can still be affected by unusual custom rules, containers, network namespaces, or rules that specifically target loopback, but that should be proven.

### Use packet counters as evidence

If there are relevant rules, run the failing test and watch the counters increase.

Example:

```bash
sudo iptables -L INPUT -n -v
```

Run the app or a simple test:

```bash
nc -vz 127.0.0.1 5432
```

Then inspect counters again:

```bash
sudo iptables -L INPUT -n -v
```

If a `DROP` or `REJECT` rule’s packet counter increments during the test, that supports the firewall hypothesis. If no relevant counters increment, that weakens it.

### Capture packets on loopback

For a TCP connection to localhost:

```bash
sudo tcpdump -i lo -nn -c 20 'port 5432'
```

In another terminal:

```bash
nc -vz 127.0.0.1 5432
```

Interpretation:

| tcpdump / test result | likely meaning |
|---|---|
| `nc` connects successfully | Network path is fine; app/auth/socket issue likely. |
| SYN seen, SYN/ACK seen, then failure later | Network allowed; problem is above the network layer: PostgreSQL, auth, TLS, app config. |
| SYN seen, RST seen | Nothing listening, or explicit reject; not necessarily `firewalld`. |
| SYN seen, no reply | Possible firewall `DROP`, routing issue, or unreachable service; confirm with counters/logs. |
| No packets seen on expected path | App is not using that path: Unix socket, different IP, different namespace, container, DNS resolution, app sandbox, wrong port. |

If the app uses a Unix socket, `tcpdump` on `lo` will not show database traffic at all. That does not mean the firewall is blocking; it means the connection is not using TCP.

### Check kernel and audit logs

```bash
sudo journalctl -k --since "-15 min"
sudo ausearch -m avc -ts recent
```

On RHEL/Fedora, SELinux denials are a common reason a local application “cannot connect” to PostgreSQL, and disabling `firewalld` will not fix that.

## 6. Evidence-based interpretation

I would make a decision table based on evidence.

| Evidence | Conclusion | Next action |
|---|---|---|
| App uses Unix socket | Not TCP firewall | Check socket path, permissions, `unix_socket_directories`, SELinux |
| `ss` shows no PostgreSQL listener | Service/config issue | Start/configure PostgreSQL listener |
| `ss` shows listener on wrong address | Binding/address issue | Fix `listen_addresses`, test IPv4/IPv6 explicitly |
| `nc` to `127.0.0.1` works but app fails | Firewall probably not blocking TCP | Investigate app config, environment, user, sandbox |
| `psql` works as app user but app fails | App-specific issue | Compare app env, connection string, runtime user, SELinux context |
| `psql` fails as app user but succeeds as root | User permission, socket permissions, SELinux, sandbox | Test as app user and check file/SELinux contexts |
| `psql` reaches PostgreSQL but returns `FATAL: no pg_hba entry` | Auth/config issue | Fix `pg_hba.conf` and reload PostgreSQL |
| `psql` reaches PostgreSQL but returns password failure | Auth issue | Verify role/password/authentication method |
| `tcpdump` shows SYN but no response and firewall counters increment | Firewall likely blocking | Add narrow temporary allow rule, not global disable |
| `tcpdump` shows no packets to expected TCP path | Wrong target/socket/namespace/DNS | Trace app connect, check containers/namespaces |
| `ausearch` shows SELinux denial | SELinux blocking app | Apply correct SELinux boolean/context |
| PostgreSQL logs show connection attempts | Request reached PostgreSQL | Focus on PostgreSQL auth/config/resources |

## 7. If firewall evidence is weak, continue with other common causes

### A. PostgreSQL is not running or crashed

```bash
systemctl status postgresql --no-pager
sudo journalctl -u postgresql --since "-30 min" -l
ps -ef | grep postgres
```

### B. Wrong host/port/socket in the application

Check app config and environment:

```bash
sudo -u <appuser> env | grep '^PG'
```

Common environment variables:

```bash
PGHOST
PGPORT
PGUSER
PGDATABASE
PGPASSWORD
PGSSLMODE
```

If the app uses `localhost`, verify whether that resolves to IPv4 or IPv6:

```bash
getent hosts localhost
```

It is sometimes useful to configure the app with an explicit address:

```text
127.0.0.1
```

instead of:

```text
localhost
```

while testing.

### C. PostgreSQL authentication configuration

Check `pg_hba.conf`. For local TCP connections, there may need to be entries like:

```text
host    all    all    127.0.0.1/32    scram-sha-256
host    all    all    ::1/128         scram-sha-256
```

Or for Unix sockets:

```text
local   all    all                    peer
```

The exact method should match the intended security model.

For a password-based local application, the app may need:

```text
host    appdb    appuser    127.0.0.1/32    scram-sha-256
```

After changing `pg_hba.conf`, reload PostgreSQL:

```bash
sudo systemctl reload postgresql
```

I would check PostgreSQL logs because they usually show the exact reason:

```text
FATAL: password authentication failed for user "appuser"
FATAL: no pg_hba.conf entry for host "127.0.0.1", user "appuser", database "appdb", no encryption
```

If the log shows `no pg_hba.conf entry`, then the packet reached PostgreSQL. The firewall was not blocking it.

### D. Unix socket permissions or wrong socket directory

Check socket existence:

```bash
ls -l /var/run/postgresql
```

You may see:

```text
.s.PGSQL.5432
```

Check ownership and permissions:

```bash
sudo stat /var/run/postgresql/.s.PGSQL.5432
```

Also check PostgreSQL config:

```bash
sudo -u postgres psql -c "show unix_socket_directories;"
```

If the application runs as a different user, it may be unable to access the socket directory or socket file.

### E. SELinux or AppArmor

On SELinux systems:

```bash
getenforce
sudo ausearch -m avc -ts recent
sudo sealert -a /var/log/audit/audit.log
```

A local web application connecting to PostgreSQL may be blocked by SELinux even when networking appears normal. A common example for Apache/nginx/php-fpm applications is the `httpd_can_network_connect_db` boolean, though the correct boolean depends on the application domain.

I would not permanently enable a boolean without understanding the security implications, but it is a much better test than disabling the entire host firewall.

On AppArmor systems:

```bash
sudo aa-status
sudo dmesg | grep -i apparmor
sudo journalctl -k | grep apparmor
```

### F. Container or network namespace mismatch

If the application or PostgreSQL is in a container, “local” may mean different things.

Example:

- The app is in a container.
- PostgreSQL is on the host.
- The app uses `localhost` inside its container.
- The database is actually on the host’s `127.0.0.1`, which is not the same as the container’s `127.0.0.1` unless host networking is used.

Checks:

```bash
sudo systemctl list-units --type=service | grep -E 'podman|docker|containerd'
sudo ip netns list
sudo podman ps -a
sudo docker ps -a
```

If inside a network namespace:

```bash
sudo ip netns exec <namespace> ss -ltnp | grep 5432
sudo ip netns exec <namespace> nc -vz 127.0.0.1 5432
```

This can avoid chasing a host firewall problem that is actually a namespace routing problem.

### G. Connection limit or resource issue

If PostgreSQL is accepting connections but then rejecting them, logs may show:

```text
FATAL: sorry, too many clients already
```

Check active connections:

```bash
sudo -u postgres psql -c "select count(*) from pg_stat_activity;"
```

Check configured limit:

```bash
sudo -u postgres psql -c "show max_connections;"
```

### H. TLS/SSL issues

If PostgreSQL has `ssl = on`, the application may require the right client certificate, CA, or SSL mode. That will not look like a firewall problem in the logs if the TLS failure occurs after TCP connection.

Relevant PostgreSQL settings:

```bash
sudo -u postgres psql -c "show ssl;"
sudo -u postgres psql -c "show ssl_ca_file;"
sudo -u postgres psql -c "show ssl_cert_file;"
sudo -u postgres psql -c "show ssl_key_file;"
```

Application SSL mode may need to be adjusted, for example:

```text
sslmode=disable
sslmode=require
sslmode=verify-ca
sslmode=verify-full
```

The correct value depends on the security policy.

## 8. If firewall evidence actually proves the hypothesis, change minimally

If I find:

- SYN packets to the correct IP/port,
- no response,
- a matching firewall `DROP` rule,
- counters incrementing,
- and no SELinux, namespace, listener, or auth issue,

then the firewall is likely blocking.

Even then, I would not globally disable `firewalld`. I would apply a narrow, testable rule.

Example conceptually:

```bash
sudo firewall-cmd --timeout=300 --add-service=postgresql
```

or a more specific rule from the app’s source address/zone if it is not truly local. Then retest. If the problem disappears, remove the temporary rule and implement a permanent controlled exception with proper source, port, and logging.

A temporary global stop would only be acceptable under a change-control process with rollback, not as the default troubleshooting approach.

## Bottom line

I would first reject the firewall recommendation unless the evidence supports it.

A quick evidence path would be:

```bash
systemctl status postgresql
ss -ltnp | grep 5432
psql -h 127.0.0.1 -p 5432 -U <user> -d <db> -c "select 1"
psql -h /var/run/postgresql -U <user> -d <db> -c "select 1"
nc -vz 127.0.0.1 5432
sudo tcpdump -i lo -nn port 5432
sudo iptables -L -n -v
sudo nft list ruleset
sudo journalctl -u postgresql --since "-15 min"
sudo ausearch -m avc -ts recent
```

Then I would interpret the results:

- If PostgreSQL is not listening, fix the listener.
- If the app is using a Unix socket, look at socket path/permissions.
- If PostgreSQL logs auth failures, fix `pg_hba.conf` or credentials.
- If `psql` works but the app fails, look at the app’s environment, user, sandbox, or namespace.
- If SELinux denials appear, fix SELinux policy.
- Only if packet traces, firewall counters, and rule inspection show packets being dropped on the intended TCP path would I treat the firewall as the actual cause — and even then, I would apply a narrow allow rule rather than disabling `firewalld` outright.
