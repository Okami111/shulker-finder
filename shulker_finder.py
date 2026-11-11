#!/usr/bin/env python3
"""
Shulker Finder: find every shulker box in a Minecraft: Java Edition world,
and see what every player is carrying.

    python shulker_finder.py "<world folder>"                  find every shulker box
    python shulker_finder.py "<world folder>" --inventories    list every player's items

Run with --help for all options. It only READS your world, and it needs
nothing but Python 3.8 or newer.

https://github.com/Okami111/shulker-finder
"""

import argparse
import gzip
import json
import math
import os
import re
import struct
import sys
import zlib
from collections import Counter

__version__ = "1.0.0"

NEEDLE = b"shulker_box"  # quick text check so only chunks with shulkers get fully parsed


# ============================================================== NBT reading
class LongArray(bytes):
    """The raw big-endian bytes of an NBT long array (decoded only when needed)."""


class _Reader(object):
    __slots__ = ("d", "i")

    def __init__(self, data):
        self.d = data
        self.i = 0

    def take(self, n):
        b = self.d[self.i:self.i + n]
        self.i += n
        return b

    def unpack(self, fmt, size):
        v = struct.unpack_from(fmt, self.d, self.i)[0]
        self.i += size
        return v


_SIMPLE = {1: (">b", 1), 2: (">h", 2), 3: (">i", 4), 4: (">q", 8), 5: (">f", 4), 6: (">d", 8)}


def _payload(r, t):
    simple = _SIMPLE.get(t)
    if simple:
        return r.unpack(*simple)
    if t == 8:  # string
        return r.take(r.unpack(">H", 2)).decode("utf-8", "replace")
    if t == 10:  # compound
        out = {}
        while True:
            tt = r.unpack(">b", 1)
            if tt == 0:
                return out
            name = r.take(r.unpack(">H", 2)).decode("utf-8", "replace")
            out[name] = _payload(r, tt)
    if t == 9:  # list
        et = r.unpack(">b", 1)
        n = r.unpack(">i", 4)
        return [_payload(r, et) for _ in range(max(n, 0))]
    if t == 7:  # byte array: not needed, skip it
        n = r.unpack(">i", 4)
        r.i += n
        return None
    if t == 11:  # int array (UUIDs)
        n = r.unpack(">i", 4)
        v = struct.unpack_from(">%di" % n, r.d, r.i)
        r.i += 4 * n
        return v
    if t == 12:  # long array (block data)
        return LongArray(r.take(8 * r.unpack(">i", 4)))
    raise ValueError("bad NBT tag type %d" % t)


def read_nbt(data):
    """Parse uncompressed NBT bytes into Python dicts and lists."""
    r = _Reader(data)
    t = r.unpack(">b", 1)
    name_len = r.unpack(">H", 2)
    r.i += name_len  # skip the root name
    return _payload(r, t)


def read_dat(path):
    """Read a .dat file, which may or may not be gzipped."""
    with open(path, "rb") as f:
        raw = f.read()
    try:
        return gzip.decompress(raw)
    except OSError:
        return raw


# =========================================================== region files
def _decompress(ctype, payload):
    if ctype == 1:
        return gzip.decompress(payload)
    if ctype == 2:
        return zlib.decompress(payload)
    if ctype == 3:
        return payload
    if ctype == 4:
        try:
            import lz4.block  # only for worlds set to LZ4 compression
        except ImportError:
            raise RuntimeError("this world uses LZ4 compression; run: pip install lz4")
        return _lz4_java(payload, lz4.block)
    raise RuntimeError("unknown chunk compression type %d" % ctype)


def _lz4_java(data, lz4block):
    """Decode the LZ4 block-stream format Minecraft uses."""
    out = bytearray()
    i = 0
    while i + 21 <= len(data) and data[i:i + 8] == b"LZ4Block":
        token = data[i + 8]
        clen, dlen = struct.unpack_from("<ii", data, i + 9)
        i += 21
        if dlen == 0:
            break
        block = data[i:i + clen]
        i += clen
        out += block if token & 0xF0 == 0x10 else lz4block.decompress(block, uncompressed_size=dlen)
    return bytes(out)


_REGION_NAME = re.compile(r"^r\.(-?\d+)\.(-?\d+)\.mca$")


