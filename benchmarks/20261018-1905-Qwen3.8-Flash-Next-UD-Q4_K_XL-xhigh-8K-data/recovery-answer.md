Do not disable `firewalld` first. “Firewall is blocking it” is only one hypothesis, and for a truly local PostgreSQL connection it is often the wrong one. Disablement also removes future evidence and can expose services unnecessarily.

A better approach is: **prove what connection path the application uses, then prove whether a firewall rule can be in that path.**

---

## 1. First, characterize the actual failure

Get the exact connection attempt and exact error. “Cannot connect” is too vague.

Try reproducing it with a simple client:

```bash
psql "host=localhost port=5432 dbname=mydb user=app_user" -c 'select 1'
psql "host=127.0.0.1 port=5432 dbname=mydb user=app_user" -c 'select 1'
psql "host=/run/postgresql port=5432 dbname=mydb user=app_user" -c 'select 1'
```

Check the app’s environment:

```bash
printenv | grep -i '^PG'
```

Important variables:

```text
PGHOST
PGPORT
PGUSER
PGDATABASE
PGPASSFILE
PGSSLMODE
PGSERVICEFILE
```

If the application runs under systemd, check its unit too:

```bash
systemctl cat myapp.service
```

Possible meanings of errors:

| Error | Likely direction |
|---|---|
| `Connection refused` | Nothing is listening on that address/port, or a rule sends a reject response |
| `Timeout` / `Network is unreachable` | Routing/interface/firewall issue |
| `Permission denied` | Unix socket permissions, filesystem permissions, SELinux/AppArmor, sandboxing |
| `FATAL: Peer authentication failed` | PostgreSQL authentication, not firewall |
| `no pg_hba.conf entry for host ...` | PostgreSQL auth policy, request reached PostgreSQL |
| `SSL error` / `server does not support SSL` | PostgreSQL SSL settings or client `sslmode` |
| `too many clients` | Resource/connection limit, not firewall |

The exact error is the first major piece of evidence.

---

## 2. Determine the connection transport: Unix socket vs TCP

PostgreSQL can connect using:

1. **Unix-domain socket**, commonly under `/run/postgresql`, `/var/run/postgresql`, or `/tmp`.
2. **TCP to loopback**, usually `127.0.0.1` or `::1`.
3. **TCP to a non-loopback address**, such as the host’s LAN IP.

Firewalld affects IP traffic according to zones/interfaces/rules. It generally does not directly filter ordinary Unix-domain socket filesystem operations, although SELinux or file permissions can.

Use `strace` to see what the application actually does:

```bash
strace -f -e trace=socket,connect,setsockopt -o /tmp/app-pg.strace \
  command-that-triggers-the-db-connection
```

If the app is already running, you can try:

```bash
sudo strace -f -p "$(pidof myapp)" -e trace=network -o /tmp/app-pg-live.strace
```

Then inspect:

```bash
grep -E 'AF_UNIX|AF_INET|AF_INET6|connect|5432|/run/postgresql|/tmp/\.s\.PGSQL' /tmp/app-pg.strace
```

Example interpretations:

```text
connect(..., {sa_family=AF_UNIX, sun_path="/run/postgresql/.s.PGSQL.5432"}, ...)
```

The app is using a **local Unix socket**. In that case, `firewalld` is almost certainly not the cause.

```text
connect(..., {sa_family=AF_INET, sin_port=htons(5432), sin_addr=inet_addr("127.0.0.1")}, ...)
```

The app is using **TCP loopback**. Firewalld is less likely, but still worth checking if there are custom loopback rules.

```text
connect(..., {sa_family=AF_INET, sin_port=htons(5432), sin_addr=inet_addr("192.168.1.10")}, ...)
```

The app is using **a non-loopback IP**. In that case, firewall/zones/interfaces become a plausible hypothesis.

Also check how names resolve:

```bash
getent hosts localhost
getent hosts "$(hostname)"
```

If `localhost` resolves to `::1`, but PostgreSQL only listens on `127.0.0.1`, you may see a connection refusal that looks like a firewall problem but is not.

---

## 3. Check whether PostgreSQL is listening on the expected address/port

On the PostgreSQL host:

```bash
sudo systemctl status postgresql
journalctl -u postgresql --since "-15 minutes" --no-pager
```

Check TCP listeners:

