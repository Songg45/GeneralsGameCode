"""Build 00_AuroraAlphaTuning.big from a local Zero Hour INIZH.big.

Use the game installation as the output directory to enable the tuning on startup.
"""

import argparse
from decimal import Decimal
import math
from pathlib import Path, PureWindowsPath
import random
import re
import struct
from tempfile import TemporaryDirectory


AIRCRAFT = r"Data\INI\Object\SuperWeaponGeneral.ini"
WEAPONS = r"Data\INI\Weapon.ini"
WEAPON_OBJECTS = r"Data\INI\Object\WeaponObjects.ini"
MOVEMENT = r"Data\INI\Locomotor.ini"
ALPHA_MOVEMENT = r"Data\INI\Locomotor\AuroraAlpha.ini"
MOD_NAME = "00_AuroraAlphaTuning.big"
ALPHA_STAT_MULTIPLIER = 8
MOD_DATA = Path(__file__).resolve().parents[1] / "mods" / "aurora_alpha"
BLAST_TEXTURE_SIZE = 256
BLAST_TEXTURE_RADIUS = 112


def fire_ring_texture():
    """Make an additive TGA with the bright flame front at 112 of 256 texels."""
    side = BLAST_TEXTURE_SIZE
    pixels = bytearray(side * side * 3)
    for y in range(side):
        dy = y + 0.5 - side / 2
        for x in range(side):
            dx = x + 0.5 - side / 2
            depth = BLAST_TEXTURE_RADIUS - math.hypot(dx, dy)
            if depth <= -0.5 or depth >= 32:
                continue

            # Vary the flame tips on the inside while the leading edge stays circular.
            angle = math.atan2(dy, dx)
            thickness = 18 + 6 * math.sin(11 * angle) + 4 * math.sin(23 * angle + 0.6)
            heat = max(0.0, 1 - max(0.0, depth - 1) / thickness)
            edge = min(1.0, depth + 0.5)
            red = round(255 * edge * heat)
            green = round(235 * edge * heat ** 2)
            blue = round(160 * edge * heat ** 4)
            offset = (y * side + x) * 3
            pixels[offset:offset + 3] = bytes((blue, green, red))

    # Uncompressed, top-origin, 24-bit BGR. Black is transparent with the additive shader.
    header = struct.pack("<BBB5xHHHHBB", 0, 0, 2, 0, 0, side, side, 24, 0x20)
    return header + pixels


def fuel_cloud_texture():
    """A filled, billowing sprite. Fire, vapor and smoke tint the same density field."""
    side = BLAST_TEXTURE_SIZE
    rng = random.Random(794)
    grid = [[rng.random() for _ in range(64)] for _ in range(64)]

    def noise(x, y):
        ix, iy = math.floor(x), math.floor(y)
        u, v = x - ix, y - iy
        u, v = u * u * (3 - 2 * u), v * v * (3 - 2 * v)
        lo, hi = grid[iy % 64], grid[(iy + 1) % 64]
        a = lo[ix % 64] * (1 - u) + lo[(ix + 1) % 64] * u
        b = hi[ix % 64] * (1 - u) + hi[(ix + 1) % 64] * u
        return a * (1 - v) + b * v

    pixels = bytearray(side * side * 4)
    for y in range(side):
        for x in range(side):
            u = (x + 0.5 - side / 2) / BLAST_TEXTURE_RADIUS
            v = (y + 0.5 - side / 2) / BLAST_TEXTURE_RADIUS
            radius = math.hypot(u, v)
            if radius >= 1:
                continue
            sx = 4.5 * u + 1.8 * noise(3 * u, 3 * v)
            sy = 4.5 * v + 1.8 * noise(3 * u + 17, 3 * v)
            billow = 1 - abs(2 * noise(sx, sy) - 1)
            detail = noise(3 * sx + 8, 3 * sy) + 0.4 * noise(7 * sx, 7 * sy)
            edge = min(1.0, (1 - radius) / (0.018 + 0.032 * noise(2 * sx, 2 * sy)))
            light = min(1.0, 0.20 + 0.46 * billow ** 2 + 0.23 * detail + 0.12 * (1 - radius))
            value = round(255 * edge * light)
            offset = (y * side + x) * 4
            opacity = round(255 * edge * (0.54 + 0.46 * billow))
            pixels[offset:offset + 4] = bytes((value, value, value, opacity))
    header = struct.pack("<BBB5xHHHHBB", 0, 0, 2, 0, 0, side, side, 32, 0x28)
    return header + pixels


