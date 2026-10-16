"""Client models placed to ship, and the server rows of the interactive ones. A placed door or object carries the client definition
export copies into the zone's archive byte for byte (clientRecord: archive, entry, its SHA-256), and, when interactive, what EQ's own
doors or object row holds for it besides its place, which export writes into the zone's rows naming the copy in the archive. Interactive
means exactly what those tables hold: a door's open type, destination, key, lock, and trigger; an object's type (a tradeskill
container's kind, or a plain clickable object). Columns are EQEmu's (base_doors_repository.h, base_object_repository.h at ae4bd2833)."""
import hashlib
import re

import eqArchive
import eqgFiles
import serverRoleExtract

# Door_Struct.name and Object_Struct.object_name are char[32] in rof2_structs.h; the doors and object columns are varchar(32).
nameLimit = 31
# doors.size is a smallint unsigned percent; RoF2 sends an object's size / 100 only for 0 < size < 5000, else 1 (rof2.cpp:1530).
doorSizeLimit = 65535
objectScaleRange = (0.01, 49.99)
doorColumnLimit = 32767
shortNamePattern = re.compile(r"^[a-z0-9]{1,31}$")
objectTypeNumbers = {name: number for number, name in serverRoleExtract.objectTypes.items()}
doorParameters = ("openType", "destination", "keyItem", "lockpick", "triggerDoor", "triggerType", "noKeyring", "closeTimerMilliseconds")
objectParameters = ("objectType", "displayName")


def clientRecord(clientRoot, details):
  """What export copies of a placed model (eqModels.buildModel's details): an EQG model's archive, entry, actor, the entry's SHA-256,
  and the archives its textures are found in as the client loads them with it (its own first); a classic actor's archive and actor,
  which no EQG archive can hold, so export refuses it."""
  definition = details["definition"]
  if definition["kind"].startswith("eqg"):
    data = eqArchive.EQArchive(clientRoot / definition["archive"]).read(definition["entry"])
    return {
      "kind": definition["kind"], "archive": definition["archive"], "entry": definition["entry"], "actor": actorName(definition["entry"]),
      "sha256": hashlib.sha256(data).hexdigest(), "textureArchives": list(details["searchedArchives"]),
    }
  return {"kind": definition["kind"], "archive": definition["archive"], "wld": definition["wld"], "actor": definition["actor"], "sha256": None}


def actorName(entry):
  """The name the client registers an EQG model under, without _ACTORDEF: its entry without the extension, uppercased (EQGraphicsDX9.dll
  0x10064e8d)."""
  return entry.rsplit(".", 1)[0].upper()


def requireObjectScale(scale):
  if not objectScaleRange[0] <= scale <= objectScaleRange[1]:
    raise ValueError(f"An object row's scale is {objectScaleRange[0]} to {objectScaleRange[1]} (its size is the scale times 100, which RoF2 is sent only under 5000), got {scale}")


def requireDoorSize(scaleFactor):
  if abs(scaleFactor - round(scaleFactor)) > 1e-3 or not 1 <= round(scaleFactor) <= doorSizeLimit:
    raise ValueError(f"A door row's size is a whole percent from 1 to {doorSizeLimit}, got scaleFactor {scaleFactor}")
  return int(round(scaleFactor))


def wholeNumber(value, name, low, high):
  if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
    raise ValueError(f"{name} is a whole number from {low} to {high}, got {value!r}")
  return value


def objectRow(record, scale, objectType, displayName):
  """An interactive object's row values besides its place: its type by number or name (serverRoleExtract.objectTypes: potteryKiln,
  forge, staticUnlocked, ...), the name the client looks its model up by (<ACTOR>_ACTORDEF, eqgame.exe 0x48b710), and a display name."""
  if objectType is None:
    raise ValueError(f"An interactive object's row needs its objectType, one of {sorted(objectTypeNumbers)} (or its number)")
  number = objectTypeNumbers.get(objectType) if isinstance(objectType, str) else objectType
  if number not in serverRoleExtract.objectTypes:
    raise ValueError(f"objectType is one of {sorted(objectTypeNumbers)} or their numbers {sorted(serverRoleExtract.objectTypes)}, got {objectType!r}")
  requireObjectScale(scale)
  if displayName is not None and (not isinstance(displayName, str) or not 0 < len(displayName) <= 64):
    raise ValueError(f"displayName is 1 to 64 characters, got {displayName!r}")
  objectName = f"{record['actor']}_ACTORDEF"
  if len(objectName) > nameLimit:
    raise ValueError(f"An object row names its model {objectName}, longer than the {nameLimit} characters the row holds")
  return {"table": "object", "objectName": objectName, "type": number, "displayName": displayName}