```bash
sudo ss -lntp | grep -E ':5432|postgresql'
```

or:

```bash
sudo lsof -nP -iTCP:5432 -sTCP:LISTEN
```

Expected healthy examples:

```text
LISTEN 0  200  127.0.0.1:5432  users:(("postgres",pid=1234,...))
LISTEN 0  200  ::1:5432         users:(("postgres",pid=1234,...))
LISTEN 0  200  10.0.0.20:5432   users:(("postgres",pid=1234,...))
```

Check Unix socket:

```bash
sudo ss -xlnp | grep -E 'pgsql|\.s\.PGSQL'
ls -l /run/postgresql
```

Typical socket:

```text
/run/postgresql/.s.PGSQL.5432
```

Possible findings:

### Finding: no TCP listener on the address the app uses

If the app connects to `127.0.0.1:5432`, but `ss` shows no listener on `127.0.0.1:5432`, the firewall is not the primary problem. PostgreSQL is not accepting connections there.

Check PostgreSQL settings:

```bash
sudo -u postgres psql -c "SHOW listen_addresses;"
sudo -u postgres psql -c "SHOW port;"
sudo -u postgres psql -c "SHOW unix_socket_directories;"
```

Important PostgreSQL config values in `postgresql.conf`:

```text
listen_addresses = 'localhost'
port = 5432
unix_socket_directories = '/run/postgresql'
```

If `listen_addresses` is empty or only `localhost`, PostgreSQL will not accept external LAN connections. If the app is trying to connect to a LAN IP, that is expected behavior, not necessarily a firewall problem.

After changing config:

```bash
sudo systemctl reload postgresql
# or
sudo systemctl restart postgresql
```

### Finding: PostgreSQL listens only on `127.0.0.1`, but app connects to `::1`

Test both:

```bash
psql -h 127.0.0.1 -p 5432 -U app_user -d mydb -c 'select 1'
psql -h ::1 -p 5432 -U app_user -d mydb -c 'select 1'
```

If IPv4 works and IPv6 fails, check whether PostgreSQL is listening on `::1`:

```bash
sudo ss -lntp | grep 5432
```

If it is not listening on IPv6, adjust `listen_addresses`, or configure the client to use `127.0.0.1`.

### Finding: PostgreSQL is listening on a Unix socket, not TCP

If the app expects TCP but PostgreSQL is only configured for local socket use, the app may need:

```bash
export PGHOST=/run/postgresql
```

or the app configuration should be changed accordingly.

---

## 4. Validate or reject the firewall hypothesis without disabling `firewalld`

Now ask: **Can a firewall rule even affect this connection?**

---

### Case A: App uses a Unix socket

If `strace` shows `AF_UNIX`, firewall validation is simple:

```bash
systemctl status firewalld
firewall-cmd --state
```

Even if `firewalld` is active, a Unix-domain socket connection does not use IP filtering in the normal way. If the error is `Permission denied`, look instead at:

```bash
ls -ld /run/postgresql
ls -l /run/postgresql/.s.PGSQL.5432
id
sudo -u postgres psql -c 'select 1'
```

Also check SELinux or AppArmor:

```bash
getenforce
sudo ausearch -m avc -ts recent
sudo sealert -a /var/log/audit/audit.log
```

Conclusion: **Firewall hypothesis rejected unless the app is somehow using IP traffic.**

---

### Case B: App uses TCP to `127.0.0.1` or `::1`

Loopback traffic is usually trusted by default in `firewalld`, but custom rules or a nonstandard zone assignment can block it.

Check whether `firewalld` is active:

```bash
systemctl status firewalld
firewall-cmd --state
```

If `firewalld` is inactive, the hypothesis is likely rejected.

Check which zone is associated with loopback:

```bash
lozone=$(firewall-cmd --get-zone-of-interface=lo)
echo "lo zone: $lozone"
firewall-cmd --info-zone="$lozone"
```

Check active zones and their rules:

```bash
firewall-cmd --get-active-zones
firewall-cmd --list-all
```

If you know the app’s destination interface/IP, check the relevant zone:

```bash
iface=$(ip route get 127.0.0.1 | awk '{print $5; exit}')
zone=$(firewall-cmd --get-zone-of-interface="$iface")
firewall-cmd --info-zone="$zone"
```

For non-loopback addresses:

```bash
getent hosts "$(hostname)"
ip route get 127.0.0.1
ip route get 192.168.1.10
```

