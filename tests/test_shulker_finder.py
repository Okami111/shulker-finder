"""Tests: build small fake worlds on disk and check that everything is found.

Run from the repository folder with:  python -m unittest discover tests
"""
import gzip
import json
import os
import shutil
import struct
import sys
import tempfile
import unittest
import zlib
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import shulker_finder as sf  # noqa: E402


# --------------------------------------------------------------- NBT writer
# Values are (tag_type, payload) pairs so the exact NBT types can be chosen.
def B(v): return (1, v)
def I(v): return (3, v)
def D(v): return (6, v)
def S(v): return (8, v)
def L(elem_type, items):
    # Elements may be given typed, like D(1.0), or bare, like a dict for a compound.
    bare = [i[1] if isinstance(i, tuple) and len(i) == 2 and i[0] == elem_type else i for i in items]
    return (9, (elem_type, bare))
def C(d): return (10, d)
def IA(v): return (11, v)
def LA(raw): return (12, raw)


def _payload(t, v):
    if t == 1:
        return struct.pack(">b", v)
    if t == 3:
        return struct.pack(">i", v)
    if t == 6:
        return struct.pack(">d", v)
    if t == 8:
        b = v.encode("utf-8")
        return struct.pack(">H", len(b)) + b
    if t == 9:
        et, items = v
        return struct.pack(">bi", et, len(items)) + b"".join(_payload(et, i) for i in items)
    if t == 10:
        out = b""
        for name, (tt, vv) in v.items():
            nb = name.encode("utf-8")
            out += struct.pack(">bH", tt, len(nb)) + nb + _payload(tt, vv)
        return out + b"\x00"
    if t == 11:
        return struct.pack(">i%di" % len(v), len(v), *v)
    if t == 12:
        return struct.pack(">i", len(v) // 8) + v
    raise ValueError(t)


def nbt(root):
    """root is a plain dict of name -> typed value."""
    return b"\x0a\x00\x00" + _payload(10, root)


def write_region(path, chunks):
    """chunks: list of (index 0-1023, nbt bytes). Writes a zlib-compressed .mca file."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    header = bytearray(8192)
    body = b""
    sector = 2
    for idx, raw in chunks:
        data = zlib.compress(raw)
        blob = struct.pack(">IB", len(data) + 1, 2) + data
        blob += b"\x00" * (-len(blob) % 4096)
        n = len(blob) // 4096
        struct.pack_into(">I", header, idx * 4, (sector << 8) | n)
        body += blob
        sector += n
    with open(path, "wb") as f:
        f.write(bytes(header) + body)


def write_dat(path, root):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(gzip.compress(nbt(root)))


def block_states(palette, positions):
    """A 16x16x16 section with palette[1] at the given (x, y, z) local positions."""
    bits = max(4, (len(palette) - 1).bit_length())
    per = 64 // bits
    words = [0] * ((4096 + per - 1) // per)
    for x, y, z in positions:
        idx = (y << 8) | (z << 4) | x
        words[idx // per] |= 1 << ((idx % per) * bits)
    raw = b"".join(struct.pack(">Q", w) for w in words)
    return C({"palette": L(10, [{"Name": S(n)} for n in palette]), "data": LA(raw)})


# 1.20.5+ item format
def item(iid, count=1, name=None, inside=None, ench=None):
    d = {"id": S(iid), "count": I(count)}
    comps = {}
    if name:
        comps["minecraft:custom_name"] = S(name)
    if inside is not None:
        comps["minecraft:container"] = L(10, [{"slot": I(i), "item": C(it)} for i, it in enumerate(inside)])
    if ench:
        comps["minecraft:enchantments"] = C({k: I(v) for k, v in ench.items()})
    if comps:
        d["components"] = C(comps)
    return d


UUID_A = "0a1b2c3d-0000-4000-8000-000000000001"
UUID_B = "0a1b2c3d-0000-4000-8000-000000000002"


class ModernWorldTest(unittest.TestCase):
    """A 1.21-style world: dimensions/ folder, players/data, new item format."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        server = os.path.join(cls.tmp, "server")
        w = cls.world = os.path.join(server, "world")
        os.makedirs(w)
        with open(os.path.join(server, "server.properties"), "w") as f:
            f.write("level-name=world\n")
        with open(os.path.join(server, "usercache.json"), "w") as f:
            json.dump([{"name": "Alex", "uuid": UUID_A}], f)
        write_dat(os.path.join(w, "level.dat"), {"Data": C({})})

        # Overworld chunk: placed blue box at 120 64 -300, plus a chest with a red box.
        placed = {"id": S("minecraft:shulker_box"), "x": I(120), "y": I(64), "z": I(-300),
                  "CustomName": S('{"text":"Kit"}'),
                  "Items": L(10, [{"Slot": B(0), "id": S("minecraft:elytra"), "count": I(1)}])}
        chest = {"id": S("minecraft:chest"), "x": I(5), "y": I(70), "z": I(9),
                 "Items": L(10, [item("minecraft:red_shulker_box", inside=[item("minecraft:diamond", 64)])])}
        section = {"Y": B(4), "block_states": block_states(
            ["minecraft:air", "minecraft:blue_shulker_box"], [(120 & 15, 64 & 15, -300 & 15)])}
        chunk = {"sections": L(10, [section]), "block_entities": L(10, [placed, chest])}
        write_region(os.path.join(w, "dimensions/minecraft/overworld/region/r.0.-1.mca"), [(0, nbt(chunk))])

        # Entities: a dropped plain box and a chest minecart with a lime box.
        dropped = {"id": S("minecraft:item"), "Pos": L(6, [D(-40.5), D(63.0), D(812.2)]),
                   "Item": C(item("minecraft:shulker_box"))}
        cart = {"id": S("minecraft:chest_minecart"), "Pos": L(6, [D(10.5), D(12.0), D(10.5)]),
                "Items": L(10, [item("minecraft:lime_shulker_box")])}
        write_region(os.path.join(w, "dimensions/minecraft/the_nether/entities/r.-1.1.mca"),
                     [(5, nbt({"Entities": L(10, [dropped, cart])}))])

        # Players: Alex has a box in the ender chest; the other player has one inside a bundle.
        bundle = {"id": S("minecraft:bundle"), "count": I(1), "components": C({
            "minecraft:bundle_contents": L(10, [item("minecraft:purple_shulker_box", name="Loot")])})}
        write_dat(os.path.join(w, "players/data/%s.dat" % UUID_A), {
            "Pos": L(6, [D(1.0), D(70.0), D(2.0)]), "Dimension": S("minecraft:overworld"),
            "Inventory": L(10, [dict(item("minecraft:diamond_sword", ench={"minecraft:sharpness": 5}),
                                     Slot=B(0))]),
            "EnderItems": L(10, [dict(item("minecraft:green_shulker_box"), Slot=B(3))])})
        write_dat(os.path.join(w, "players/data/%s.dat" % UUID_B), {
            "Pos": L(6, [D(-5.0), D(80.0), D(5.0)]), "Dimension": S("minecraft:the_end"),
            "Inventory": L(10, [dict(bundle, Slot=B(10))])})

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def all_hits(self):
        warnings = []
        hits, files = sf.scan_world(self.world, warnings)
        hits += sf.player_hits(sf.load_players(self.world, warnings))
        self.assertEqual(warnings, [])
        return hits

    def find(self, hits, **want):
        found = [h for h in hits if all(h.get(k) == v for k, v in want.items())]
        self.assertEqual(len(found), 1, "expected one hit matching %r in %r" % (want, hits))
        return found[0]

    def test_placed_box_has_color_name_and_contents(self):
        h = self.find(self.all_hits(), kind="placed")
        self.assertEqual((h["pos"], h["color"], h["name"], h["dimension"]), ((120, 64, -300), "blue", "Kit", "Overworld"))
        self.assertEqual(sf.summarize(h["contents"]), "1 elytra")

    def test_box_in_chest(self):
        h = self.find(self.all_hits(), kind="container")
        self.assertEqual((h["pos"], h["color"], h["where"]), ((5, 70, 9), "red", "in a chest"))
        self.assertEqual(sf.summarize(h["contents"]), "64 diamond")

    def test_entities(self):
        hits = self.all_hits()
        drop = self.find(hits, kind="entity", color="plain")
        self.assertEqual((drop["pos"], drop["where"], drop["dimension"]), ((-41, 63, 812), "dropped on the ground", "Nether"))
        cart = self.find(hits, kind="entity", color="lime")
        self.assertEqual(cart["where"], "in a chest minecart")

    def test_players(self):
        hits = self.all_hits()
        alex = self.find(hits, kind="player", player="Alex")
        self.assertEqual((alex["where"], alex["color"]), ("ender chest slot 4", "green"))
        other = self.find(hits, kind="player", player=UUID_B)
        self.assertEqual((other["where"], other["name"]), ("inventory slot 10 (inside a bundle)", "Loot"))

    def test_total_and_filters(self):
        hits = self.all_hits()
        self.assertEqual(len(hits), 6)
        self.assertEqual([h["color"] for h in sf.filter_hits(hits, contains="elytra")], ["blue"])
        self.assertEqual([h["color"] for h in sf.filter_hits(hits, name="loot")], ["purple"])

    def test_resolves_server_folder_and_inner_folder(self):
        self.assertEqual(sf.resolve_world(os.path.dirname(self.world)), self.world)
        self.assertEqual(sf.resolve_world(os.path.join(self.world, "dimensions", "minecraft")), self.world)

    def test_cli_both_modes(self):
        report_path = os.path.join(self.tmp, "report.txt")
        out = StringIO()
        with redirect_stdout(out), redirect_stderr(StringIO()):
            sf.main([os.path.dirname(self.world), "-o", report_path])
        self.assertIn("Found 6 shulker boxes.", out.getvalue())
        self.assertTrue(os.path.isfile(report_path))
        out = StringIO()
        with redirect_stdout(out), redirect_stderr(StringIO()):
            sf.main([self.world, "--inventories", "--player", "alex", "-o", "-"])
        text = out.getvalue()
        self.assertIn("diamond sword [sharpness 5]", text)
        self.assertNotIn(UUID_B + "\n", text)


class LegacyWorldTest(unittest.TestCase):
    """A pre-1.20.5 world: region/ and DIM-1/region, playerdata/, old item format, host in level.dat."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        w = cls.world = os.path.join(cls.tmp, "My World")

        def old_item(iid, count=1, name=None, inside=None):
            d = {"id": S(iid), "Count": B(count)}
            tag = {}
            if name:
                tag["display"] = C({"Name": S(json.dumps({"text": name}))})
            if inside:
                tag["BlockEntityTag"] = C({"Items": L(10, [dict(old_item(*i), Slot=B(n)) for n, i in enumerate(inside)])})
            if tag:
                d["tag"] = C(tag)
            return d

        host = {"UUID": IA([1, 2, 3, 4]), "Pos": L(6, [D(0.5), D(64.0), D(0.5)]), "Dimension": S("minecraft:overworld"),
                "Inventory": L(10, [dict(old_item("minecraft:orange_shulker_box", 1, "Tools", [("minecraft:torch", 64)]), Slot=B(0))])}
        write_dat(os.path.join(w, "level.dat"), {"Data": C({"Player": C(host)})})
        barrel = {"id": S("minecraft:barrel"), "x": I(-20), "y": I(30), "z": I(40),
                  "Items": L(10, [dict(old_item("minecraft:black_shulker_box"), Slot=B(0))])}
        write_region(os.path.join(w, "DIM-1/region/r.-1.0.mca"),
                     [(0, nbt({"Level": C({"TileEntities": L(10, [barrel])})}))])
        write_dat(os.path.join(w, "playerdata/%s.dat" % UUID_A), {
            "Pos": L(6, [D(9.0), D(9.0), D(9.0)]), "Dimension": I(-1),
            "EnderItems": L(10, [dict(old_item("minecraft:white_shulker_box"), Slot=B(0))])})

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_everything(self):
        warnings = []
        hits, _ = sf.scan_world(self.world, warnings)
        players = sf.load_players(self.world, warnings)
        hits += sf.player_hits(players)
        self.assertEqual(warnings, [])
        by_color = {h["color"]: h for h in hits}
        self.assertEqual(sorted(by_color), ["black", "orange", "white"])
        self.assertEqual((by_color["black"]["dimension"], by_color["black"]["where"]), ("Nether", "in a barrel"))
        self.assertEqual(by_color["orange"]["player"], "single-player host")
        self.assertEqual(by_color["orange"]["name"], "Tools")
        self.assertEqual(sf.summarize(by_color["orange"]["contents"]), "64 torch")
        self.assertEqual(by_color["white"]["dimension"], "Nether")


if __name__ == "__main__":
    unittest.main()
