from pathlib import Path

ROOT = Path(__file__).parents[1]
PYTHON_ROOTS = (ROOT / "atlas_core", ROOT / "atlas_api")


def _python_text() -> str:
    return "\n".join(
        path.read_text(encoding="utf-8", errors="replace")
        for root in PYTHON_ROOTS
        for path in root.rglob("*.py")
    )


def test_morning_is_not_embedded_in_atlas_runtime():
    assert not (ROOT / "atlas_morning").exists()
    assert not (ROOT / "atlas_core" / "morning").exists()
    text = _python_text().casefold()
    assert "operations.morning" not in text
    assert "atlas_morning" not in text


def test_semantic_mail_runtime_is_absent():
    assert not (ROOT / "atlas_core" / "mail.py").exists()
    text = _python_text()
    assert "MailRuntime" not in text
    assert "mail/connection" not in text


def test_legacy_authority_engines_are_absent():
    text = _python_text()
    for obsolete in (
        "AuthorityGrant", "required_authority", "operational_privilege",
        "communication.email.send", "ReasoningMesh", "TurnPolicy",
    ):
        assert obsolete not in text


def test_user_service_contains_startup_mechanics_not_owner_policy():
    unit = (ROOT / "deploy/systemd/user/atlas-api.service").read_text()
    assert "WantedBy=default.target" in unit
    assert "--instance-root /home/jaco/Projects/atlas-agent-state/production" in unit
    assert "User=" not in unit
    assert "Group=" not in unit
    assert "PrivateTmp=" not in unit
    assert "Polkit" not in unit
    assert "ATLAS_HOST_RESTART_ENABLED" not in unit
    assert "CONFIRM" not in unit


def test_companion_has_no_now_route():
    app = (ROOT / "companion/src/App.tsx").read_text()
    shell = (ROOT / "companion/src/ui/Shell.tsx").read_text()
    assert 'path="/now"' not in app
    assert "['/now'" not in shell
    assert "['/atlas', 'Atlas']" in shell