Then:

```bash
firewall-cmd --query-port=5432/tcp --zone="$zone"
firewall-cmd --query-service=postgresql --zone="$zone"
```

Check raw netfilter/nftables rules:

```bash
sudo iptables -S
sudo iptables -L -n -v
sudo nft list ruleset
```

Look specifically for PostgreSQL port and loopback rules:

```bash
sudo iptables -S | grep -E '5432|lo|REJECT|DROP'
sudo nft list ruleset | grep -A5 -B5 -E '5432|lo'
```

Use a packet capture on loopback while reproducing the error:

```bash
sudo tcpdump -i lo -nn -w /tmp/pg-lo.pcap 'tcp port 5432'
```

or monitor it:

```bash
sudo tcpdump -i lo -nn 'tcp port 5432'
```

Then trigger the application error.

Interpretation:

| Capture result | Meaning |
|---|---|
| No packets | App did not send network traffic; likely config/connection string/Unix socket path/sandboxing issue |
| SYN then SYN/ACK then app still fails | Network layer worked; failure is probably auth, SSL, driver, application logic |
| SYN then RST | Listener missing, wrong port/address family, or firewall/iptables REJECT |
| SYN only, no response | Likely DROP/blackhole, possible firewall or routing issue |
| ICMP port unreachable | Something is rejecting; may be no listener or REJECT rule |

If `firewalld` is active but no rule affects loopback or port 5432, the firewall hypothesis is weakened or rejected.

---

### Case C: App connects to the machine’s LAN IP, not loopback

If the app connects to something like `192.168.1.10:5432`, firewalld is more plausible.

Check the destination:

```bash
getent hosts "$(hostname)"
ip -br addr
ip route get 192.168.1.10
```

Find the interface and zone:

```bash
iface=$(ip route get 192.168.1.10 | awk '{print $5; exit}')
zone=$(firewall-cmd --get-zone-of-interface="$iface")
firewall-cmd --info-zone="$zone"
```

Check whether port is allowed:

```bash
firewall-cmd --query-port=5432/tcp --zone="$zone"
firewall-cmd --query-service=postgresql --zone="$zone"
```

Test from the same host:

```bash
nc -vz 192.168.1.10 5432
psql -h 192.168.1.10 -p 5432 -U app_user -d mydb -c 'select 1'
```

Test from another host:

```bash
nc -vz 192.168.1.10 5432
psql "host=192.168.1.10 port=5432 dbname=mydb user=app_user" -c 'select 1'
```

If the connection times out, a firewall rule becomes plausible. If the connection is refused, first check whether PostgreSQL is actually listening on that interface:

```bash
sudo ss -lntp | grep 5432
```

If PostgreSQL only listens on `127.0.0.1`, then LAN connections will fail regardless of the firewall.

If you need evidence from firewalld without disabling it, you can enable logging temporarily, if that is acceptable in your environment:

```bash
sudo firewall-cmd --set-log-denied=all
journalctl -k -f | grep -iE 'nf_log|firewall|denied'
```

Afterwards:

```bash
sudo firewall-cmd --set-log-denied=off
```

Do not use `systemctl disable firewalld` as a diagnostic step.

---

## 5. Check PostgreSQL authentication, because that is a very common “connection” failure

A firewall blocks packets. PostgreSQL rejects or accepts connections according to `pg_hba.conf`, roles, passwords, and SSL settings.

Find the config paths:

```bash
sudo -u postgres psql -c "SHOW config_file;"
sudo -u postgres psql -c "SHOW hba_file;"
```

Look at relevant `pg_hba.conf` rules:

```bash
sudo -u postgres psql -c "SELECT line_number, type, database, user_name, address, auth_method FROM pg_hba_rules();"
```

If that SQL view is not available:

```bash
sudo grep -vE '^\s*#|^\s*$' "$(sudo -u postgres psql -tAc 'SHOW hba_file')"
```

Typical local socket rule:

```text
local   all             all                                     peer
```

`peer` means the OS user must match the PostgreSQL username. If your application runs as `app_user` but connects to PostgreSQL user `app_user` using a Unix socket, that may work. But if it connects to PostgreSQL user `postgres`, it may fail with:

```text
Peer authentication failed for user "postgres"
```

That is not a firewall issue.

Typical TCP rules:

