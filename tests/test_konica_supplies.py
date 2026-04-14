"""
TDD tests for S9-1: Konica supply level parsing from bizhub XML.

Bug: poll_supplies() uses SNMP which returns max_cap=-2 for Konica,
     so pct is always None — supply levels never reported.

Fix: parse_konica_xml_supplies(xml_text) extracts toner/drum %
     from the Konica HTTP XML response.
"""

import sys
import os
import types

# ── Stub pysnmp and requests so printer_poller can be imported ────────────────
for mod in ("pysnmp", "requests"):
    if mod not in sys.modules:
        sys.modules[mod] = types.ModuleType(mod)

# stub whatsapp_notify for _send_ink_alerts (only if not already stubbed)
if "whatsapp_notify" not in sys.modules:
    _wn = types.ModuleType("whatsapp_notify")
    _wn.send_staff_alert = lambda msg: True  # type: ignore
    sys.modules["whatsapp_notify"] = _wn

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from printer_poller import parse_konica_xml_supplies


# ─────────────────────────────────────────────────────────────────────────────
# Sample XML fixtures — bizhub flat-tag format (confirmed for Pro 1100)
# ─────────────────────────────────────────────────────────────────────────────

FLAT_TONER_XML = """<?xml version="1.0" encoding="UTF-8"?>
<DeviceStatus>
  <TnrBlkRmng>72</TnrBlkRmng>
  <DrmBlkRmng>45</DrmBlkRmng>
</DeviceStatus>
"""

FLAT_TONER_LOW_XML = """<?xml version="1.0" encoding="UTF-8"?>
<DeviceStatus>
  <TnrBlkRmng>5</TnrBlkRmng>
  <DrmBlkRmng>91</DrmBlkRmng>
</DeviceStatus>
"""

FLAT_TONER_ZERO_XML = """<?xml version="1.0" encoding="UTF-8"?>
<DeviceStatus>
  <TnrBlkRmng>0</TnrBlkRmng>
</DeviceStatus>
"""

# Alternate tag style some bizhub firmware versions use
ALT_TAG_XML = """<?xml version="1.0" encoding="UTF-8"?>
<sys_data>
  <TonerBlack>68</TonerBlack>
</sys_data>
"""

# Toner as a percentage string with % sign
PCT_SIGN_XML = """<?xml version="1.0" encoding="UTF-8"?>
<DeviceStatus>
  <TnrBlkRmng>33%</TnrBlkRmng>
</DeviceStatus>
"""

EMPTY_XML = """<?xml version="1.0"?>
<DeviceStatus><TotalCounter>123456</TotalCounter></DeviceStatus>
"""


# ─────────────────────────────────────────────────────────────────────────────
# Tests
# ─────────────────────────────────────────────────────────────────────────────

class TestParseKonicaXmlSupplies:

    def test_returns_list(self):
        result = parse_konica_xml_supplies(FLAT_TONER_XML)
        assert isinstance(result, list)

    def test_toner_black_extracted(self):
        result = parse_konica_xml_supplies(FLAT_TONER_XML)
        labels = [s["description"] for s in result]
        assert any("Toner" in l and "Black" in l for l in labels), (
            f"No toner black entry in {labels}"
        )

    def test_toner_black_pct_correct(self):
        result = parse_konica_xml_supplies(FLAT_TONER_XML)
        toner = next(s for s in result if "Toner" in s["description"] and "Black" in s["description"])
        assert toner["pct"] == 72.0

    def test_drum_black_extracted(self):
        result = parse_konica_xml_supplies(FLAT_TONER_XML)
        labels = [s["description"] for s in result]
        assert any("Drum" in l for l in labels), f"No drum entry in {labels}"

    def test_drum_black_pct_correct(self):
        result = parse_konica_xml_supplies(FLAT_TONER_XML)
        drum = next(s for s in result if "Drum" in s["description"])
        assert drum["pct"] == 45.0

    def test_low_toner_returned(self):
        result = parse_konica_xml_supplies(FLAT_TONER_LOW_XML)
        toner = next(s for s in result if "Toner" in s["description"] and "Black" in s["description"])
        assert toner["pct"] == 5.0

    def test_zero_toner_returned(self):
        result = parse_konica_xml_supplies(FLAT_TONER_ZERO_XML)
        toner = next(s for s in result if "Toner" in s["description"] and "Black" in s["description"])
        assert toner["pct"] == 0.0

    def test_alt_tag_toner_extracted(self):
        """Firmware variant that uses TonerBlack instead of TnrBlkRmng."""
        result = parse_konica_xml_supplies(ALT_TAG_XML)
        assert len(result) >= 1
        toner = next((s for s in result if "Toner" in s["description"] and "Black" in s["description"]), None)
        assert toner is not None, "TonerBlack tag not parsed"
        assert toner["pct"] == 68.0

    def test_pct_sign_stripped(self):
        """Some firmware returns '33%' — strip the % sign."""
        result = parse_konica_xml_supplies(PCT_SIGN_XML)
        toner = next(s for s in result if "Toner" in s["description"] and "Black" in s["description"])
        assert toner["pct"] == 33.0

    def test_empty_xml_returns_empty_list(self):
        result = parse_konica_xml_supplies(EMPTY_XML)
        assert result == []

    def test_malformed_xml_returns_empty_list(self):
        result = parse_konica_xml_supplies("<broken><unclosed>")
        assert result == []

    def test_supply_index_is_int(self):
        result = parse_konica_xml_supplies(FLAT_TONER_XML)
        for s in result:
            assert isinstance(s["supply_index"], int)

    def test_required_keys_present(self):
        result = parse_konica_xml_supplies(FLAT_TONER_XML)
        required = {"supply_index", "description", "pct", "max_capacity", "current_level"}
        for s in result:
            assert required.issubset(s.keys()), f"Missing keys in {s}"

    def test_pct_in_valid_range(self):
        result = parse_konica_xml_supplies(FLAT_TONER_XML)
        for s in result:
            if s["pct"] is not None:
                assert 0.0 <= s["pct"] <= 100.0