def doorRow(record, scaleFactor, parameters):
  """An interactive door's row values besides its place: openType (required; the client's door behaviour, as Peridot's own doors use:
  doors swing, teleports open on a click), destination ({zone, x, y, z, headingDegrees}, Blender axes: the door teleports there), keyItem,
  lockpick, triggerDoor (another interactive door's object name, opened with it), triggerType, noKeyring, closeTimerMilliseconds; its
  size, a whole percent, is the placement's scale at export. The client looks a door's model up by its bare name."""
  unknown = sorted(set(parameters) - set(doorParameters))
  if unknown:
    raise ValueError(f"A door row takes {list(doorParameters)}; not {unknown}")
  if parameters.get("openType") is None:
    raise ValueError("An interactive door's row needs its openType (EQ's doors.opentype, as the client's own doors of this model use it: getAsset's role)")
  requireDoorSize(scaleFactor)
  if len(record["actor"]) > nameLimit:
    raise ValueError(f"A door row names its model {record['actor']}, longer than the {nameLimit} characters the row holds")
  destination = parameters.get("destination")
  if destination is not None:
    if not isinstance(destination, dict) or set(destination) != {"zone", "x", "y", "z", "headingDegrees"}:
      raise ValueError(f"destination is {{zone, x, y, z, headingDegrees}} (Blender axes; zone a short name), got {destination!r}")
    if not isinstance(destination["zone"], str) or not shortNamePattern.match(destination["zone"]):
      raise ValueError(f"destination's zone is a short name (lowercase letters and digits, at most 31), got {destination['zone']!r}")
    if not all(isinstance(destination[axis], (int, float)) and not isinstance(destination[axis], bool) for axis in ("x", "y", "z", "headingDegrees")):
      raise ValueError(f"destination's x, y, z, and headingDegrees are numbers, got {destination!r}")
  triggerDoor = parameters.get("triggerDoor")
  if triggerDoor is not None and (not isinstance(triggerDoor, str) or not triggerDoor):
    raise ValueError(f"triggerDoor names another interactive door's object, got {triggerDoor!r}")
  return {
    "table": "doors", "name": record["actor"], "openType": wholeNumber(parameters["openType"], "openType", 0, doorColumnLimit), "destination": destination,
    "keyItem": wholeNumber(parameters.get("keyItem", 0), "keyItem", 0, 2147483647), "lockpick": wholeNumber(parameters.get("lockpick", 0), "lockpick", 0, doorColumnLimit),
    "triggerDoor": triggerDoor, "triggerType": wholeNumber(parameters.get("triggerType", 0), "triggerType", 0, doorColumnLimit),
    "noKeyring": wholeNumber(parameters.get("noKeyring", 0), "noKeyring", 0, 1),
    "closeTimerMilliseconds": wholeNumber(parameters.get("closeTimerMilliseconds", 5000), "closeTimerMilliseconds", 0, 65535),
  }


def requireBundleable(record):
  """A record export can copy: an EQG model (.mod). A classic actor lives in a WLD no EQG archive holds."""
  if record["kind"] != "eqgModel":
    raise ValueError(f"{record['archive']}:{record.get('entry') or record['actor']} is a {record['kind']} model; only EQG models (.mod) are copied into an EQG archive")


def modelBytes(clientRoot, record):
  """The client entry's bytes, as recorded: refused when the client file changed since the model was placed (a patch)."""
  data = eqArchive.EQArchive(clientRoot / record["archive"]).read(record["entry"])
  digest = hashlib.sha256(data).hexdigest()
  if digest != record["sha256"]:
    raise ValueError(f"{record['archive']}:{record['entry']} now has SHA-256 {digest}, not the {record['sha256']} it had when placed: the client was patched; place it again")
  return data


def parsedModel(record, data):
  return eqgFiles.parseModel(data, f"{record['archive']}:{record['entry']}")
