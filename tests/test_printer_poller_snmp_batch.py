"""
Tests for printer_poller's batched SNMP reads.

Each OID used to be its own snmp_get() call, and each of those built a fresh
event loop, SnmpEngine and UDP transport for a single GET. A poll cycle spent
~27 of those back to back; the Epson supply sweep alone was 12 sequential round
trips to read 5 ink levels, every five minutes.

These tests drive snmp_get_many against a fake pysnmp agent, counting the GETs
it receives. They pin three things:

  * a batch is one round trip, not one per OID
  * an agent that rejects multi-varbind GETs (tooBig) still gets correct
    answers, by falling back to the one-at-a-time behaviour this replaced
  * every requested OID comes back as a key, so a caller never sees a KeyError
    where the old per-OID call returned None
"""

import os
import sys
import types

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest


# ─────────────────────────────────────────────────────────────────────────────
# A fake pysnmp agent
# ─────────────────────────────────────────────────────────────────────────────

class _FakeAgent:
    """Records every GET and answers from a dict of {oid: value}.

    `max_varbinds` models an agent's reply-buffer limit: a larger PDU comes back
    as errStat=1 (tooBig), the real-world case the fallback exists for.
    """

    def __init__(self, values, max_varbinds=None, fail_all=False):
        self.values = values
        self.max_varbinds = max_varbinds
        self.fail_all = fail_all
        self.gets = []            # one entry per PDU: the list of OIDs asked
        self.engines_created = 0
        self.transports_created = 0

    @property
    def round_trips(self):
        return len(self.gets)

    def build_module(self):
        agent = self

        class SnmpEngine:
            def __init__(self):
                agent.engines_created += 1

        class CommunityData:
            def __init__(self, *a, **kw): pass

        class ContextData:
            def __init__(self, *a, **kw): pass

        class UdpTransportTarget:
            @classmethod
            async def create(cls, *a, **kw):
                agent.transports_created += 1
                return cls()

        class ObjectIdentity:
            def __init__(self, oid): self.oid = oid

        class ObjectType:
            def __init__(self, identity): self.identity = identity

        async def get_cmd(engine, auth, transport, ctx, *var_types):
            oids = [vt.identity.oid for vt in var_types]
            agent.gets.append(oids)
            if agent.fail_all:
                return ("timeout", 0, 0, [])
            if agent.max_varbinds is not None and len(oids) > agent.max_varbinds:
                return (None, 1, 0, [])          # errStat 1 == tooBig
            # A v2c agent answers per-varbind: an absent OID is prose, not an error.
            return (None, 0, 0,
                    [(o, agent.values.get(o, "No Such Instance currently exists"))
                     for o in oids])

        mod = types.ModuleType("pysnmp.hlapi.asyncio")
        for name, obj in [
            ("get_cmd", get_cmd), ("SnmpEngine", SnmpEngine),
            ("CommunityData", CommunityData), ("ContextData", ContextData),
            ("UdpTransportTarget", UdpTransportTarget),
            ("ObjectType", ObjectType), ("ObjectIdentity", ObjectIdentity),
        ]:
            setattr(mod, name, obj)
        return mod


@pytest.fixture
def agent(monkeypatch):
    """Install a fake pysnmp for the duration of one test."""
    def _install(values, **kw):
        a = _FakeAgent(values, **kw)
        pysnmp = types.ModuleType("pysnmp")
        hlapi = types.ModuleType("pysnmp.hlapi")
        asyncio_mod = a.build_module()
        hlapi.asyncio = asyncio_mod
        pysnmp.hlapi = hlapi
        monkeypatch.setitem(sys.modules, "pysnmp", pysnmp)
        monkeypatch.setitem(sys.modules, "pysnmp.hlapi", hlapi)
        monkeypatch.setitem(sys.modules, "pysnmp.hlapi.asyncio", asyncio_mod)
        return a
    return _install


@pytest.fixture
def pp():
    import printer_poller
    return printer_poller