def region_chunks(path, warnings):
    """Yield the decompressed NBT bytes of every chunk in an .mca file."""
    with open(path, "rb") as f:
        data = f.read()
    if len(data) < 8192:
        return
    m = _REGION_NAME.match(os.path.basename(path))
    rx, rz = (int(m.group(1)), int(m.group(2))) if m else (0, 0)
    for idx in range(1024):
        loc = struct.unpack_from(">I", data, idx * 4)[0]
        off, sectors = (loc >> 8) * 4096, loc & 0xFF
        if off == 0 or sectors == 0 or off + 5 > len(data):
            continue
        length = struct.unpack_from(">I", data, off)[0]
        ctype = data[off + 4]
        try:
            if ctype & 0x80:  # oversized chunk kept in its own .mcc file
                cx, cz = rx * 32 + idx % 32, rz * 32 + idx // 32
                with open(os.path.join(os.path.dirname(path), "c.%d.%d.mcc" % (cx, cz)), "rb") as f:
                    yield _decompress(ctype & 0x7F, f.read())
            else:
                yield _decompress(ctype, data[off + 5:off + 4 + length])
        except Exception as e:
            warnings.append("skipped a chunk in %s: %s" % (os.path.basename(path), e))


# ============================================================ item helpers
DIM_LABELS = {"overworld": "Overworld", "the_nether": "Nether", "the_end": "End"}


def short_id(item_id):
    return item_id.split(":")[-1] if isinstance(item_id, str) else "?"


def nice(item_id):
    return short_id(item_id).replace("_", " ")


def article(word):
    return ("an " if word[:1].lower() in "aeiou" else "a ") + word


def box_color(block_or_item_id):
    s = short_id(block_or_item_id)
    if s == "shulker_box":
        return "plain"
    if s.endswith("_shulker_box"):
        return s[:-len("_shulker_box")].replace("_", " ")
    return "?"


def text_of(value):
    """Turn a custom name (plain text, JSON text or a text compound) into a string."""
    if isinstance(value, dict):
        s = str(value.get("text", ""))
        for part in value.get("extra") or []:
            s += text_of(part)
        return s
    if isinstance(value, list):
        return "".join(text_of(p) for p in value)
    if isinstance(value, str):
        if value[:1] in ('{', '[', '"'):
            try:
                return text_of(json.loads(value))
            except ValueError:
                pass
        return value
    return ""


def item_count(item):
    c = item.get("count", item.get("Count", 1))
    return c if isinstance(c, int) and c > 0 else 1


def item_name(item):
    comps = item.get("components") or {}
    if "minecraft:custom_name" in comps:
        return text_of(comps["minecraft:custom_name"])
    display = (item.get("tag") or {}).get("display") or {}  # worlds before 1.20.5
    if "Name" in display:
        return text_of(display["Name"])
    if "CustomName" in item:  # placed blocks
        return text_of(item["CustomName"])
    return ""


def item_children(item):
    """Items stored inside this item or block (shulker box or bundle contents)."""
    out = []
    comps = item.get("components") or {}
    for entry in comps.get("minecraft:container") or []:
        if isinstance(entry, dict) and isinstance(entry.get("item"), dict):
            out.append(entry["item"])
    out += comps.get("minecraft:bundle_contents") or []
    tag = item.get("tag") or {}
    out += (tag.get("BlockEntityTag") or {}).get("Items") or []  # before 1.20.5
    out += tag.get("Items") or []  # bundles before 1.20.5
    out += item.get("Items") or []  # placed shulker boxes
    return [i for i in out if isinstance(i, dict) and isinstance(i.get("id"), str)]


def all_descendants(items):
    for it in items:
        yield it
        for sub in all_descendants(item_children(it)):
            yield sub


def enchantments(item):
    levels = {}
    comps = item.get("components") or {}
    for key in ("minecraft:enchantments", "minecraft:stored_enchantments"):
        e = comps.get(key)
        if isinstance(e, dict):
            levels.update(e["levels"] if isinstance(e.get("levels"), dict) else e)
    tag = item.get("tag") or {}
    for key in ("Enchantments", "StoredEnchantments"):  # before 1.20.5
        for e in tag.get(key) or []:
            if isinstance(e, dict) and "id" in e:
                levels[str(e["id"])] = e.get("lvl", 1)
    return ", ".join("%s %s" % (nice(k), v) for k, v in levels.items())


