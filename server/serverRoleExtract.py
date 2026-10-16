"""The in-game role of each client model as the server's own tables hold it: the doors rows naming it (each door's open type, whether it
teleports, needs a key, or triggers another door) and the object rows naming it (each object's type: a tradeskill container's kind, or a
plain clickable object). zonewright never connects to a database: extractQuery is run read-only on claudeperidot's copy of Peridot's
database (README, Asset catalog), and rolesFromRows turns its tab-separated output into catalog/serverRoles.json, which the catalog
merges into each model's measured facts by name."""
import collections
import json

# The object table's types (EQEmu ae4bd2833 zone/object.h:90-132, enum ObjectTypes), which LoadZoneObjects sends the client as the
# object's type: 0 and 255 are plain objects, 1 a dropped item, and the rest tradeskill containers of the kind named.
objectTypes = {
  0: "staticLocked", 1: "temporary", 10: "toolBox", 11: "research", 12: "mortar", 13: "selfDusting", 14: "baking1", 15: "baking2", 16: "tailoring",
  17: "forge", 18: "fletching", 19: "brewBarrel", 20: "jewelcrafting", 21: "potteryWheel", 22: "potteryKiln", 24: "wizardResearch",
  25: "magicianResearch", 26: "necromancerResearch", 27: "enchanterResearch", 28: "invalid1", 29: "invalid2", 30: "experimental",
  31: "highElfForge", 32: "darkElfForge", 33: "ogreForge", 34: "dwarfForge", 35: "gnomeForge", 36: "barbarianForge", 38: "iksarForge",
  39: "humanForge", 40: "humanForge2", 41: "halflingTailoring", 42: "eruditeTailoring", 43: "woodElfTailoring", 44: "woodElfFletching",
  45: "iksarPotteryWheel", 47: "trollForge", 48: "woodElfForge", 49: "halflingForge", 50: "eruditeForge", 53: "augmentationPool", 255: "staticUnlocked",
}
plainObjectTypes = (0, 1, 255)
sceneryRole = {"kinds": ["scenery"]}
extractQuery = (
  "SELECT 'door', name, zone, opentype, dest_zone <> 'NONE' AND dest_zone IS NOT NULL, keyitem <> 0, triggerdoor <> 0 FROM doors"
  " UNION ALL SELECT 'object', objectname, zoneid, type, 0, 0, 0 FROM object WHERE objectname IS NOT NULL;"
)


def modelKey(name):
  return name.lower().removesuffix("_actordef")


def rolesFromRows(text):
  """Per model name: its doors rows (count, open types, how many teleport, need a key, or trigger another door, the zones holding
  them) and object rows (count, types by name, the zone ids holding them), and its kinds: door, teleportDoor, tradeskillContainer,
  clickableObject. text is extractQuery's output, one tab-separated row per line."""
  doors = collections.defaultdict(lambda: {"rows": 0, "openTypes": collections.Counter(), "teleports": 0, "keyed": 0, "triggering": 0, "zones": set()})
  objects = collections.defaultdict(lambda: {"rows": 0, "types": collections.Counter(), "zones": set()})
  for line in text.splitlines():
    fields = line.split("\t")
    if len(fields) != 7 or fields[0] not in ("door", "object"):
      raise ValueError(f"An extract row is kind, name, zone, type, teleports, keyed, triggering; got {line!r}")
    kind, name, zone, typeNumber, teleports, keyed, triggering = fields
    if not name.strip():
      continue
    key = modelKey(name.strip())
    if kind == "door":
      entry = doors[key]
      entry["openTypes"][int(typeNumber)] += 1
      entry["teleports"] += int(teleports)
      entry["keyed"] += int(keyed)
      entry["triggering"] += int(triggering)
    else:
      entry = objects[key]
      number = int(typeNumber)
      entry["types"][objectTypes.get(number, f"type{number}")] += 1
    entry["rows"] += 1
    entry["zones"].add(zone)
  roles = {}
  for key in sorted(set(doors) | set(objects)):
    role, kinds = {}, []
    if key in doors:
      entry = doors[key]
      role["doors"] = {"rows": entry["rows"], "openTypes": {str(number): count for number, count in sorted(entry["openTypes"].items())}, "teleports": entry["teleports"],
        "keyed": entry["keyed"], "triggering": entry["triggering"], "zones": sorted(entry["zones"])}
      kinds += ["door"] + (["teleportDoor"] if entry["teleports"] else [])
    if key in objects:
      entry = objects[key]
      role["objects"] = {"rows": entry["rows"], "types": dict(entry["types"].most_common()), "zones": sorted(entry["zones"], key=int)}
      plainNames = {objectTypes[number] for number in plainObjectTypes}
      kinds += (["tradeskillContainer"] if set(entry["types"]) - plainNames else []) + (["clickableObject"] if set(entry["types"]) & plainNames else [])
    roles[key] = role | {"kinds": kinds}
  return roles


def extractFile(text, server, database, eqemuCommit):
  """catalog/serverRoles.json's text: where the rows were read and the roles."""
  return json.dumps({"server": server, "database": database, "eqemu": eqemuCommit, "query": extractQuery, "roles": rolesFromRows(text)}, indent=1, sort_keys=True) + "\n"