# ─────────────────────────────────────────────────────────────────────────────
# Batching
# ─────────────────────────────────────────────────────────────────────────────

class TestBatching:
    def test_five_oids_cost_one_round_trip(self, pp, agent):
        oids = [f"1.3.6.1.2.1.43.10.2.1.4.1.{i}" for i in range(5)]
        a = agent({o: i * 10 for i, o in enumerate(oids)})

        got = pp.snmp_get_many("10.0.0.1", oids)

        assert got == {o: i * 10 for i, o in enumerate(oids)}
        assert a.round_trips == 1, "five OIDs should be one PDU, not five"

    def test_one_engine_and_transport_per_batch(self, pp, agent):
        """The old code built an engine and a transport per OID."""
        oids = [f"1.3.6.1.4.1.{i}" for i in range(8)]
        a = agent({o: 1 for o in oids})

        pp.snmp_get_many("10.0.0.1", oids)

        assert a.engines_created == 1
        assert a.transports_created == 1

    def test_batches_are_chunked(self, pp, agent):
        oids = [f"1.3.6.1.4.1.{i}" for i in range(25)]
        a = agent({o: 7 for o in oids})

        got = pp.snmp_get_many("10.0.0.1", oids, chunk_size=10)

        assert len(got) == 25
        assert a.round_trips == 3          # 10 + 10 + 5
        assert all(v == 7 for v in got.values())

    def test_absent_oid_is_none_not_an_error(self, pp, agent):
        """v2c reports a missing OID per-varbind, so the rest of the batch
        must still come back with real values."""
        a = agent({"1.1": 5})

        got = pp.snmp_get_many("10.0.0.1", ["1.1", "1.2"])

        assert got == {"1.1": 5, "1.2": None}
        assert a.round_trips == 1

    def test_every_requested_oid_is_a_key(self, pp, agent):
        agent({})
        got = pp.snmp_get_many("10.0.0.1", ["9.9.9", "8.8.8"])
        # A short dict would turn a caller's got[oid] into a KeyError where the
        # old per-OID call simply returned None.
        assert set(got) == {"9.9.9", "8.8.8"}
        assert got["9.9.9"] is None

    def test_empty_request_does_no_io(self, pp, agent):
        a = agent({})
        assert pp.snmp_get_many("10.0.0.1", []) == {}
        assert a.round_trips == 0


# ─────────────────────────────────────────────────────────────────────────────
# Degrading safely on a fussy agent
# ─────────────────────────────────────────────────────────────────────────────

class TestFallback:
    def test_too_big_falls_back_to_one_oid_at_a_time(self, pp, agent):
        """A printer that refuses multi-varbind GETs must still be read
        correctly — slower, never wrong. This is the whole reason the change is
        safe to ship without a printer to test against."""
        oids = ["1.1", "1.2", "1.3"]
        a = agent({"1.1": 10, "1.2": 20, "1.3": 30}, max_varbinds=1)

        got = pp.snmp_get_many("10.0.0.1", oids)

        assert got == {"1.1": 10, "1.2": 20, "1.3": 30}
        # 1 rejected batch + 3 singles
        assert a.round_trips == 4
        assert [len(g) for g in a.gets] == [3, 1, 1, 1]

    def test_unreachable_host_returns_all_none(self, pp, agent):
        a = agent({"1.1": 1}, fail_all=True)

        got = pp.snmp_get_many("10.0.0.1", ["1.1", "1.2"])

        assert got == {"1.1": None, "1.2": None}

    def test_single_oid_failure_does_not_retry_forever(self, pp, agent):
        """A one-OID chunk that fails has nothing to fall back to."""
        a = agent({}, fail_all=True)
        assert pp.snmp_get_many("10.0.0.1", ["1.1"]) == {"1.1": None}
        assert a.round_trips == 1

    def test_missing_pysnmp_returns_all_none(self, pp, monkeypatch):
        """The store PCs are the only boxes with pysnmp; everywhere else this
        must stay importable and quietly answer nothing."""
        monkeypatch.setitem(sys.modules, "pysnmp", types.ModuleType("pysnmp"))
        monkeypatch.delitem(sys.modules, "pysnmp.hlapi", raising=False)
        monkeypatch.delitem(sys.modules, "pysnmp.hlapi.asyncio", raising=False)

        assert pp.snmp_get_many("10.0.0.1", ["1.1", "1.2"]) == {"1.1": None, "1.2": None}


