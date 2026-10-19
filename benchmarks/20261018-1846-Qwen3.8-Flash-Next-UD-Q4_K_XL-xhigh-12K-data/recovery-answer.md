I would not disable `firewalld` as the first step. On a “local” PostgreSQL setup, the connection is often either a Unix-domain socket or loopback TCP, and `firewalld` usually has little or no effect on those paths. Disabling the firewall could also remove the only useful evidence and create an unnecessary security exposure. I would prove or disprove the firewall hypothesis with non-destructive evidence first, then continue troubleshooting based on what the evidence shows.

---

## 1. Capture the exact failure before changing anything

First, get the exact error from the application, not just “cannot connect.”

Look for messages such as:

```text
Connection refused
Connection timed out
Connection reset by peer
could not connect to server: No such file or directory
could not connect to server: Permission denied
no pg_hba.conf entry for host ...
Peer authentication failed
password authentication failed
SSL connection request ...
server does not support SSL
role does not exist
database does not exist
```

The exact error strongly narrows the cause.

Also identify the application’s target:

```bash
env | grep -E '^PG'
ps -eaf | grep app
```

Common PostgreSQL environment variables:

```bash
PGHOST
PGPORT
PGUSER
PGDATABASE
PGSSLMODE
PGOPTIONS
```

Important distinction:

| Application connection target | Path used | Is `firewalld` likely relevant? |
|---|---|---|
| `host=/var/run/postgresql` or blank host | Unix socket | Usually no |
| `host=127.0.0.1` or `localhost` | Loopback TCP | Usually no |
| `host=server.example.com` or LAN IP | Regular network TCP | Possibly, if traffic leaves loopback |

For a local database, the Unix socket path is often `/var/run/postgresql/.s.PGSQL.5432`.

---

## 2. Test from the same context as the application

Do not only test as `postgres` or root. The application may run as a different user, in a different container, namespace, or with a different environment.

Test Unix socket first, if applicable:

```bash
psql "host=/var/run/postgresql port=5432 dbname=mydb user=myuser"
```

If the application runs as `appuser`, try that user:

```bash
sudo -u appuser psql "host=/var/run/postgresql port=5432 dbname=mydb user=myuser"
```

Then test TCP loopback:

```bash
psql "host=127.0.0.1 port=5432 dbname=mydb user=myuser"
psql "host=localhost port=5432 dbname=mydb user=myuser"
psql "host=::1 port=5432 dbname=mydb user=myuser"
```

Use `pg_isready` to separate “no listener/accepting connections” from authentication:

```bash
pg_isready -h 127.0.0.1 -p 5432
pg_isready -h localhost -p 5432
pg_isready -h ::1 -p 5432
pg_isready -h /var/run/postgresql -p 5432
```

If `pg_isready` succeeds but the application fails, the problem is likely authentication, app configuration, SSL, socket path, IPv6/IPv4 choice, or application runtime environment.

---

## 3. Check what PostgreSQL is actually listening on

Check TCP listeners:

```bash
ss -ltnp | grep -E ':5432\b'
```

or:

```bash
ss -ltnp sport = :5432
```

Check Unix sockets:

```bash
ss -lxp | grep postgresql
```

or directly:

```bash
ls -l /var/run/postgresql/
```

Typical socket file:

```text
/var/run/postgresql/.s.PGSQL.5432
```

If you see this:

```text
127.0.0.1:5432
```

but the application is connecting to a non-loopback IP such as `192.168.1.10`, then the problem is likely `listen_addresses`, not firewall.

If you see no listener at all, `firewalld` cannot be the root cause. PostgreSQL must be listening on the requested address first.

Possible causes of no listener:

- PostgreSQL is not running.
- PostgreSQL is running on a different port.
- `listen_addresses` is too restrictive.
- `postgresql.conf` or instance configuration is wrong.
- PostgreSQL failed to bind because the port is already in use.
- Another instance is using the same data directory.
- The app is in a container/network namespace where “localhost” means the container, not the host.

Check service status:

```bash
systemctl status postgresql
systemctl status 'postgresql@*'
journalctl -u postgresql -n 100 --no-pager
```

For Debian/Ubuntu, instances may look like:

```bash
systemctl status postgresql@16-main.service
journalctl -u postgresql@16-main -n 100 --no-pager
```

