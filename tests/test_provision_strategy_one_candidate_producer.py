from __future__ import annotations

import pytest

from scripts.clickhouse import provision_strategy_one_candidate_producer as subject


class Admin:
    def __init__(self, *, present="0"):
        self.present, self.sql = present, []
    def execute(self, query):
        self.sql.append(query)
        if "FROM system.users" in query:
            return self.present
        if query.startswith(("CREATE USER", "GRANT ")):
            return ""
        raise AssertionError(query)


class Producer:
    def __init__(self, grants=()):
        self.grants = set(grants)
        self.closed = False
    def execute(self, query):
        if query == "SELECT currentUser()":
            return subject.PRINCIPAL
        if query == "SHOW GRANTS FINAL":
            return "\n".join(sorted(self.grants))
        raise AssertionError(query)
    def close(self):
        self.closed = True


def test_dry_run_is_nonconnecting(capsys, monkeypatch):
    monkeypatch.setattr(subject, "_admin_client", lambda _url:
                        pytest.fail("dry run connected"))
    assert subject.main([]) == 0
    text = capsys.readouterr().out
    assert "DRY RUN" in text and "No connection" in text


def test_new_producer_grants_only_two_owned_tables(monkeypatch):
    monkeypatch.setattr(subject, "verify_tables", lambda _admin: None)
    admin = Admin()
    producer = Producer()
    def apply_grant(query):
        answer = Admin.execute(admin, query)
        if query.startswith("GRANT "):
            producer.grants.add(query + f" TO {subject.PRINCIPAL}" if
                                " TO " not in query else query)
        return answer
    admin.execute = apply_grant
    subject.provision(admin, credential=lambda **_kwargs: "p" * 40,
                      client_factory=lambda _user, _password: producer)
    assert producer.closed
    assert len([sql for sql in admin.sql if sql.startswith("GRANT ")]) == 4
    assert not any("bars_v1" in sql or "indicators_v1" in sql or
                   "liquidity_100ms_v1" in sql for sql in admin.sql)


def test_existing_broad_grant_fails_before_new_grant(monkeypatch):
    monkeypatch.setattr(subject, "verify_tables", lambda _admin: None)
    admin = Admin(present="1")
    producer = Producer((f"GRANT INSERT ON arte.bars_v1 TO {subject.PRINCIPAL}",))
    with pytest.raises(RuntimeError, match="outside its two tables"):
        subject.provision(admin, credential=lambda **_kwargs: "p" * 40,
                          client_factory=lambda _user, _password: producer)
    assert producer.closed
    assert not any(sql.startswith("GRANT ") for sql in admin.sql)


def test_empty_private_credential_can_resume_only_before_account_creation(tmp_path, monkeypatch):
    path = tmp_path / "producer.env"
    path.touch()
    monkeypatch.setattr(subject, "SECRET_PATH", path)
    monkeypatch.setattr(subject, "_restrict_secret_file", lambda _path: None)
    monkeypatch.setattr(subject.secrets, "token_urlsafe", lambda _size: "p" * 48)
    assert subject._credential(account_exists=False) == "p" * 48
    assert subject._credential(account_exists=True) == "p" * 48
    path.write_text("", encoding="utf-8")
    with pytest.raises(RuntimeError, match="lacks private credential"):
        subject._credential(account_exists=True)
