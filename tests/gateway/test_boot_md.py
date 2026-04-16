import hashlib

from gateway.builtin_hooks import boot_md


def test_verify_boot_integrity_allows_when_unconfigured(monkeypatch):
    monkeypatch.delenv("BOOT_MD_SHA256", raising=False)
    assert boot_md._verify_boot_integrity("hello world") is True


def test_verify_boot_integrity_blocks_mismatch(monkeypatch):
    monkeypatch.setenv("BOOT_MD_SHA256", "deadbeef")
    assert boot_md._verify_boot_integrity("hello world") is False


def test_verify_boot_integrity_accepts_matching_hash(monkeypatch):
    content = "hello world"
    monkeypatch.setenv("BOOT_MD_SHA256", hashlib.sha256(content.encode("utf-8")).hexdigest())
    assert boot_md._verify_boot_integrity(content) is True