For RHEL/Fedora, look for:

```bash
systemctl status postgresql.service
journalctl -u postgresql.service -n 100 --no-pager
```

If you can connect as the database superuser, inspect PostgreSQL settings directly:

```bash
sudo -u postgres psql -c "SHOW listen_addresses;"
sudo -u postgres psql -c "SHOW port;"
sudo -u postgres psql -c "SHOW unix_socket_directories;"
sudo -u postgres psql -c "SHOW hba_file;"
sudo -u postgres psql -c "SHOW config_file;"
sudo -u postgres psql -c "SHOW data_directory;"
```

If you cannot connect as `postgres`, check config files such as:

Debian/Ubuntu:

```text
/etc/postgresql/*/main/postgresql.conf
/etc/postgresql/*/main/pg_hba.conf
```

RHEL/Fedora:

```text
/var/lib/pgsql/*/data/postgresql.conf
/var/lib/pgsql/*/data/pg_hba.conf
```

---

## 4. Validate the firewall hypothesis without disabling it

If the application is using loopback TCP or a Unix socket, I would treat the firewall hypothesis as suspect.

### Check whether firewalld is even active

```bash
systemctl status firewalld
firewall-cmd --state
```

If `firewalld` is inactive, reject the firewall hypothesis for a local loopback/socket connection.

### Check which zones matter

```bash
firewall-cmd --get-default-zone
firewall-cmd --get-active-zones
```

Loopback is normally in the `trusted` zone:

```bash
firewall-cmd --get-zone-of-interface=lo
firewall-cmd --list-all --zone=trusted
```

If `lo` is in `trusted` and there are no unusual direct/rich rules dropping it, firewalld is very unlikely to be blocking local loopback connections.

For a non-loopback connection, identify the interface and zone:

```bash
ip route get 192.168.1.10
firewall-cmd --get-zone-of-interface=eth0
```

Replace `eth0` with the actual interface.

Then inspect that zone:

```bash
firewall-cmd --list-all --zone=public
firewall-cmd --list-all --zone=internal
firewall-cmd --list-all --zone=trusted
firewall-cmd --list-rich-rules --all-zones
firewall-cmd --direct --get-all-rules
```

### Look for explicit PostgreSQL or 5432 rules

```bash
firewall-cmd --list-services --all-zones
firewall-cmd --list-ports --all-zones
```

Look for:

```text
postgresql
postgresql-client
5432/tcp
```

### Inspect the actual packet-filtering rules

`firewalld` may use `nftables` underneath, so inspect both views:

```bash
iptables-save -c | grep -E '5432|5433'
nft list ruleset | grep -E '5432|5433'
```

Also look for broader drop/reject rules:

```bash
iptables -L -n -v
nft list ruleset
```

If you want evidence of packet counts, take a snapshot before and after reproducing the failure:

```bash
iptables -L -n -v > /tmp/iptables-before.txt
nft list ruleset > /tmp/nft-before.txt

# reproduce the connection attempt

iptables -L -n -v > /tmp/iptables-after.txt
nft list ruleset > /tmp/nft-after.txt

diff -u /tmp/iptables-before.txt /tmp/iptables-after.txt
diff -u /tmp/nft-before.txt /tmp/nft-after.txt
```

If a firewall rule is truly involved, you should see counters increase or a matching rule hit.

### Use packet capture as objective evidence

For loopback:

```bash
sudo tcpdump -i lo -nn -l 'port 5432'
```

For a non-loopback interface:

```bash
sudo tcpdump -i eth0 -nn -l 'host 192.168.1.10 and port 5432'
```

Interpretation:

| tcpdump result | Meaning |
|---|---|
| No TCP packets at all from app process | App is not reaching the host network stack, wrong host/IP, wrong container namespace, wrong route, or wrong port |
| SYN seen on `lo`, no SYN/ACK | PostgreSQL likely not listening on that address, or app is using wrong IPv4/IPv6 address |
| SYN seen, SYN/ACK returned, but application fails | Network/firewall path probably works; look at auth, SSL, socket, app config |
| SYN sent, no response, no ICMP/REJECT visible | Possibly firewall drop, but also could be wrong route/interface or service not accepting on that IP |