# ─────────────────────────────────────────────────────────────────────────────
# snmp_get still works for single-OID callers
# ─────────────────────────────────────────────────────────────────────────────

class TestSingleGet:
    def test_returns_the_value(self, pp, agent):
        agent({"1.2.3": 42})
        assert pp.snmp_get("10.0.0.1", "1.2.3") == 42

    def test_missing_oid_is_none(self, pp, agent):
        agent({})
        assert pp.snmp_get("10.0.0.1", "1.2.3") is None


# ─────────────────────────────────────────────────────────────────────────────
# Value coercion
# ─────────────────────────────────────────────────────────────────────────────

class TestSnmpValue:
    @pytest.mark.parametrize("raw,expected", [
        (42, 42),
        ("42", 42),
        ("-2", -2),            # printer MIB: unknown max capacity
        ("-3", -3),            # printer MIB: level OK but no count
        ("No Such Instance currently exists at this OID", None),
        ("No Such Object currently exists at this OID", None),
        ("", None),
        (None, None),
    ])
    def test_coercion(self, pp, raw, expected):
        assert pp._snmp_value(raw) == expected


# ─────────────────────────────────────────────────────────────────────────────
# poll_supplies: same answers, far fewer round trips
# ─────────────────────────────────────────────────────────────────────────────

class TestPollSupplies:
    def _supply_values(self, count):
        """A printer reporting `count` supplies at 50%."""
        vals = {}
        for i in range(1, count + 1):
            vals[f"1.3.6.1.2.1.43.11.1.1.8.1.{i}"] = 100
            vals[f"1.3.6.1.2.1.43.11.1.1.9.1.{i}"] = 50
        return vals

    def test_five_supplies_read_in_two_round_trips(self, pp, agent):
        """The Epson case: was 12 sequential GETs every cycle."""
        a = agent(self._supply_values(5))

        supplies = pp.poll_supplies("10.0.0.2", "epson")

        assert [s["supply_index"] for s in supplies] == [1, 2, 3, 4, 5]
        assert all(s["pct"] == 50.0 for s in supplies)
        assert a.round_trips == 2, "20 OIDs at chunk 10 should be two PDUs"

    def test_stops_at_the_first_empty_index(self, pp, agent):
        agent(self._supply_values(2))
        supplies = pp.poll_supplies("10.0.0.1", "konica")
        assert [s["supply_index"] for s in supplies] == [1, 2]

    def test_labels_are_applied(self, pp, agent):
        agent(self._supply_values(2))
        supplies = pp.poll_supplies("10.0.0.1", "konica")
        assert supplies[0]["description"] == "Toner Black"
        assert supplies[1]["description"] == "Drum Black"

    def test_unknown_max_capacity_leaves_pct_none(self, pp, agent):
        """Konica answers -2 for max capacity; pct must stay None rather than
        become a bogus percentage."""
        agent({
            "1.3.6.1.2.1.43.11.1.1.8.1.1": -2,
            "1.3.6.1.2.1.43.11.1.1.9.1.1": -3,
        })
        supplies = pp.poll_supplies("10.0.0.1", "konica")
        assert len(supplies) == 1
        assert supplies[0]["pct"] is None

    def test_unreachable_printer_yields_nothing(self, pp, agent):
        a = agent({}, fail_all=True)
        assert pp.poll_supplies("10.0.0.1", "epson") == []
        # Two chunks, each falling back to 10 singles: bounded, and no worse
        # than the old code's per-OID walk.
        assert a.round_trips > 0