def describe_item(item):
    n = item_count(item)
    s = ("%d x " % n if n > 1 else "") + nice(item.get("id"))
    name = item_name(item)
    if name:
        s += ' "%s"' % name
    ench = enchantments(item)
    if ench:
        s += " [%s]" % ench
    return s


def summarize(items, limit=6):
    if not items:
        return "(empty)"
    totals = Counter()
    for it in items:
        totals[nice(it.get("id"))] += item_count(it)
    common = totals.most_common()
    shown = common if limit is None else common[:limit]
    text = ", ".join("%d %s" % (n, name) for name, n in shown)
    if len(common) > len(shown):
        text += ", + %d more kinds" % (len(common) - len(shown))
    return text


# =========================================================== world layout
def resolve_world(path):
    """Find the world folder from whatever folder the user pointed at."""
    path = os.path.abspath(os.path.expanduser(path.strip().strip('"')))
    if not os.path.isdir(path):
        raise SystemExit("Folder not found: %s" % path)
    if os.path.isdir(os.path.join(path, "db")) and not os.path.isdir(os.path.join(path, "region")) \
            and not os.path.isdir(os.path.join(path, "dimensions")):
        raise SystemExit("This looks like a Bedrock Edition world. Shulker Finder only reads Java Edition worlds.")
    if os.path.isfile(os.path.join(path, "level.dat")):
        return path
    # A server folder: the world name is in server.properties (default "world").
    level_name = "world"
    try:
        with open(os.path.join(path, "server.properties"), encoding="utf-8", errors="replace") as f:
            for line in f:
                if line.startswith("level-name="):
                    level_name = line.split("=", 1)[1].strip() or "world"
    except OSError:
        pass
    if os.path.isfile(os.path.join(path, level_name, "level.dat")):
        return os.path.join(path, level_name)
    # A folder inside the world, like dimensions/minecraft or region.
    p = path
    for _ in range(5):
        p = os.path.dirname(p)
        if os.path.isfile(os.path.join(p, "level.dat")):
            return p
    raise SystemExit("No Minecraft world found in %s\n"
                     "Point this at the world folder: the one that contains level.dat." % path)


def world_seems_open(world):
    """On Windows, Minecraft locks session.lock while the world is open."""
    try:
        with open(os.path.join(world, "session.lock"), "rb") as f:
            f.read(1)
    except PermissionError:
        return True
    except OSError:
        return False
    return False


def chunk_folders(world):
    """Every region/ and entities/ folder that belongs to this world."""
    roots = [world]
    parent, base = os.path.split(world)
    for suffix in ("_nether", "_the_end"):  # Spigot/Paper keep these beside the world
        if os.path.isdir(os.path.join(parent, base + suffix)):
            roots.append(os.path.join(parent, base + suffix))
    found = set()
    for root in roots:
        for folder, _subdirs, files in os.walk(root):
            if os.path.basename(folder) in ("region", "entities") and any(f.endswith(".mca") for f in files):
                found.add(folder)
    return sorted(found)


def dimension_label(folder, world):
    rel = os.path.relpath(os.path.dirname(folder), world).replace("\\", "/")
    parts = [p for p in rel.split("/") if p not in ("", ".")]
    if "dimensions" in parts:
        rest = parts[parts.index("dimensions") + 1:]
        if len(rest) >= 2:
            ns, name = rest[0], "/".join(rest[1:])
            return DIM_LABELS.get(name, name) if ns == "minecraft" else ns + ":" + name
    if not parts:
        return "Overworld"
    return {"DIM-1": "Nether", "DIM1": "End"}.get(parts[-1], parts[-1])


# ===================================================== searching the world
_SKIP_KEYS = frozenset(("sections", "Sections", "Heightmaps", "structures", "Structures", "block_ticks",
                        "fluid_ticks", "TileTicks", "LiquidTicks", "PostProcessing", "Lights", "Biomes",
                        "CarvingMasks", "blending_data", "Brain"))


def _is_int(v):
    return isinstance(v, int) and not isinstance(v, bool)