def read_ini_files(archive):
    data = archive.read_bytes()
    if data[:4] != b"BIGF":
        raise ValueError(f"{archive} is not a BIGF archive")
    count, index_end = struct.unpack_from(">II", data, 8)
    wanted = {name.lower(): name for name in (AIRCRAFT, WEAPONS, WEAPON_OBJECTS, MOVEMENT)}
    files = {}
    pos = 16
    for _ in range(count):
        offset, size = struct.unpack_from(">II", data, pos)
        end = data.index(b"\0", pos + 8, index_end)
        path = data[pos + 8:end].decode("ascii").replace("/", "\\").lower()
        pos = end + 1
        if path in wanted:
            if offset < index_end or offset + size > len(data) or wanted[path] in files:
                raise ValueError(f"Invalid archive entry for {path}")
            files[wanted[path]] = data[offset:offset + size].decode("latin-1")
    for name in wanted.values():
        if name not in files:
            raise ValueError(f"Missing {name} in {archive}")
    return files


def one_match(pattern, source, label):
    matches = list(re.finditer(pattern, source, re.MULTILINE | re.IGNORECASE))
    if len(matches) != 1:
        raise ValueError(f"Expected one {label}, found {len(matches)}")
    return matches[0]


def block(source, kind, name):
    header = rf"^{kind}[ \t]+{re.escape(name)}(?=[ \t;\r\n])"
    return one_match(header + r"[\s\S]*?^End\b", source, f"{kind} {name}")


def numeric_field(source, field):
    pattern = rf"^[ \t]*{field}[ \t]*=[ \t]*(\d+(?:\.\d+)?)(?=[ \t;\r\n]|$)"
    return one_match(pattern, source, field)


def scale_field(source, field, factor):
    match = numeric_field(source, field)
    value = Decimal(match[1]) * factor
    return source[:match.start(1)] + format(value, "f") + source[match.end(1):]


def tune_weapon_objects(objects):
    # Impact can occur before a new projectile or gas object registers with the shroud.
    # Position-based FX still respect fog of war and work on the first visible frame.
    bomb = block(objects, "Object", "SupW_AuroraFuelAirBomb")
    fx = one_match(r"^([ \t]*)DeathFX[ \t]*=[ \t]*AirF_FX_AuroraBombExplode\b", bomb[0], "Alpha release")
    replacement = f"{fx[1]}DeathFX = SupW_FX_AlphaFuelRelease\r\n{fx[1]}OrientToObject = No"
    objects = objects[:bomb.start() + fx.start()] + replacement + objects[bomb.start() + fx.end():]

    gas = block(objects, "Object", "SupW_AuroraFuelAirGas")
    fx = one_match(r"^[ \t]*FX[ \t]*=[ \t]*INITIAL[ \t]+AirF_FX_AuroraBombIgnite[^\n]*\n",
                   gas[0], "Alpha ignition")
    tuned_gas = (gas[0][:fx.start()] + gas[0][fx.end():]).removesuffix("End") + (
        "  Behavior = FXListDie ModuleTag_AlphaIgnition\r\n"
        "    DeathFX = SupW_FX_AlphaFuelIgnition\r\n"
        "    OrientToObject = No\r\n"
        "  End\r\nEnd"
    )
    return objects[:gas.start()] + tuned_gas + objects[gas.end():]