For local loopback, `Connection refused` usually means “no listener on that IP/port,” not “firewall blocked me.” A firewall usually produces a timeout, reject ICMP, or rule counter activity, not a clean TCP RST from nothing.

---

## 5. Interpret the evidence

### If raw TCP to `127.0.0.1:5432` works

```bash
nc -vz 127.0.0.1 5432
```

and succeeds, but the application still fails, reject the firewall hypothesis for that path. Continue looking at:

- Application connection string.
- Environment variables such as `PGHOST` or `PGSSLMODE`.
- Authentication settings.
- Unix socket vs TCP choice.
- IPv6 vs IPv4 resolution of `localhost`.
- SSL configuration.
- App user/permissions.
- Container/network namespace.

### If raw TCP to `127.0.0.1` fails with “Connection refused”

Check whether PostgreSQL is listening:

```bash
ss -ltnp | grep ':5432'
```

If no listener exists, the issue is not firewall. PostgreSQL is not accepting TCP on that address.

Possible fixes to investigate after gathering evidence:

```text
listen_addresses = 'localhost'
listen_addresses = '127.0.0.1'
listen_addresses = '*'
port = 5432
```

Do not blindly set `listen_addresses = '*'`; choose the minimum required address.

### If raw TCP to `127.0.0.1` times out

That is unusual for loopback. Check:

```bash
ip addr show dev lo
ip route get 127.0.0.1
```

Then inspect unusual `firewalld` rules affecting loopback:

```bash
firewall-cmd --get-zone-of-interface=lo
firewall-cmd --list-all --zone=trusted
nft list ruleset | grep -E 'lo|5432'
```

If there is a rule dropping loopback port 5432, you have evidence. Then fix the specific rule; do not disable the whole firewall.

### If TCP to `127.0.0.1` works but TCP to hostname fails

Check hostname resolution:

```bash
hostname
hostname -f
getent hosts $(hostname)
getent ahosts $(hostname)
```

Then test the resolved addresses:

```bash
nc -vz 127.0.0.1 5432
nc -vz $(hostname) 5432
nc -vz $(hostname -I | awk '{print $1}') 5432
```

Common causes:

1. PostgreSQL listens only on `127.0.0.1`, but the app connects to a LAN IP.
2. `localhost` resolves to `::1`, but PostgreSQL is not listening on IPv6.
3. The app uses an external DNS name that resolves to a public/LAN IP.
4. The app is in a container and its `localhost` is not the host’s `localhost`.

This is not necessarily a firewall issue.

---

## 6. Check common non-firewall causes

### A. PostgreSQL is not running or failed to start

```bash
systemctl status postgresql
journalctl -u postgresql -n 100 --no-pager
```

Look for:

```text
could not bind IPv4 address
could not bind IPv6 address
address already in use
database system was not properly shut down
could not access file
insufficient disk space
shared memory allocation failed
```

---

### B. PostgreSQL is not listening on the address the app expects

If you can connect over the Unix socket:

```bash
sudo -u postgres psql -c "SHOW listen_addresses;"
sudo -u postgres psql -c "SHOW port;"
```

Typical safe local configurations:

```conf
listen_addresses = 'localhost'
port = 5432
```

or, if only IPv4 is needed:

```conf
listen_addresses = '127.0.0.1'
```

Reload or restart PostgreSQL after configuration changes:

```bash
sudo systemctl reload postgresql
```

or for templated instances:

```bash
sudo systemctl reload postgresql@16-main
```

Avoid restarting unnecessarily if you can reload first.

---

### C. `pg_hba.conf` is rejecting the connection

A very common cause.

Example error:

```text
FATAL: no pg_hba.conf entry for host "127.0.0.1", user "appuser", database "mydb", SSL off
```

Find the HBA file:

```bash
sudo -u postgres psql -c "SHOW hba_file;"
```

Example entries for local TCP:

```conf
host    all    all    127.0.0.1/32    scram-sha-256
host    all    all    ::1/128         scram-sha-256
```

For Unix sockets:

```conf
local   all    postgres                peer
local   all    appuser                 scram-sha-256
```

`pg_hba.conf` is evaluated top-down. The first matching line wins.

Reload after editing:

```bash
sudo systemctl reload postgresql
```

---

