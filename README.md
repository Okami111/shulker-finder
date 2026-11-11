# Shulker Finder

Lost a shulker box? Shulker Finder scans a **Minecraft: Java Edition** world and tells you where every shulker box is, what color and name it has, and what's inside it. It can also list everything each player is carrying.

It finds shulker boxes that are:

- **placed** in the world (with exact X Y Z coordinates)
- **inside chests, barrels, hoppers** and any other container (the container's coordinates)
- **dropped on the ground**, in **item frames**, in **chest minecarts or chest boats**, or carried by **donkeys, llamas or allays**
- in a **player's inventory or ender chest**, including boxes tucked inside a bundle

It covers the Overworld, the Nether, the End and modded dimensions. It never changes your world: it only reads the files.

## What you need

- **Python 3.8 or newer.** Get it from [python.org](https://www.python.org/downloads/). On Windows, tick **"Add Python to PATH"** during setup.
- Nothing else. The script uses only Python's standard library.

## Quick start

1. **Download** [`shulker_finder.py`](https://raw.githubusercontent.com/Okami111/shulker-finder/main/shulker_finder.py) (right-click → *Save link as…*), or click **Code → Download ZIP** at the top of this page.
2. **Close Minecraft**, or stop the server. Minecraft only writes everything to disk when the world closes. For extra safety, run it on a copy of the world.
3. **Open a terminal** in the folder where you saved the script, then run it with the path to your world folder:

```
python shulker_finder.py "PATH/TO/YOUR/WORLD"
```

On macOS and Linux, use `python3` instead of `python`.

The report prints in the terminal and is also saved as `shulker_report.txt` in the folder you ran it from.

### Where is my world folder?

Your world folder is the one that contains `level.dat`.

| Setup | Where the worlds are |
|---|---|
| Windows | `%APPDATA%\.minecraft\saves\<world name>` (paste `%APPDATA%\.minecraft\saves` into File Explorer's address bar) |
| macOS | `~/Library/Application Support/minecraft/saves/<world name>` |
| Linux | `~/.minecraft/saves/<world name>` |
| Prism / MultiMC / CurseForge | inside the instance folder: `.minecraft/saves/<world name>` or `minecraft/saves/<world name>` |
| Server | the server folder works as-is; the world name is read from `server.properties` |

It doesn't have to be exact: you can also point it at a server folder, or at a folder inside the world (such as `dimensions/minecraft` or `region`), and it will find the world.

Example on Windows:

```
python shulker_finder.py "%APPDATA%\.minecraft\saves\New World"
```

## Example output

```
Found 6 shulker boxes.

== PLACED SHULKER BOXES (1) ==
  DIMENSION        X    Y       Z   COLOR       WHERE / NAME
  Overworld      120   64    -300   blue        placed  "Kit"
      contains: 1 elytra

== INSIDE CHESTS, BARRELS AND OTHER CONTAINERS (1) ==
  DIMENSION        X    Y       Z   COLOR       WHERE / NAME
  Overworld        5   70       9   red         in a chest
      contains: 64 diamond

== DROPPED, IN ITEM FRAMES, MINECARTS OR CARRIED BY MOBS (2) ==
  DIMENSION        X    Y       Z   COLOR       WHERE / NAME
  Nether         -41   63     812   plain       dropped on the ground
      contains: (empty)
  Nether          10   12      10   lime        in a chest minecart
      contains: (empty)

== IN PLAYER INVENTORIES AND ENDER CHESTS (2) ==
  PLAYER
      COLOR       WHERE / NAME
  Alex   (last seen: Overworld 1 70 2)
      green       ender chest slot 4
      contains: (empty)
  Steve   (last seen: End -5 80 5)
      purple      inventory slot 10 (inside a bundle)  "Loot"
      contains: 64 diamond, 30 iron ingot
```

For a box inside a container, the coordinates are the container's, so that's the chest to open.

## Narrowing it down

Know what was in the box, or what it was called? Filter the results:

```
python shulker_finder.py "PATH/TO/WORLD" --contains elytra       # boxes holding an elytra
python shulker_finder.py "PATH/TO/WORLD" --name "kit"            # boxes whose name contains "kit"
python shulker_finder.py "PATH/TO/WORLD" --player Steve          # only Steve's inventory and ender chest
```

## See what every player is carrying

```
python shulker_finder.py "PATH/TO/WORLD" --inventories
python shulker_finder.py "PATH/TO/WORLD" --inventories --player Steve
```

This lists each player's inventory, armor, offhand and ender chest, with enchantments, custom names and the contents of any shulker box or bundle. It's saved as `inventories_report.txt`.

Player names come from `usercache.json`, which servers keep next to the world. Without it (in most single-player worlds), players show as their UUID. You can look up whose UUID it is on [namemc.com](https://namemc.com). In single-player, the world's owner shows as **single-player host**.

Only look through inventories on worlds and servers you own or run.

## All options

| Option | What it does |
|---|---|
| `--inventories` | List every player's items instead of searching for shulker boxes |
| `--player NAME` | Only this player (name or UUID) |
| `--name TEXT` | Only boxes whose custom name contains TEXT |
| `--contains ITEM` | Only boxes holding an item whose name contains ITEM |
| `--skip-players` | Don't look in player inventories |
| `-o FILE` | Save the report somewhere else (`-o -` to not save) |
| `--help` | Show all of this |

## Running it from PyCharm or VS Code

Open the script, then add the world folder as a **script parameter / argument** in the run configuration, in quotes. If you just press Run without it, the script prints its help text and stops.

If the terminal says `python` isn't found (Windows sends you to the Microsoft Store), Python isn't on your PATH. Reinstall it with "Add Python to PATH" ticked, or use the full path to your `python.exe`.

## Didn't find your box?

- **Dropped items disappear after 5 minutes** in loaded chunks. If you dropped it and walked away, it's most likely gone. It only survives if the chunk unloaded first.
- **Items that fall into lava or the void, or burn in fire, are destroyed.**
- **The game was still running.** Close the world (or run `/save-all` and stop the server), then scan again. Online players' inventories are saved only when they log out or the world saves.
- **A hopper moved it.** Check the containers section for a box in a hopper, chest or minecart near where you lost it.

## Supported worlds

- Java Edition from 1.13 to the latest release: both the newer `dimensions/` layout and the classic `region/`, `DIM-1/`, `DIM1/` layout, and both the new (1.20.5+) and old item formats.
- Spigot and Paper servers, which keep the Nether and End in separate `world_nether` and `world_the_end` folders.
- Modded dimensions and containers, as long as they store items the vanilla way.
- **Not supported:** Bedrock Edition (Windows 10/11 Edition, consoles, phones). Those worlds use a different file format.

The color of a placed box comes from the block data. In very old worlds (before 1.16) it may show as `?`, but the coordinates are still right.

## How it works

Java worlds are stored as region files (`.mca`), each holding 1,024 chunks of compressed NBT data. Shulker Finder unpacks each chunk, skips any that don't mention a shulker box, and fully reads the rest, following items into containers, entities and other items. Player inventories come from `level.dat` and the `playerdata` or `players` folder.

## Running the tests

```
python -m unittest discover tests
```

The tests build small fake worlds in both the new and old formats and check every kind of hiding place.

## License

[MIT](LICENSE). Not affiliated with Mojang or Microsoft.
