# Managed ClickHouse and local Keeper on the workstation

The source in this directory is synchronized to
`D:\TradingML\runtimes\clickhouse-managed` on `DESKTOP-SAAI85T`. Run the
workstation copy, not the laptop repository copy. The launcher preserves the
existing physical-drive/UUID checks, mount chain, ClickHouse configuration,
and managed graceful stop. It adds one embedded Keeper member on TCP 9181.
Keeper's Raft log and snapshots are created only under the validated
`/clickhouse-disks/live-market/keeper` SSD path. No `arte` market table is
created or modified by these launchers.

The runtime copy contains no secrets. By default it reads the workstation's
existing `D:\quant_code\trading-dashboard\.env` for the password hashes and
network settings; `-SettingsPath` or `CLICKHOUSE_MANAGED_SETTINGS_PATH` can
point to a replacement secret file. The launcher does not copy that file.

On the workstation, after stopping live writers that rely on ClickHouse:

```powershell
$managed = 'D:\TradingML\runtimes\clickhouse-managed'
& (Join-Path $managed 'stop_clickhouse_wsl_managed_disks.ps1')
& (Join-Path $managed 'start_clickhouse_wsl_managed_disks.ps1')
```

Startup fails if the external disks, credential hashes, config overlay,
firewall block, ClickHouse port, or local Keeper `ruok` response fail their
checks. It creates and validates an inbound Windows Firewall block for TCP
9181 before changing the WSL disk state. This first deployment is local-only:
do not forward Keeper's unauthenticated port to the LAN. The direct trading
coordinator stays disabled until a secure client path with TLS/ACL and cold
recovery tests is validated.

After startup, verify through an authenticated ClickHouse account:

```sql
SELECT host, port, is_connected, is_readonly FROM system.zookeeper_info;
SELECT name FROM system.zookeeper WHERE path = '/';
SELECT name, path FROM system.disks WHERE name = 'live_market_ssd';
```

`system.zookeeper_info` may not be exposed by this ClickHouse build; the
`system.zookeeper` query and the bootstrap `ruok` check are the required
connection evidence. Confirm the workstation's TCP 9181 remains unreachable
from the laptop before admitting any trading client. The single-member Keeper
has no independent quorum redundancy: stopping the workstation stops both
ClickHouse and coordination. Existing live trading and Backtest still use
their guarded old paths until their typed journal cutover is separately
verified; enabling Keeper alone does not remove SQLite.

## Dedicated typed-journal principal

From the synchronized workstation code checkout, run
`scripts/clickhouse/provision_trading_journal.py` once without `--apply` to see
the account and exact-grant plan, then with `--apply` to provision it. The
command must run on `DESKTOP-SAAI85T`. It writes only ClickHouse access-control
records and a newly generated `D:\TradingML\secrets\trading_journal.env`;
it never changes market or journal tables. The credential file receives a
private Windows ACL before the password is written. An existing account is
reused only if that saved credential authenticates; no implicit rotation or
overwrite occurs. The final check logs in as the restricted user and verifies
that it cannot insert into market tables or alter schema. Do not enable a
runtime writer until the typed storage-placement and recovery checks pass.
