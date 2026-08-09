"""Tests for the Oxygen-LAN store_id override in store_config.

Every PC on the Oxygen shop LAN (192.168.55.0/24) is an Oxygen store PC, so its
store_id must resolve to OSP regardless of what the local store_config.json says
— guaranteeing all shop PCs attribute jobs/revenue/heartbeat to the one store.
"""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
import store_config as sc  # noqa: E402


def _cfg(store_id="PRINTK"):
    raw = dict(sc._LEGACY_OXYGEN_DEFAULTS, store_id=store_id)
    return sc._build(raw, source="test")


class TestOnOxygenLan:
    def test_detects_lan_from_local_ips(self, monkeypatch):
        monkeypatch.setattr(sc, "_local_ipv4s", lambda: {"127.0.0.1", "192.168.55.212"})
        assert sc._on_oxygen_lan() is True

    def test_off_lan_is_false(self, monkeypatch):
        monkeypatch.setattr(sc, "_local_ipv4s", lambda: {"127.0.0.1", "10.0.0.5"})
        assert sc._on_oxygen_lan() is False

    def test_empty_prefix_disables_rule(self, monkeypatch):
        monkeypatch.setattr(sc, "OXYGEN_LAN_PREFIX", "")
        monkeypatch.setattr(sc, "_local_ipv4s", lambda: {"192.168.55.212"})
        assert sc._on_oxygen_lan() is False


class TestApplyLanOverride:
    def test_forces_osp_on_lan(self, monkeypatch):
        monkeypatch.setattr(sc, "_on_oxygen_lan", lambda: True)
        assert _apply(sc, _cfg("PRINTK")).store_id == "OSP"

    def test_already_osp_is_untouched(self, monkeypatch):
        # No override needed; returns the same object.
        monkeypatch.setattr(sc, "_on_oxygen_lan", lambda: True)
        cfg = _cfg("OSP")
        assert sc._apply_lan_override(cfg) is cfg

    def test_off_lan_keeps_config_store_id(self, monkeypatch):
        monkeypatch.setattr(sc, "_on_oxygen_lan", lambda: False)
        assert sc._apply_lan_override(_cfg("PRINTK")).store_id == "PRINTK"

    def test_override_preserves_other_fields(self, monkeypatch):
        monkeypatch.setattr(sc, "_on_oxygen_lan", lambda: True)
        out = sc._apply_lan_override(_cfg("PRINTK"))
        assert out.store_id == "OSP"
        assert out.printers.konica_ip == "192.168.55.110"
        assert out.hot_folder == sc._LEGACY_OXYGEN_DEFAULTS["hot_folder"]


class TestGetStoreConfigIntegration:
    def test_lan_forces_osp_through_public_api(self, monkeypatch, tmp_path):
        # A config file that says PRINTK, but on the LAN -> OSP.
        cfgfile = tmp_path / "store_config.json"
        cfgfile.write_text('{"store_id": "PRINTK"}', encoding="utf-8")
        monkeypatch.setenv("PRINTOSKY_STORE_CONFIG", str(cfgfile))
        monkeypatch.setattr(sc, "_on_oxygen_lan", lambda: True)
        sc.get_store_config.cache_clear()
        try:
            assert sc.get_store_config().store_id == "OSP"
        finally:
            sc.get_store_config.cache_clear()


def _apply(mod, cfg):
    return mod._apply_lan_override(cfg)