### D. User or database does not exist

If the app reports:

```text
role does not exist
database does not exist
```

check roles and databases:

```bash
sudo -u postgres psql -c "\du"
sudo -u postgres psql -c "\l"
```

Create role/database if appropriate:

```bash
sudo -u postgres createuser --login appuser
sudo -u postgres createdb -O appuser mydb
```

Password failures may be caused by:

- Wrong password in app config.
- Role does not have `LOGIN`.
- `pg_hba.conf` requires `scram-sha-256` but role has an old/clear-text or mismatched password.
- Application is connecting as a different user than intended.

Reset password:

```bash
sudo -u postgres psql -c "ALTER ROLE appuser LOGIN PASSWORD 'secret';"
```

---

### E. Unix socket path or permissions are wrong

Check socket directory:

```bash
ls -ld /var/run/postgresql
ls -l /var/run/postgresql/
```

Typical ownership:

```text
postgres:postgres
```

Typical directory permissions:

```text
drwxrwsr-x or drwxrwxr-x depending on distribution
```

If the app expects `/tmp` but PostgreSQL uses `/var/run/postgresql`, that explains a “No such file or directory” error.

Check socket directory configured in PostgreSQL:

```bash
sudo -u postgres psql -c "SHOW unix_socket_directories;"
```

If SELinux is enforcing, a nonstandard socket path may be denied.

Check SELinux:

```bash
getenforce
sudo ausearch -m avc -ts recent
```

Do not disable SELinux as the first fix. Use proper labeling or policy.

For example, if using a nonstandard PostgreSQL port:

```bash
sudo semanage port -a -t postgresql_port_t -p tcp 5433
sudo restorecon -Rv /path/to/socket/dir
```

For nonstandard socket locations, the details depend on policy; investigate denials before changing policy.

---

### F. IPv6 vs IPv4 mismatch

`localhost` may resolve to `::1` first.

Check listeners:

```bash
ss -ltnp | grep ':5432'
```

If you see only:

```text
127.0.0.1:5432
```

but the app connects to `localhost` and resolves to `::1`, it may fail.

Compare:

```bash
nc -vz 127.0.0.1 5432
nc -vz ::1 5432
psql "host=127.0.0.1 port=5432 ..."
psql "host=::1 port=5432 ..."
```

Fix options:

- Make the app explicitly use `127.0.0.1`.
- Make PostgreSQL listen on IPv6 too if appropriate.
- Add a `host ... ::1/128` line to `pg_hba.conf` if IPv6 loopback is intended.

---

### G. SSL/TLS mismatch

If the app expects SSL but PostgreSQL does not have SSL enabled, or vice versa, connection can fail.

Check server setting:

```bash
sudo -u postgres psql -c "SHOW ssl;"
```

Check client setting:

```bash
env | grep PGSSLMODE
psql "host=127.0.0.1 port=5432 sslmode=disable dbname=mydb user=myuser"
psql "host=127.0.0.1 port=5432 sslmode=require dbname=mydb user=myuser"
```

If SSL is enabled, ensure `hostssl`/`host` lines in `pg_hba.conf` match the expected mode and certificates are correct.

---

### H. The application is not actually on the same network namespace

“Local” can be misleading if the app runs in a container or sandbox.

Check the application process namespace:

```bash
pid=$(pgrep -f your-app-process | head -1)
readlink /proc/$pid/ns/net
readlink /proc/1/ns/net
```

If those differ, the app is in a different network namespace. Its `localhost` is not the host’s `localhost`.

For Docker:

```bash
docker exec <container> getent hosts localhost
docker exec <container> nc -vz 172.17.0.1 5432
```

For Kubernetes:

```bash
kubectl exec -it <pod> -- sh
nc -vz <service-name> 5432
```

In that case, the fix is usually:

- Use the correct service DNS name.
- Use the host gateway IP.
- Use `hostNetwork` or `host.docker.internal` where appropriate.
- Expose PostgreSQL on a container-accessible interface/IP.

Again, this is not initially a firewall issue.

---

### I. Connection limits, resource exhaustion, or file descriptor limits

If PostgreSQL is accepting connections but the app hangs or gets rejected, check limits.

PostgreSQL connection limits:

```bash
sudo -u postgres psql -c "SHOW max_connections;"
```

