"""Build 00_AuroraAlphaTuning.big from a local Zero Hour INIZH.big.

Use the game installation as the output directory to enable the tuning on startup.
"""

import argparse
from decimal import Decimal
from pathlib import Path
import re
import struct
from tempfile import TemporaryDirectory


AIRCRAFT = r"Data\INI\Object\SuperWeaponGeneral.ini"
WEAPONS = r"Data\INI\Weapon.ini"
MOVEMENT = r"Data\INI\Locomotor.ini"
ALPHA_MOVEMENT = r"Data\INI\Locomotor\AuroraAlpha.ini"
MOD_NAME = "00_AuroraAlphaTuning.big"


def read_ini_files(archive):
    data = archive.read_bytes()
    if data[:4] != b"BIGF":
        raise ValueError(f"{archive} is not a BIGF archive")
    count, index_end = struct.unpack_from(">II", data, 8)
    wanted = {name.lower(): name for name in (AIRCRAFT, WEAPONS, MOVEMENT)}
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


def tune(files):
    aircraft = files[AIRCRAFT]
    weapons = files[WEAPONS]
    plane = block(aircraft, "Object", "SupW_AmericaJetAurora")
    start, end = numeric_field(plane[0], "BuildTime").span(1)
    tuned_plane = plane[0][:start] + "5.0" + plane[0][end:]

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
        template = scale_field(template, "Speed", 2)
        locomotors.append(scale_field(template, "SpeedDamaged", 2))
        tuned_plane = tuned_plane[:binding.start(1)] + private + tuned_plane[binding.end(1):]

    blast = block(weapons, "Weapon", "SupW_FuelBombDetonationWeapon")
    tuned_blast = blast[0]
    for field in ("PrimaryDamage", "PrimaryDamageRadius", "ShockWaveRadius"):
        tuned_blast = scale_field(tuned_blast, field, 2)

    return {
        AIRCRAFT: aircraft[:plane.start()] + tuned_plane + aircraft[plane.end():],
        WEAPONS: weapons[:blast.start()] + tuned_blast + weapons[blast.end():],
        ALPHA_MOVEMENT: "\r\n\r\n".join(locomotors) + "\r\n",
    }


def build_archive(files):
    entries = [(path.encode("ascii") + b"\0", data.encode("latin-1"))
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
