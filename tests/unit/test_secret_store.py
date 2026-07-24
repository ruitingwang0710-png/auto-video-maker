"""SecretStore 测试（FakeSecretStore；不访问真实钥匙串）。"""

from auto_video_maker.infrastructure.secret_store import (
    FakeSecretStore,
    InMemorySecretStore,
    secret_id_for_base_url,
)


class TestSecretIdForBaseUrl:
    def test_derived_from_normalized_url(self) -> None:
        a = secret_id_for_base_url("https://API.Example.com/v1/")
        b = secret_id_for_base_url("  https://api.example.com/v1  ")
        assert a == b

    def test_different_urls_different_ids(self) -> None:
        assert secret_id_for_base_url("https://a.com/v1") != secret_id_for_base_url(
            "https://b.com/v1"
        )

    def test_id_is_sha256_hex_without_key_material(self) -> None:
        secret_id = secret_id_for_base_url("https://a.com/v1")
        assert len(secret_id) == 64
        assert all(c in "0123456789abcdef" for c in secret_id)


class TestInMemorySecretStore:
    def test_set_get_exists_delete(self) -> None:
        store = InMemorySecretStore()
        sid = secret_id_for_base_url("https://a.com/v1")
        assert store.get(sid) is None
        assert not store.exists(sid)
        store.set(sid, "secret-value")
        assert store.exists(sid)
        assert store.get(sid) == "secret-value"
        store.delete(sid)
        assert store.get(sid) is None
        assert not store.exists(sid)

    def test_delete_missing_is_silent(self) -> None:
        InMemorySecretStore().delete("missing")

    def test_keys_isolated_per_base_url(self) -> None:
        """切换 base_url 不得误用旧地址的 Key（测试要求 18）。"""
        store = FakeSecretStore()
        sid_a = secret_id_for_base_url("https://service-a.com/v1")
        sid_b = secret_id_for_base_url("https://service-b.com/v1")
        store.set(sid_a, "key-for-a")
        # 新地址没有 Key：显示未配置，不复用旧 Key
        assert not store.exists(sid_b)
        assert store.get(sid_b) is None
        # 切回旧地址仍可读取原 Key
        assert store.get(sid_a) == "key-for-a"
        # 为新地址保存 Key 后互不影响
        store.set(sid_b, "key-for-b")
        assert store.get(sid_a) == "key-for-a"
        assert store.get(sid_b) == "key-for-b"