```text
host    all             all             127.0.0.1/32            scram-sha-256
host    all             all             ::1/128                 scram-sha-256
host    all             all             10.0.0.0/24             scram-sha-256
```

If the app gets:

```text
FATAL: no pg_hba.conf entry for host "127.0.0.1", user "app_user", database "mydb", SSL off
```

The request reached PostgreSQL, and the firewall did not block it.

After editing `pg_hba.conf`, reload PostgreSQL:

```bash
sudo systemctl reload postgresql
```

or from SQL:

```sql
SELECT pg_reload_conf();
```

---

## 6. Check file permissions and sandboxing for Unix socket connections

For Unix sockets, common failures are:

```text
could not connect to server: Permission denied
```

Check:

```bash
ls -ld /run/postgresql
ls -l /run/postgresql/.s.PGSQL.5432
stat /run/postgresql/.s.PGSQL.5432
```

Permissions often need to allow the client OS user to read/write the socket.

Typical:

```text
srwxrwxrwx 1 postgres postgres 0 ... /run/postgresql/.s.PGSQL.5432
```

But do not blindly set world-writable sockets on shared systems. Better approaches:

- Put the app user in the correct group.
- Use `peer` or `ident` authentication correctly.
- Use TCP loopback with proper `pg_hba.conf`.
- Run the app in a group that can access the socket directory.

Check SELinux/AppArmor:

```bash
getenforce
sudo ausearch -m avc -ts recent
sudo sealert -a /var/log/audit/audit.log
```

Check systemd sandboxing if the app is a service:

```bash
systemctl show myapp.service -p ProtectSystem,PrivateTmp,ProtectHome,ProtectSystem,IPAddressDeny,IPAddressAllow,IPAddressDeny,RestrictAddressFamilies
```

Relevant possibilities:

| Setting | Effect |
|---|---|
| `PrivateTmp=yes` | App may not see sockets under `/tmp` |
| `ProtectSystem=strict` | App may not access `/run` paths normally |
| `RestrictAddressFamilies=AF_UNIX` | Blocks Unix sockets |
| `IPAddressDeny=any` | May block local TCP connections |
| `ProtectHome=yes` | Can affect config/socket paths |

If the service is sandboxed, the firewall may be irrelevant.

---

## 7. Check container or namespace isolation

If the application or PostgreSQL runs in Docker, Podman, LXC, Kubernetes, or another namespace, “local” may not mean the same network namespace.

For Docker:

```bash
docker inspect -f '{{json .NetworkSettings.Networks}}' mycontainer
docker exec -it mycontainer psql -h localhost -p 5432 ...
```

If the container uses the host network:

```bash
docker inspect -f '{{.HostConfig.NetworkMode}}' mycontainer
```

If it uses a bridge network, `localhost` inside the container is not the host’s `localhost`.

For system-level namespaces:

```bash
ip netns list
ip netns exec <ns> ss -lntp | grep 5432
```

Firewalld rules on the host may or may not apply depending on netns and veth setup. The evidence must include which namespace the app actually uses.

---

## 8. Decision tree after collecting evidence

### Evidence: App uses Unix socket

Reject firewalld as the main hypothesis.

Continue by checking:

```bash
ls -l /run/postgresql
sudo -u postgres psql -c 'select 1'
psql -h /run/postgresql -U app_user -d mydb -c 'select 1'
getenforce
ausearch -m avc -ts recent
systemd sandbox options
```

---

### Evidence: PostgreSQL is not listening on the address the app uses

Reject firewalld for now.

Fix PostgreSQL:

```text
listen_addresses = 'localhost'
```

or:

```text
listen_addresses = '*'
```

Only if you actually intend to listen on all interfaces, and only with proper firewall/network restrictions.

Also check:

```bash
sudo systemctl status postgresql
journalctl -u postgresql
```

Then reload/restart and retest.

---

### Evidence: PostgreSQL listens, but app gets `Connection refused`

Likely causes, in rough order:

1. Wrong IP family: app uses `::1`, PostgreSQL only listens on `127.0.0.1`.
2. Wrong port.
3. Wrong host/IP.
4. Container/network namespace mismatch.
5. REJECT firewall rule.
6. Application uses a local proxy that is not running.

Use packet capture:

```bash
sudo tcpdump -i lo -nn 'tcp port 5432'
sudo tcpdump -i eth0 -nn 'tcp port 5432'
```