def _anchor_of(node):
    """If this NBT compound is a block entity or an entity, return where it is."""
    nid = node.get("id")
    if not isinstance(nid, str):
        return None
    if _is_int(node.get("x")) and _is_int(node.get("y")) and _is_int(node.get("z")):
        return ("block", node["x"], node["y"], node["z"], nid)
    pos = node.get("Pos")
    if isinstance(pos, list) and len(pos) == 3 and all(isinstance(v, float) for v in pos):
        return ("entity", int(math.floor(pos[0])), int(math.floor(pos[1])), int(math.floor(pos[2])), nid)
    return None


def block_at(chunk, x, y, z):
    """Name of the block at x y z, read from the chunk's block data (1.16+ format)."""
    root = chunk.get("Level", chunk)
    for section in root.get("sections") or root.get("Sections") or []:
        if not isinstance(section, dict) or section.get("Y") != y >> 4:
            continue
        states = section.get("block_states")
        if isinstance(states, dict):
            palette, data = states.get("palette") or [], states.get("data")
        else:
            palette, data = section.get("Palette") or [], section.get("BlockStates")
        if len(palette) == 1:
            return palette[0].get("Name")
        if not palette or not isinstance(data, LongArray):
            return None
        bits = max(4, (len(palette) - 1).bit_length())
        per_long = 64 // bits
        index = ((y & 15) << 8) | ((z & 15) << 4) | (x & 15)
        word_index, slot = divmod(index, per_long)
        if (word_index + 1) * 8 > len(data):
            return None
        word = struct.unpack_from(">Q", data, word_index * 8)[0]
        p = (word >> (slot * bits)) & ((1 << bits) - 1)
        return palette[p].get("Name") if p < len(palette) else None
    return None


def _entity_where(entity_id):
    s = short_id(entity_id)
    if s == "item":
        return "dropped on the ground"
    if s in ("item_frame", "glow_item_frame"):
        return "in " + article(nice(s))
    if s.endswith("minecart") or "chest_boat" in s or "chest_raft" in s:
        return "in " + article(nice(s))
    if s in ("donkey", "mule", "llama", "trader_llama"):
        return "carried by " + article(nice(s))
    if s == "armor_stand":
        return "on an armor stand"
    return "held by " + article(nice(s))


def _world_hit(node, here, is_self, via, chunk, dim, source):
    hit = {"dimension": dim, "source": source, "name": item_name(node),
           "contents": item_children(node), "color": box_color(node["id"]), "pos": None}
    if here is None:
        hit.update(kind="other", where="location unknown")
        return hit
    kind, x, y, z, holder = here
    hit["pos"] = (x, y, z)
    if is_self:  # the shulker box is a placed block
        hit["kind"], hit["where"] = "placed", "placed"
        block = block_at(chunk, x, y, z)  # the color lives in the block, not the block entity
        hit["color"] = box_color(block) if block and "shulker_box" in block else "?"
    elif kind == "block":
        hit["kind"], hit["where"] = "container", "in " + article(nice(holder))
    else:
        hit["kind"], hit["where"] = "entity", _entity_where(holder)
    if via:
        hit["where"] += " (inside %s)" % article(nice(via[-1]))
    return hit


def _walk(node, anchor, via, ctx, hits):
    if isinstance(node, dict):
        found = _anchor_of(node)
        here = found or anchor
        nid = node.get("id")
        if isinstance(nid, str) and "shulker_box" in nid:
            hits.append(_world_hit(node, here, found is not None, via, ctx[0], ctx[1], ctx[2]))
        if found is not None:
            child_via = ()
        elif isinstance(nid, str):
            child_via = via + (nid,)  # this is an item; things below it are inside it
        else:
            child_via = via
        for k, v in node.items():
            if k not in _SKIP_KEYS and isinstance(v, (dict, list)):
                _walk(v, here, child_via, ctx, hits)
    elif isinstance(node, list):
        for v in node:
            if isinstance(v, (dict, list)):
                _walk(v, anchor, via, ctx, hits)