Current connections:

```bash
sudo -u postgres psql -c "SELECT count(*) FROM pg_stat_activity;"
```

Look for:

```text
FATAL: sorry, too many clients already
FATAL: remaining connection slots are reserved for non-replication superuser connections
```

Also check OS file descriptors for the PostgreSQL process:

```bash
systemctl show postgresql | grep FileDescriptor
```

or for a specific process:

```bash
sudo ls /proc/$(pgrep -f 'postgres -D' | head -1)/fd | wc -l
cat /proc/$(pgrep -f 'postgres -D' | head -1)/limits
```

Check disk space:

```bash
df -h
```

PostgreSQL may fail to accept or start connections if WAL, temporary files, logs, or the data directory have filled the disk.

---

## 7. If firewall evidence actually proves `firewalld` is blocking

If you have evidence such as:

- The app uses a non-loopback IP.
- `nc` to the loopback address works.
- `nc` to the interface IP times out.
- `ss` shows PostgreSQL listening on that interface.
- `nftables`/`iptables` has a DROP/REJECT matching the flow.
- Packet counters increment on drop/reject rules.

Then the firewall may be involved, but I would still not disable `firewalld` broadly. I would add the least-privilege rule needed.

First test a runtime rule without persisting it:

```bash
sudo firewall-cmd --runtime --zone=public --add-service=postgresql
```

or:

```bash
sudo firewall-cmd --runtime --zone=public --add-port=5432/tcp
```

Then test the application.

If it works, decide whether the rule is intended. If yes, persist it:

```bash
sudo firewall-cmd --permanent --zone=public --add-service=postgresql
sudo firewall-cmd --reload
```

For a safer restricted rule, allow only the app source address:

```bash
sudo firewall-cmd --permanent --zone=public --add-rich-rule='rule family="ipv4" source address="192.168.1.25" port port="5432" protocol="tcp" accept'
sudo firewall-cmd --reload
```

If the runtime rule fails or is unnecessary, remove it:

```bash
sudo firewall-cmd --runtime --zone=public --remove-port=5432/tcp
```

The key point: prove which rule matters, open only what is required, then persist intentionally.

---

## 8. A practical troubleshooting flow

A concise flow would be:

1. Get exact application error and connection string.
2. Determine if connection is Unix socket, IPv4 loopback, IPv6 loopback, or real network IP.
3. Test manually with `psql`, `pg_isready`, and `nc`.
4. Confirm PostgreSQL is running and listening:

   ```bash
   systemctl status postgresql
   ss -ltnp | grep ':5432'
   ss -lxp | grep postgresql
   ```

5. If no listener exists on the target address:

   - Check `listen_addresses`.
   - Check `port`.
   - Check PostgreSQL logs.
   - Check network namespace/container.

6. If listener exists but TCP connection fails:

   - If target is loopback, suspect unusual rules, IPv6, bind address, or namespace.
   - If target is non-loopback, inspect `firewalld`/`nftables` rules and packet counters.

7. If TCP connects but `psql`/app fails authentication:

   - Inspect `pg_hba.conf`.
   - Check role/database/password.
   - Check SSL mode.

8. If Unix socket connection fails:

   - Check socket path.
   - Check socket directory permissions.
   - Check peer authentication user mapping.
   - Check SELinux denials.

9. If firewall is proven to be the issue:

   - Add a specific port/service/rich rule.
   - Test.
   - Persist only the needed rule.
   - Do not disable `firewalld` as a general fix.

---

## Bottom line

The firewall hypothesis should be tested, not assumed. For a local PostgreSQL connection, the most likely causes are usually one of these:

- Wrong Unix socket path.
- Application using TCP but PostgreSQL listening only on socket or loopback.
- `listen_addresses` not including the IP the app uses.
- IPv4/IPv6 mismatch.
- `pg_hba.conf` rejection.
- Missing role/database/password mismatch.
- SSL mismatch.
- Application running in a container/network namespace.
- Service failed to start.
- SELinux/file permission problem.

Only after seeing direct evidence such as a matching DROP/REJECT rule, packet counters, or a timeout on a non-loopback interface should I treat `firewalld` as the cause. Even then, the correct change is usually a narrow firewall rule, not disabling the firewall.