If you see SYN then RST and PostgreSQL is not listening, it is not a firewall DROP problem. If you see SYN then RST and PostgreSQL is listening, check for REJECT rules, wrong interface, or wrong listener.

---

### Evidence: App gets timeout

Firewall becomes more plausible, especially for non-loopback IP.

Check:

```bash
firewall-cmd --state
firewall-cmd --get-active-zones
firewall-cmd --query-port=5432/tcp --zone="$zone"
sudo nft list ruleset
tcpdump -i eth0 -nn 'tcp port 5432'
```

If you see SYN packets leaving but no response, and no PostgreSQL listener, fix the listener first. If PostgreSQL is listening and packets are blocked, fix the specific rule.

Do not disable firewalld globally. Instead, allow the needed service in the correct zone, ideally scoped:

```bash
firewall-cmd --zone="$zone" --add-service=postgresql --permanent
firewall-cmd --reload
```

Or for a specific port:

```bash
firewall-cmd --zone="$zone" --add-port=5432/tcp --permanent
firewall-cmd --reload
```

Even better, restrict source addresses if appropriate:

```bash
firewall-cmd --zone="$zone" --add-rich-rule='rule family="ipv4" source address="10.0.1.0/24" port protocol="tcp" port="5432" accept' --permanent
firewall-cmd --reload
```

---

### Evidence: App gets authentication failure

Reject firewalld. The traffic reached PostgreSQL.

Check:

```bash
journalctl -u postgresql --since "-10 minutes" --no-pager
```

Review:

```text
pg_hba.conf
user roles
passwords
SSL mode
database name
```

Test as different users:

```bash
sudo -u postgres psql -h localhost -c 'select 1'
psql -h localhost -U app_user -d mydb -c 'select 1'
```

If running locally as a different OS user using peer authentication:

```bash
psql -U app_user
```

may fail if the OS user does not match. Switch to the matching OS user or change authentication method to `scram-sha-256` over TCP.

---

## 9. What I would do in practice

My first checks would be:

```bash
psql "host=localhost port=5432 dbname=mydb user=app_user" -c 'select 1'
psql "host=127.0.0.1 port=5432 dbname=mydb user=app_user" -c 'select 1'
psql "host=/run/postgresql port=5432 dbname=mydb user=app_user" -c 'select 1'

sudo ss -lntp | grep 5432
sudo ss -xlnp | grep pgsql

systemctl status firewalld
firewall-cmd --state
firewall-cmd --get-active-zones
firewall-cmd --info-zone="$(firewall-cmd --get-zone-of-interface=lo)"

sudo -u postgres psql -c "SHOW listen_addresses;"
sudo -u postgres psql -c "SHOW port;"
sudo -u postgres psql -c "SHOW unix_socket_directories;"
sudo -u postgres psql -c "SHOW hba_file;"
journalctl -u postgresql --since "-15 minutes" --no-pager
```

Then I would choose the next step based on the result:

| Result | Next action |
|---|---|
| No TCP listener | Fix PostgreSQL listener/config |
| Unix socket in use | Check socket path, permissions, peer auth, SELinux/sandboxing |
| Loopback TCP works, app fails | Check app config, environment, IPv4/IPv6, SSL, proxy, credentials |
| `localhost` resolves to IPv6 but service IPv4-only | Force `127.0.0.1` or enable IPv6 listening |
| `pg_hba.conf` error | Fix PostgreSQL auth rules |
| TCP to LAN IP works only from loopback | Check `listen_addresses`, zone rules, container/network namespace |
| Confirmed firewalld DROP on correct interface | Add targeted allow rule, then reload firewalld |

---

## Bottom line

The firewall hypothesis should be tested, not assumed. For local PostgreSQL, the most common real causes are usually:

1. Wrong connection string/host.
2. Unix socket vs TCP confusion.
3. PostgreSQL not listening on the requested address.
4. `listen_addresses` misconfiguration.
5. `pg_hba.conf` authentication rules.
6. Peer authentication mismatch.
7. Unix socket file permissions.
8. IPv4/IPv6 mismatch.
9. SELinux/AppArmor/systemd sandboxing.
10. Container or namespace isolation.

Only after evidence shows blocked packets on the relevant interface should a firewall rule be added. The correct change is a targeted rule, not disabling `firewalld`.