def scan_world(world, warnings, progress=None):
    """Find shulker boxes in every region and entities file. Returns (hits, files scanned)."""
    files = []
    for folder in chunk_folders(world):
        dim = dimension_label(folder, world)
        files += [(os.path.join(folder, f), dim) for f in sorted(os.listdir(folder)) if f.endswith(".mca")]
    hits = []
    for n, (path, dim) in enumerate(files, 1):
        if progress:
            progress(n, len(files))
        rel = os.path.relpath(path, world)
        try:
            for raw in region_chunks(path, warnings):
                if NEEDLE not in raw:
                    continue
                try:
                    chunk = read_nbt(raw)
                except Exception as e:
                    warnings.append("could not parse a chunk in %s: %s" % (rel, e))
                    continue
                _walk(chunk, None, (), (chunk, dim, rel), hits)
        except Exception as e:
            warnings.append("could not read %s: %s" % (rel, e))
    return hits, len(files)


# ================================================================= players
_UUID_FILE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\.dat$")
_ARMOR_SLOTS = {100: "feet", 101: "legs", 102: "chest", 103: "head", -106: "offhand"}


def _uuid_from_ints(v):
    if not (isinstance(v, tuple) and len(v) == 4):
        return None
    h = b"".join(struct.pack(">i", n) for n in v).hex()
    return "%s-%s-%s-%s-%s" % (h[:8], h[8:12], h[12:16], h[16:20], h[20:])


def load_names(world):
    """UUID -> player name, from usercache.json (servers keep one beside the world)."""
    names = {}
    for folder in (world, os.path.dirname(world)):
        try:
            with open(os.path.join(folder, "usercache.json"), encoding="utf-8") as f:
                for entry in json.load(f):
                    names.setdefault(entry["uuid"].lower(), entry["name"])
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            pass
    return names


def _dim_of_player(data):
    d = data.get("Dimension")
    if isinstance(d, str):
        return DIM_LABELS.get(short_id(d), d)
    if _is_int(d):
        return {0: "Overworld", -1: "Nether", 1: "End"}.get(d, str(d))
    return "?"


def _player_items(data):
    items = []
    for it in data.get("Inventory") or []:
        if isinstance(it, dict) and isinstance(it.get("id"), str):
            slot = it.get("Slot")
            if _is_int(slot) and 0 <= slot <= 8:
                label = "hotbar %d" % (slot + 1)
            elif _is_int(slot) and slot in _ARMOR_SLOTS:
                label = _ARMOR_SLOTS[slot]
            else:
                label = "slot %s" % slot
            items.append(("inventory", label, it))
    equipment = data.get("equipment")  # 1.21.5+ keeps armor and offhand here
    if isinstance(equipment, dict):
        for key in ("head", "chest", "legs", "feet", "offhand", "body", "saddle"):
            it = equipment.get(key)
            if isinstance(it, dict) and isinstance(it.get("id"), str):
                items.append(("inventory", key, it))
    for it in data.get("EnderItems") or []:
        if isinstance(it, dict) and isinstance(it.get("id"), str):
            slot = it.get("Slot")
            items.append(("ender chest", "slot %d" % (slot + 1) if _is_int(slot) else "", it))
    return items


def _make_player(data, uuid, names, source, host=False):
    bukkit = data.get("bukkit") or data.get("Paper") or {}
    name = names.get(uuid or "") or (bukkit.get("lastKnownName") if isinstance(bukkit, dict) else None)
    if not name:
        name = "single-player host" if host else (uuid or "unknown player")
    pos = data.get("Pos")
    if isinstance(pos, list) and len(pos) == 3 and all(isinstance(v, float) for v in pos):
        pos = tuple(int(math.floor(v)) for v in pos)
    else:
        pos = None
    return {"name": name, "uuid": uuid, "source": source, "host": host,
            "dimension": _dim_of_player(data), "pos": pos, "items": _player_items(data)}


def load_players(world, warnings):
    names = load_names(world)
    players, seen = [], set()
    try:  # in single-player, the host's data lives in level.dat
        host = (read_nbt(read_dat(os.path.join(world, "level.dat"))).get("Data") or {}).get("Player")
        if isinstance(host, dict):
            uuid = _uuid_from_ints(host.get("UUID"))
            players.append(_make_player(host, uuid, names, "level.dat", host=True))
            if uuid:
                seen.add(uuid)
    except Exception as e:
        warnings.append("could not read level.dat: %s" % e)
    for sub in ("playerdata", "players"):
        for folder, _subdirs, files in os.walk(os.path.join(world, sub)):
            for f in sorted(files):
                if not _UUID_FILE.match(f):
                    continue
                uuid = f[:-4].lower()
                if uuid in seen:
                    continue
                seen.add(uuid)
                path = os.path.join(folder, f)
                try:
                    data = read_nbt(read_dat(path))
                except Exception as e:
                    warnings.append("could not read %s: %s" % (os.path.relpath(path, world), e))
                    continue
                players.append(_make_player(data, uuid, names, os.path.relpath(path, world)))
    players.sort(key=lambda p: (not p["host"], p["name"].lower()))
    return players