def tune(files):
    aircraft = files[AIRCRAFT]
    weapons = files[WEAPONS]
    plane = block(aircraft, "Object", "SupW_AmericaJetAurora")
    start, end = numeric_field(plane[0], "BuildTime").span(1)
    tuned_plane = plane[0][:start] + "1.0" + plane[0][end:]

    # Other factions use these locomotors too. Give the Alpha private copies.
    locomotors = []
    for mode, name in (("NORMAL", "Cruise"), ("SUPERSONIC", "Attack"),
                       ("SLUGGISH", "Return"), ("TAXIING", "Taxi")):
        binding = one_match(rf"^[ \t]*Locomotor[ \t]*=[ \t]*SET_{mode}[ \t]+(\w+)",
                            tuned_plane, f"Alpha {mode} locomotor")
        original = binding[1]
        private = f"SupW_Alpha{name}Locomotor"
        template = block(files[MOVEMENT], "Locomotor", original)[0]
        template = re.sub(r"^Locomotor[ \t]+\w+", f"Locomotor {private}", template, count=1)
        template = scale_field(template, "Speed", ALPHA_STAT_MULTIPLIER)
        locomotors.append(scale_field(template, "SpeedDamaged", ALPHA_STAT_MULTIPLIER))
        tuned_plane = tuned_plane[:binding.start(1)] + private + tuned_plane[binding.end(1):]

    blast = block(weapons, "Weapon", "SupW_FuelBombDetonationWeapon")
    tuned_blast = blast[0]
    for field in ("PrimaryDamage", "PrimaryDamageRadius", "ShockWaveRadius"):
        tuned_blast = scale_field(tuned_blast, field, ALPHA_STAT_MULTIPLIER)

    # The private detonation weapon is shared by the dropped bomb and the death blast.
    tuned_blast = tuned_blast.removesuffix("End") + "  FireFX = SupW_FX_AlphaFuelAirBlast\r\nEnd"
    tuned_plane = tuned_plane.removesuffix("End") + (
        "  Behavior = CreateObjectDie ModuleTag_AlphaDeathBlast\r\n"
        "    CreationList = SupW_OCL_AlphaDeathBlast\r\n"
        "  End\r\nEnd"
    )
    result = {
        AIRCRAFT: aircraft[:plane.start()] + tuned_plane + aircraft[plane.end():],
        WEAPONS: weapons[:blast.start()] + tuned_blast + weapons[blast.end():],
        WEAPON_OBJECTS: tune_weapon_objects(files[WEAPON_OBJECTS]),
        ALPHA_MOVEMENT: "\r\n\r\n".join(locomotors) + "\r\n",
    }
    radius = Decimal(numeric_field(tuned_blast, "PrimaryDamageRadius")[1])
    for category in ("FXList", "Object", "ObjectCreationList", "ParticleSystem"):
        path = Path("Data", "INI", category, "AlphaFuelAirBlast.ini")
        text = (MOD_DATA / path).read_text(encoding="ascii")
        # Distances marked in blast radii follow the tuned weapon on every installation.
        text = re.sub(r"@(\d+(?:\.\d+)?)R@", lambda m: format(radius * Decimal(m[1]), "f"), text)
        if category == "ParticleSystem":
            # W3D XY-planar quads extend one Size in each direction from their center.
            final_size = radius * BLAST_TEXTURE_SIZE / (2 * BLAST_TEXTURE_RADIUS)
            start_size = final_size / 20
            # With 0.75 damping, the total growth is four times the initial growth rate.
            growth = (final_size - start_size) / 4
            text = text.replace("@START_SIZE@", format(start_size, "f"))
            text = text.replace("@GROWTH@", format(growth, "f"))
        result[str(PureWindowsPath(*path.parts))] = text
    result[r"Art\Textures\SupWAlphaFireRing.tga"] = fire_ring_texture()
    result[r"Art\Textures\SupWAlphaFuelCloud.tga"] = fuel_cloud_texture()
    return result


def build_archive(files):
    entries = [(path.encode("ascii") + b"\0", data.encode("latin-1") if isinstance(data, str) else data)
               for path, data in files.items()]
    index_end = 16 + sum(8 + len(path) for path, _ in entries)
    offset = index_end
    index = bytearray()
    for path, data in entries:
        index.extend(struct.pack(">II", offset, len(data)))
        index.extend(path)
        offset += len(data)
    header = b"BIGF" + struct.pack("<I", offset) + struct.pack(">II", len(entries), index_end)
    return header + index + b"".join(data for _, data in entries)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("inizh", type=Path, help="The installed INIZH.big")
    parser.add_argument("output_directory", type=Path)
    args = parser.parse_args()
    contents = build_archive(tune(read_ini_files(args.inizh)))
    output = args.output_directory / MOD_NAME
    with TemporaryDirectory(dir=output.parent) as staging:
        temp_path = Path(staging) / "archive.tmp"
        temp_path.write_bytes(contents)
        temp_path.replace(output)
    print(output)


if __name__ == "__main__":
    main()
