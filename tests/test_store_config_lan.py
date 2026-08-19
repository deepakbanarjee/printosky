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


class TestPollPrinters:
    """One machine per physical printer owns polling.

    Nattika ran two boxes against one Epson for weeks: identical job rows stored
    under both PRINTK and PRIOFF, and two sets of counters for one printer.
    """

    def test_defaults_to_true_so_a_single_store_pc_is_unaffected(self):
        cfg = sc._build(dict(sc._LEGACY_OXYGEN_DEFAULTS), source=None)
        assert cfg.poll_printers is True

    def test_absent_key_defaults_to_true(self):
        raw = sc._merge_with_defaults({"store_id": "OSP", "store_name": "x"})
        raw.pop("poll_printers", None)
        assert sc._build(raw, source=None).poll_printers is True

    def test_false_is_honoured(self):
        raw = sc._merge_with_defaults({
            "store_id": "PRIOFF", "store_name": "Printosky Office, Nattika",
            "poll_printers": False,
        })
        assert sc._build(raw, source=None).poll_printers is False


class TestShippedStoreConfigs:
    """The per-location templates in config/stores are the source of truth for
    what each machine should be set to; keep them honest."""

    import json as _json
    import pathlib as _pathlib

    STORES = _pathlib.Path(__file__).resolve().parent.parent / "config" / "stores"

    def _load(self, name):
        return self._json.loads((self.STORES / name).read_text(encoding="utf-8"))

    def test_osp_has_both_printers_and_polls(self):
        cfg = self._load("OSP.store_config.json")
        assert cfg["store_id"] == "OSP"
        assert cfg["printers"]["konica_ip"] == "192.168.55.110"
        assert cfg["printers"]["epson_ip"] == "192.168.55.214"
        assert cfg["poll_printers"] is True

    def test_nattika_counter_has_no_konica_and_the_current_epson_ip(self):
        cfg = self._load("PRINTK.store_config.json")
        assert cfg["store_id"] == "PRINTK"
        assert cfg["printers"]["konica_ip"] is None
        assert cfg["printers"]["epson_ip"] == "192.168.1.240"
        assert cfg["poll_printers"] is True

    def test_nattika_office_shares_the_printer_and_must_not_poll(self):
        cfg = self._load("PRIOFF.store_config.json")
        assert cfg["store_id"] == "PRIOFF"
        assert cfg["printers"]["konica_ip"] is None
        assert cfg["poll_printers"] is False

    def test_only_one_machine_polls_each_epson(self):
        pollers = {}
        for f in self.STORES.glob("*.json"):
            cfg = self._json.loads(f.read_text(encoding="utf-8"))
            if cfg.get("poll_printers", True):
                ip = cfg["printers"]["epson_ip"]
                assert ip not in pollers, (
                    f"{f.name} and {pollers[ip]} both poll the Epson at {ip} — "
                    "that double-counts its page counters and imports every job twice")
                pollers[ip] = f.name

    def test_every_template_parses_as_a_store_config(self):
        for f in self.STORES.glob("*.json"):
            raw = sc._merge_with_defaults(self._json.loads(f.read_text(encoding="utf-8")))
            cfg = sc._build(raw, source=str(f))
            assert cfg.store_id and cfg.store_name and cfg.db_path
