"""Tests for the shop-LAN store_id override in store_config.

Every PC on a shop's LAN is that shop's store PC, so its store_id resolves to
that store regardless of what the local store_config.json says — guaranteeing
all of a shop's PCs attribute jobs/revenue/heartbeat to the one store.

    192.168.55.* -> OSP     (Oxygen, Thriprayar)
    192.168.1.*  -> PRINTK  (Printosky, Nattika)
"""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
import store_config as sc  # noqa: E402


def _cfg(store_id="XYZ"):
    raw = dict(sc._LEGACY_OXYGEN_DEFAULTS, store_id=store_id)
    return sc._build(raw, source="test")


class TestLanStoreMap:
    def test_default_map(self, monkeypatch):
        monkeypatch.delenv("LAN_STORE_MAP", raising=False)
        m = sc._lan_store_map()
        assert m["192.168.55."] == "OSP"
        assert m["192.168.1."] == "PRINTK"

    def test_env_override_parsed(self, monkeypatch):
        monkeypatch.setenv("LAN_STORE_MAP", "10.0.0.=OSP, 172.16.0.=PRINTK")
        assert sc._lan_store_map() == {"10.0.0.": "OSP", "172.16.0.": "PRINTK"}

    def test_empty_env_disables(self, monkeypatch):
        monkeypatch.setenv("LAN_STORE_MAP", "")
        assert sc._lan_store_map() == {}


class TestResolveLanStore:
    def test_oxygen_subnet_resolves_osp(self, monkeypatch):
        monkeypatch.delenv("LAN_STORE_MAP", raising=False)
        monkeypatch.setattr(sc, "_local_ipv4s", lambda probe=(): {"127.0.0.1", "192.168.55.212"})
        assert sc._resolve_lan_store() == "OSP"

    def test_printosky_subnet_resolves_printk(self, monkeypatch):
        monkeypatch.delenv("LAN_STORE_MAP", raising=False)
        monkeypatch.setattr(sc, "_local_ipv4s", lambda probe=(): {"127.0.0.1", "192.168.1.50"})
        assert sc._resolve_lan_store() == "PRINTK"

    def test_off_all_subnets_is_none(self, monkeypatch):
        monkeypatch.delenv("LAN_STORE_MAP", raising=False)
        monkeypatch.setattr(sc, "_local_ipv4s", lambda probe=(): {"127.0.0.1", "10.0.0.5"})
        assert sc._resolve_lan_store() is None

    def test_empty_map_is_none(self, monkeypatch):
        monkeypatch.setenv("LAN_STORE_MAP", "")
        monkeypatch.setattr(sc, "_local_ipv4s", lambda probe=(): {"192.168.55.212"})
        assert sc._resolve_lan_store() is None

    def test_192_168_1_not_matched_by_osp_prefix(self, monkeypatch):
        # Guard against a loose prefix: 192.168.1.* must be PRINTK, never OSP.
        monkeypatch.delenv("LAN_STORE_MAP", raising=False)
        monkeypatch.setattr(sc, "_local_ipv4s", lambda probe=(): {"192.168.1.212"})
        assert sc._resolve_lan_store() == "PRINTK"


class TestExemptStoreIds:
    def test_default_exempts_prioff(self, monkeypatch):
        monkeypatch.delenv("LAN_STORE_EXEMPT", raising=False)
        assert "PRIOFF" in sc._lan_exempt_store_ids()

    def test_env_override(self, monkeypatch):
        monkeypatch.setenv("LAN_STORE_EXEMPT", "PRIOFF, DEVBOX")
        assert sc._lan_exempt_store_ids() == {"PRIOFF", "DEVBOX"}


class TestApplyLanOverride:
    def test_forces_store_on_lan(self, monkeypatch):
        monkeypatch.setattr(sc, "_resolve_lan_store", lambda: "PRINTK")
        assert sc._apply_lan_override(_cfg("OSP")).store_id == "PRINTK"

    def test_prioff_dev_box_keeps_its_id_on_printk_subnet(self, monkeypatch):
        # The Nattika dev box shares 192.168.1.* with the Printosky store but
        # must stay PRIOFF, not be swept into PRINTK.
        monkeypatch.delenv("LAN_STORE_EXEMPT", raising=False)
        monkeypatch.setattr(sc, "_resolve_lan_store", lambda: "PRINTK")
        cfg = _cfg("PRIOFF")
        assert sc._apply_lan_override(cfg) is cfg
        assert sc._apply_lan_override(cfg).store_id == "PRIOFF"

    def test_matching_store_is_untouched(self, monkeypatch):
        monkeypatch.setattr(sc, "_resolve_lan_store", lambda: "OSP")
        cfg = _cfg("OSP")
        assert sc._apply_lan_override(cfg) is cfg

    def test_off_lan_keeps_config_store_id(self, monkeypatch):
        monkeypatch.setattr(sc, "_resolve_lan_store", lambda: None)
        assert sc._apply_lan_override(_cfg("PRINTK")).store_id == "PRINTK"

    def test_override_preserves_other_fields(self, monkeypatch):
        monkeypatch.setattr(sc, "_resolve_lan_store", lambda: "OSP")
        out = sc._apply_lan_override(_cfg("XYZ"))
        assert out.store_id == "OSP"
        assert out.printers.konica_ip == "192.168.55.110"
        assert out.hot_folder == sc._LEGACY_OXYGEN_DEFAULTS["hot_folder"]


class TestGetStoreConfigIntegration:
    def test_lan_forces_store_through_public_api(self, monkeypatch, tmp_path):
        # A config file that says OSP, but on the Printosky LAN -> PRINTK.
        cfgfile = tmp_path / "store_config.json"
        cfgfile.write_text('{"store_id": "OSP"}', encoding="utf-8")
        monkeypatch.setenv("PRINTOSKY_STORE_CONFIG", str(cfgfile))
        monkeypatch.setattr(sc, "_resolve_lan_store", lambda: "PRINTK")
        sc.get_store_config.cache_clear()
        try:
            assert sc.get_store_config().store_id == "PRINTK"
        finally:
            sc.get_store_config.cache_clear()
