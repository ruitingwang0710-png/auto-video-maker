"""ConfigStore 与 base_url 规范化测试。"""

import json
import os
import stat
from pathlib import Path

from auto_video_maker.infrastructure.config import (
    ConfigStore,
    LLMSettings,
    normalize_base_url,
)


class TestNormalizeBaseUrl:
    def test_strips_and_lowercases(self) -> None:
        assert normalize_base_url("  HTTPS://API.Example.COM/v1/  ") == "https://api.example.com/v1"

    def test_removes_trailing_slashes(self) -> None:
        assert normalize_base_url("https://a.com/v1///") == "https://a.com/v1"

    def test_path_case_preserved(self) -> None:
        assert normalize_base_url("https://a.com/V1") == "https://a.com/V1"

    def test_empty(self) -> None:
        assert normalize_base_url("") == ""
        assert normalize_base_url("   ") == ""


class TestConfigStore:
    def make_store(self, tmp_path: Path) -> ConfigStore:
        return ConfigStore(tmp_path / "conf" / "config.json")

    def test_missing_file_returns_defaults(self, tmp_path: Path) -> None:
        settings = self.make_store(tmp_path).load()
        assert settings == LLMSettings()
        assert settings.enabled is False

    def test_save_and_load_roundtrip(self, tmp_path: Path) -> None:
        store = self.make_store(tmp_path)
        settings = LLMSettings(
            enabled=True,
            base_url="https://api.example.com/v1",
            model="test-model",
            timeout_seconds=20.0,
            max_retries=1,
            privacy_confirmed_for_base_url="https://api.example.com/v1",
        )
        store.save(settings)
        assert store.load() == settings

    def test_no_api_key_field_in_file(self, tmp_path: Path) -> None:
        store = self.make_store(tmp_path)
        store.save(LLMSettings(enabled=True, base_url="https://a.com/v1", model="m"))
        raw = store.path.read_text(encoding="utf-8")
        assert "api_key" not in raw
        assert "key" not in json.loads(raw)

    def test_file_permissions_600(self, tmp_path: Path) -> None:
        store = self.make_store(tmp_path)
        store.save(LLMSettings())
        mode = stat.S_IMODE(os.stat(store.path).st_mode)
        assert mode == 0o600

    def test_atomic_write_no_temp_left(self, tmp_path: Path) -> None:
        store = self.make_store(tmp_path)
        store.save(LLMSettings())
        store.save(LLMSettings(enabled=True))
        leftovers = [p for p in store.path.parent.iterdir() if p.name != store.path.name]
        assert leftovers == []

    def test_corrupted_file_falls_back_to_defaults(self, tmp_path: Path) -> None:
        store = self.make_store(tmp_path)
        store.path.parent.mkdir(parents=True)
        store.path.write_text("{ 坏掉的 json", encoding="utf-8")
        assert store.load() == LLMSettings()

    def test_wrong_types_fall_back_to_defaults(self, tmp_path: Path) -> None:
        store = self.make_store(tmp_path)
        store.path.parent.mkdir(parents=True)
        store.path.write_text(
            json.dumps({"enabled": "yes", "base_url": 123, "timeout_seconds": -5,
                        "max_retries": "many"}),
            encoding="utf-8",
        )
        settings = store.load()
        assert settings.enabled is False
        assert settings.base_url == ""
        assert settings.timeout_seconds == LLMSettings().timeout_seconds
        assert settings.max_retries == LLMSettings().max_retries

    def test_missing_fields_use_defaults(self, tmp_path: Path) -> None:
        store = self.make_store(tmp_path)
        store.path.parent.mkdir(parents=True)
        store.path.write_text(json.dumps({"enabled": True}), encoding="utf-8")
        settings = store.load()
        assert settings.enabled is True
        assert settings.model == ""
        # TTS 隐私字段缺失时的默认值（旧配置兼容）
        assert settings.tts_privacy_confirmed is False
        assert settings.tts_privacy_provider == ""
        assert settings.tts_privacy_notice_version == 0

    def test_tts_privacy_fields_roundtrip(self, tmp_path: Path) -> None:
        store = self.make_store(tmp_path)
        settings = LLMSettings(
            tts_privacy_confirmed=True,
            tts_privacy_provider="edge-tts",
            tts_privacy_notice_version=1,
        )
        store.save(settings)
        loaded = store.load()
        assert loaded.tts_privacy_confirmed is True
        assert loaded.tts_privacy_provider == "edge-tts"
        assert loaded.tts_privacy_notice_version == 1

    def test_tts_privacy_fields_tolerant(self, tmp_path: Path) -> None:
        store = self.make_store(tmp_path)
        store.path.parent.mkdir(parents=True)
        store.path.write_text(
            json.dumps({"tts_privacy_confirmed": "yes",
                        "tts_privacy_notice_version": "one"}),
            encoding="utf-8",
        )
        settings = store.load()
        assert settings.tts_privacy_confirmed is False
        assert settings.tts_privacy_notice_version == 0
