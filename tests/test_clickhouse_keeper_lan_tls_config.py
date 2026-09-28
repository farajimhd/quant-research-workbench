from pathlib import Path
from xml.etree import ElementTree


ROOT = Path(__file__).resolve().parents[1]
CLICKHOUSE = ROOT / "scripts" / "clickhouse"


def test_secure_keeper_overlay_is_strict_and_does_not_replace_private_port():
    private = ElementTree.parse(CLICKHOUSE / "config.d" / "50-trading-keeper.xml").getroot()
    secure = ElementTree.parse(CLICKHOUSE / "config.d" / "51-trading-keeper-lan-tls.xml").getroot()
    assert private.findtext("keeper_server/tcp_port") == "9181"
    assert secure.findtext("keeper_server/tcp_port_secure") == "9281"
    assert secure.findtext("openSSL/server/verificationMode") == "strict"
    assert secure.findtext("openSSL/server/invalidCertificateHandler/name") == "RejectCertificateHandler"
    for name in ("certificateFile", "privateKeyFile", "caConfig"):
        assert secure.findtext(f"openSSL/server/{name}").startswith(
            "/etc/clickhouse-server/keeper-tls/")
    bootstrap = (CLICKHOUSE / "clickhouse_bootstrap.sh").read_text(encoding="utf-8")
    assert 'CLICKHOUSE_KEEPER_LAN_TLS_ENABLED:-false' in bootstrap
    assert 'rm -f "$config_target/51-trading-keeper-lan-tls.xml"' in bootstrap
    launcher = (CLICKHOUSE / "start_clickhouse_on_mounted_disk_wsl.ps1").read_text(
        encoding="utf-8")
    assert "connectaddress=$($KeeperWslIp[0])" in launcher


def test_certificate_provisioner_keeps_private_keys_on_origin_hosts():
    source = (CLICKHOUSE / "provision_keeper_lan_tls.py").read_text(encoding="utf-8")
    assert 'LAPTOP_SECRET / "client.key"' in source
    assert 'f"{WSL_SECRET}/{name}" for name in ("ca.key", "ca.crt")' in source
    assert 'for name in ("ca.crt", "client.crt")' in source
    assert '"server.key"' in source