def player_hits(players):
    hits = []

    def visit(item, via, player, place, slot):
        if "shulker_box" in item["id"]:
            where = place + (" " + slot if slot else "")
            if via:
                where += " (inside %s)" % article(nice(via[-1]))
            hits.append({"kind": "player", "player": player["name"], "where": where,
                         "dimension": player["dimension"], "pos": player["pos"],
                         "color": box_color(item["id"]), "name": item_name(item),
                         "contents": item_children(item), "source": player["source"]})
        for child in item_children(item):
            visit(child, via + (item["id"],), player, place, slot)

    for p in players:
        for place, slot, item in p["items"]:
            visit(item, (), p, place, slot)
    return hits


# =============================================================== filtering
def _norm(text):
    return text.lower().replace(" ", "_")


def filter_hits(hits, name=None, contains=None):
    out = []
    for h in hits:
        if name and _norm(name) not in _norm(h["name"]):
            continue
        if contains:
            want = _norm(contains)
            if not any(want in _norm(short_id(i["id"])) or want in _norm(item_name(i))
                       for i in all_descendants(h["contents"])):
                continue
        out.append(h)
    return out


# ================================================================ reports
_SECTIONS = (("placed", "PLACED SHULKER BOXES"),
             ("container", "INSIDE CHESTS, BARRELS AND OTHER CONTAINERS"),
             ("entity", "DROPPED, IN ITEM FRAMES, MINECARTS OR CARRIED BY MOBS"),
             ("player", "IN PLAYER INVENTORIES AND ENDER CHESTS"),
             ("other", "OTHER"))
_DIM_ORDER = {"Overworld": 0, "Nether": 1, "End": 2}


def _hit_lines(h):
    name = '  "%s"' % h["name"] if h["name"] else ""
    if h["kind"] == "player":
        seen = "   (last seen: %s %d %d %d)" % ((h["dimension"],) + h["pos"]) if h["pos"] else ""
        return ["  " + h["player"] + seen,
                "      %-11s %s%s" % (h["color"], h["where"], name),
                "      contains: " + summarize(h["contents"])]
    else:
        x, y, z = h["pos"] if h["pos"] else ("?", "?", "?")
        first = "  %-10s %7s %4s %7s   %-11s %s%s" % (h["dimension"], x, y, z, h["color"], h["where"], name)
    return [first, "      contains: " + summarize(h["contents"])]


def format_shulker_report(world, hits, files_scanned, players_scanned, filters):
    lines = ["Shulker Finder %s" % __version__, "World: %s" % world,
             "Scanned %d region/entity files and %d player files." % (files_scanned, players_scanned)]
    if filters:
        lines.append("Filter: " + filters)
    lines.append("")
    if not hits:
        lines.append("No shulker boxes found.")
        return "\n".join(lines) + "\n"
    counts = Counter(h["kind"] for h in hits)
    lines.append("Found %d shulker box%s." % (len(hits), "" if len(hits) == 1 else "es"))
    for kind, title in _SECTIONS:
        group = [h for h in hits if h["kind"] == kind]
        if not group:
            continue
        if kind == "player":
            group.sort(key=lambda h: (h["player"].lower(), h["where"]))
            header = "  PLAYER\n      %-11s %s" % ("COLOR", "WHERE / NAME")
        else:
            group.sort(key=lambda h: (_DIM_ORDER.get(h["dimension"], 9), h["dimension"],
                                      h["pos"] or (0, 0, 0)))
            header = "  %-10s %7s %4s %7s   %-11s %s" % ("DIMENSION", "X", "Y", "Z", "COLOR", "WHERE / NAME")
        lines += ["", "== %s (%d) ==" % (title, counts[kind]), header]
        for h in group:
            lines += _hit_lines(h)
    return "\n".join(lines) + "\n"


def format_inventories(world, players):
    lines = ["Shulker Finder %s - player inventories" % __version__, "World: %s" % world, ""]
    if not players:
        lines.append("No player data found.")
        return "\n".join(lines) + "\n"
    for p in players:
        title = p["name"] + ("" if p["name"] == p["uuid"] or not p["uuid"] else "  (%s)" % p["uuid"])
        lines.append("=" * 72)
        lines.append(title)
        if p["pos"]:
            lines.append("Last seen: %s at %d %d %d" % ((p["dimension"],) + p["pos"]))
        for place in ("inventory", "ender chest"):
            entries = [(slot, it) for pl, slot, it in p["items"] if pl == place]
            lines.append("")
            lines.append("%s:%s" % (place.capitalize(), " (empty)" if not entries else ""))
            for slot, it in entries:
                lines.append("  %-10s %s" % (slot, describe_item(it)))
                inside = item_children(it)
                if inside or "shulker_box" in it["id"]:
                    lines.append("  %-10s   -> %s" % ("", summarize(inside, limit=None)))
        lines.append("")
    return "\n".join(lines) + "\n"


# ==================================================================== main
def _save(report, path):
    if path == "-":
        return
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(report)
        print("Saved this report to %s" % os.path.abspath(path))
    except OSError as e:
        print("Could not save the report to %s: %s" % (path, e))


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")  # odd characters in names won't crash old consoles
        except (AttributeError, ValueError):
            pass

    ap = argparse.ArgumentParser(
        prog="shulker_finder.py",
        description="Find every shulker box in a Minecraft: Java Edition world, "
                    "or list what every player is carrying. Read-only.")
    ap.add_argument("world", help="the world folder (the one with level.dat), or a server folder")
    ap.add_argument("--inventories", action="store_true",
                    help="list every player's inventory and ender chest instead of searching for shulker boxes")
    ap.add_argument("--player", metavar="NAME",
                    help="only this player (name or UUID); works with both modes")
    ap.add_argument("--name", metavar="TEXT", help="only shulker boxes whose custom name contains TEXT")
    ap.add_argument("--contains", metavar="ITEM",
                    help="only shulker boxes holding an item whose name contains ITEM, e.g. elytra")
    ap.add_argument("--skip-players", action="store_true", help="don't search player inventories")
    ap.add_argument("-o", "--output", metavar="FILE",
                    help="where to save the report (default: shulker_report.txt or inventories_report.txt "
                         "in the current folder; '-' to not save)")
    ap.add_argument("--version", action="version", version="%(prog)s " + __version__)
    args = ap.parse_args(argv)

    world = resolve_world(args.world)
    if world_seems_open(world):
        print("Warning: the world looks open in Minecraft. Close it (or stop the server) "
              "for up-to-date results.\n", file=sys.stderr)
    warnings = []

    def only_player(players):
        if not args.player:
            return players
        want = args.player.lower()
        return [p for p in players if want in (p["name"].lower(), (p["uuid"] or "").lower())]

    if args.inventories:
        players = only_player(load_players(world, warnings))
        report = format_inventories(world, players)
        print(report)
        _save(report, args.output or "inventories_report.txt")
    else:
        interactive = sys.stderr.isatty()

        def progress(n, total):
            if interactive:
                sys.stderr.write("\rScanning %d/%d files..." % (n, total))
                sys.stderr.flush()

        hits, files_scanned = ([], 0)
        if not args.player:
            hits, files_scanned = scan_world(world, warnings, progress)
            if interactive:
                sys.stderr.write("\r" + " " * 40 + "\r")
        players = [] if args.skip_players else only_player(load_players(world, warnings))
        hits += player_hits(players)
        filters = ", ".join(s for s in (
            args.player and "player %s" % args.player,
            args.name and 'name contains "%s"' % args.name,
            args.contains and 'holding "%s"' % args.contains) if s)
        hits = filter_hits(hits, args.name, args.contains)
        report = format_shulker_report(world, hits, files_scanned, len(players), filters)
        print(report)
        _save(report, args.output or "shulker_report.txt")

    if warnings:
        print("\n%d warning(s):" % len(warnings), file=sys.stderr)
        for w in warnings[:20]:
            print("  " + w, file=sys.stderr)
        if len(warnings) > 20:
            print("  ... and %d more" % (len(warnings) - 20), file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
