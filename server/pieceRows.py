"""A zone's interactive pieces as server rows: <short>_pieces.sql, one compound statement in serverRows' form (an exit handler that
rolls back and raises again, the transaction, guards that SIGNAL, the writes, the commit), which the owner applies with the mariadb
client. Each interactive door is a doors row and each interactive object an object row, naming the model copied into the zone's own
archive (clientModelBundle), never also a .zon placement. A door's id is its zone-local number (1-255, the client's Door_Struct doorId
is a uint8), given in object-name order; a door id it writes where another row stands refuses, unless an export of this short name wrote
that row (the record, as for zone_points); object rows, whose id is one across every zone, are replaced by value: the rows an export of
this short name wrote are deleted where the table still holds them as written, and the new ones inserted without an id. Columns are
EQEmu's (base_doors_repository.h, base_object_repository.h at ae4bd2833). The server stands every object row on its .map at boot
(Zone::LoadZoneObjects, FindBestZ), so objectHeights gives where it will stand each."""
import math
import re

import numpy

import eqAxes
import playerReach
import serverNav
import serverRows

doorColumns = (
  "doorid", "zone", "version", "name", "pos_y", "pos_x", "pos_z", "heading", "opentype", "guild", "lockpick", "keyitem", "nokeyring", "triggerdoor",
  "triggertype", "disable_timer", "doorisopen", "door_param", "dest_zone", "dest_instance", "dest_x", "dest_y", "dest_z", "dest_heading",
  "invert_state", "incline", "size", "buffer", "client_version_mask", "is_ldon_door", "close_timer_ms", "dz_switch_id", "min_expansion",
  "max_expansion", "content_flags", "content_flags_disabled",
)
objectColumns = (
  "zoneid", "version", "xpos", "ypos", "zpos", "heading", "itemid", "charges", "objectname", "type", "icon", "size_percentage", "unknown24",
  "unknown60", "unknown64", "unknown68", "unknown72", "unknown76", "unknown84", "size", "solid_type", "incline", "tilt_x", "tilt_y",
  "display_name", "min_expansion", "max_expansion", "content_flags", "content_flags_disabled",
)
doorIDLimit = 255
doorMatchColumns = ("pos_x", "pos_y", "pos_z", "heading")
objectMatchFloats = ("xpos", "ypos", "zpos", "heading", "size")
# How far an object row may stand from where the server's FindBestZ puts it before the server would visibly move it.
standTolerance = 0.05
noDestination = "NONE"


def serverPlace(piece):
  """A piece's position in the server's axes and its heading in 512ths of a turn."""
  x, y, z = (round(float(value), 4) for value in eqAxes.serverFromZone(piece["position"]))
  heading = round(eqAxes.eqHeadingFromTurn(math.degrees(piece["rotation"][0])), 4) % eqAxes.eqHeadingUnits
  return x, y, z, heading


def doorRows(short, pieces):
  """The doors rows of the interactive doors (bridgeExport clientModels whose row's table is doors), numbered 1 up in object-name order,
  each trigger as the triggered door's number."""
  doors = sorted((piece for piece in pieces if piece["row"]["table"] == "doors"), key=lambda piece: piece["object"])
  if len(doors) > doorIDLimit:
    raise ValueError(f"{len(doors)} interactive doors; a zone holds at most {doorIDLimit} (the client's door id is one byte)")
  numbers = {piece["object"]: number for number, piece in enumerate(doors, start=1)}
  rows = []
  for piece in doors:
    row = piece["row"]
    x, y, z, heading = serverPlace(piece)
    destination = row["destination"]
    if destination is None:
      target = {"dest_zone": noDestination, "dest_x": 0, "dest_y": 0, "dest_z": 0, "dest_heading": 0}
    else:
      destinationX, destinationY, destinationZ = (round(float(value), 4) for value in eqAxes.serverFromZone([destination["x"], destination["y"], destination["z"]]))
      target = {"dest_zone": destination["zone"], "dest_x": destinationX, "dest_y": destinationY, "dest_z": destinationZ, "dest_heading": round(eqAxes.eqHeadingFromHeading(destination["headingDegrees"]), 4)}
    if row["triggerDoor"] is not None and row["triggerDoor"] not in numbers:
      raise ValueError(f"Door '{piece['object']}' triggers '{row['triggerDoor']}', which is no interactive door of this zone")
    rows.append({
      "doorid": numbers[piece["object"]], "zone": short, "version": 0, "name": row["name"], "pos_y": y, "pos_x": x, "pos_z": z, "heading": heading,
      "opentype": row["openType"], "guild": 0, "lockpick": row["lockpick"], "keyitem": row["keyItem"], "nokeyring": row["noKeyring"],
      "triggerdoor": numbers[row["triggerDoor"]] if row["triggerDoor"] is not None else 0, "triggertype": row["triggerType"], "disable_timer": 0,
      "doorisopen": 0, "door_param": 0, "dest_instance": 0, "invert_state": 0, "incline": 0, "size": int(round(piece["scale"] * 100)), "buffer": 0,
      "client_version_mask": serverRows.allClientVersions, "is_ldon_door": 0, "close_timer_ms": row["closeTimerMilliseconds"], "dz_switch_id": 0,
      "min_expansion": -1, "max_expansion": -1, "content_flags": None, "content_flags_disabled": None,
    } | target)
  return rows, {piece["object"]: numbers[piece["object"]] for piece in doors}


def objectRows(zoneID, pieces):
  """The object rows of the interactive objects, in object-name order: at their place in the server's axes, their size their scale times
  100, the content filter's columns as zone_points rows write them (expansion -1, no flags)."""
  rows = []
  for piece in sorted((piece for piece in pieces if piece["row"]["table"] == "object"), key=lambda piece: piece["object"]):
    row = piece["row"]
    x, y, z, heading = serverPlace(piece)
    rows.append({
      "zoneid": zoneID, "version": 0, "xpos": x, "ypos": y, "zpos": z, "heading": heading, "itemid": 0, "charges": 0, "objectname": row["objectName"],
      "type": row["type"], "icon": 0, "size_percentage": 0, "unknown24": 0, "unknown60": 0, "unknown64": 0, "unknown68": 0, "unknown72": 0,
      "unknown76": 0, "unknown84": 0, "size": round(piece["scale"] * 100, 4), "solid_type": 0, "incline": 0, "tilt_x": 0, "tilt_y": 0,
      "display_name": row["displayName"], "min_expansion": -1, "max_expansion": -1, "content_flags": None, "content_flags_disabled": None,
    })
  return rows


def findBestZ(triangles, x, y, z):
  """Where the server stands a point on its .map's collision (map.cpp:34-71): the first surface at or below z plus the height adjust,
  else the first above, else None. triangles are in the zone's axes."""
  heights, _ = playerReach.heightsUnder(triangles, x, y)
  adjust = serverNav.serverPathing["findBestZHeightAdjust"]
  below = heights[heights <= z + adjust]
  if len(below):
    return float(below.max())
  above = heights[heights > z + adjust]
  return float(above.min()) if len(above) else None


def objectHeights(collision, pieces):
  """Each interactive object's height against where the server stands it at boot, on collision (the .map's triangles, server axes):
  [{object, z, server}] with server None where the .map holds no surface over or under it."""
  triangles = eqAxes.zoneFromServer(numpy.asarray(collision, dtype=numpy.float64))
  stands = []
  for piece in sorted((piece for piece in pieces if piece["row"]["table"] == "object"), key=lambda piece: piece["object"]):
    x, y, z = piece["position"]
    ground = findBestZ(triangles, x, y, z)
    stands.append({"object": piece["object"], "z": round(float(z), 4), "server": None if ground is None else round(ground, 4)})
  return stands


def movedFailures(stands):
  return [
    {
      "failure": "row moved by the server", "object": stand["object"], "z": stand["z"], "server": stand["server"],
      "message": (
        f"Object row '{stand['object']}' stands at {stand['z']:g}, but the server stands every object row on its .map as it boots: it would put"
        f" it at {'no ground' if stand['server'] is None else format(stand['server'], 'g')}; set it on the ground the zone ships"
      ),
    }
    for stand in stands if stand["server"] is None or abs(stand["server"] - stand["z"]) > standTolerance
  ]


def nearCondition(columns, row):
  return " AND ".join(f"ABS({column} - ({serverRows.literal(row[column])})) < {serverRows.literal(serverRows.storedFloatTolerance)}" for column in columns)


def doorMatch(row):
  return f"(doorid = {row['doorid']} AND name = {serverRows.literal(row['name'])} AND {nearCondition(doorMatchColumns, row)})"


def objectMatch(row):
  return (
    f"(objectname = {serverRows.literal(row['objectname'])} AND type = {row['type']} AND {nearCondition(objectMatchFloats, row)})"
  )


def ownedRecord(record, short, zoneID):
  """The record's door and object rows, checked to be this zone's as piecesFile gives them."""
  for row in record["doors"]:
    if set(row) != set(doorColumns) or (row["zone"], row["version"]) != (short, 0):
      raise ValueError(f"The record holds {short}'s version-0 doors rows as piecesFile gives them, got {row!r}")
  for row in record["objects"]:
    if set(row) != set(objectColumns) or row["version"] != 0:
      raise ValueError(f"The record holds version-0 object rows as piecesFile gives them, got {row!r}")
  return record


def piecesFile(zone, pieces, record, archiveSHA256):
  """<short>_pieces.sql for the zone's interactive pieces, or None without any: {text, doors, objects, doorNumbers}. record holds the
  doors and object rows earlier game exports of this short name wrote ({doors, objects}); they are deleted where the table still holds
  them as written, the rest of the zone's rows stay."""
  short, zoneID = zone["shortName"], zone["zoneId"]
  doors, doorNumbers = doorRows(short, pieces)
  objects = objectRows(zoneID, pieces)
  ownedRecord(record, short, zoneID)
  if not doors and not objects and not record["doors"] and not record["objects"]:
    return None
  text = [
    f"-- zonewright pieces: zone {short}", f"-- archive sha256: {archiveSHA256}", f"-- doors written: {serverRows.numberList([row['doorid'] for row in doors]) or 'none'}",
    f"-- objects written: {len(objects)}", *serverRows.blockStart,
  ]
  for row in doors:
    owned = [earlier for earlier in record["doors"] if earlier["doorid"] == row["doorid"]]
    exclusion = f" AND NOT ({' OR '.join(doorMatch(earlier) for earlier in owned)})" if owned else ""
    text.append(serverRows.signal(
      f"EXISTS (SELECT 1 FROM doors WHERE zone = {serverRows.literal(short)} AND doorid = {row['doorid']} AND (version = -1 OR (version = 0{exclusion})))",
      f"{short} door {row['doorid']} is not zonewright's: remove that row or place fewer doors",
    ))
  text += [f"  DELETE FROM doors WHERE zone = {serverRows.literal(short)} AND version = 0 AND {doorMatch(row)};" for row in record["doors"]]
  text += [f"  DELETE FROM object WHERE zoneid = {zoneID} AND version = 0 AND {objectMatch(row)};" for row in record["objects"]]
  for table, columns, rows in (("doors", doorColumns, doors), ("object", objectColumns, objects)):
    if rows:
      text.append(f"  INSERT INTO {table} ({', '.join(columns)}) VALUES")
      text += [f"    ({', '.join(serverRows.literal(row[column]) for column in columns)})" + ("," if index < len(rows) - 1 else ";") for index, row in enumerate(rows)]
  text += serverRows.blockEnd
  return {"text": "\n".join(text) + "\n", "doors": doors, "objects": objects, "doorNumbers": doorNumbers}


def readPieces(text, fileName="the pieces file"):
  """What <short>_pieces.sql holds: its zone, archive, the door ids and object count written, each door guard's id, the rows deleted,
  and the doors and object rows inserted."""
  lines = serverRows.Lines(text, fileName)
  shortName = lines.take(r"-- zonewright pieces: zone ([a-z0-9]{1,31})", "the header's zone").group(1)
  archiveSHA256 = lines.take(r"-- archive sha256: ([0-9a-f]{64})", "the archive's SHA-256").group(1)
  doorIDs = serverRows.numbersOf(lines.take(r"-- doors written: (none|[0-9]+(?:, [0-9]+)*)", "the doors written").group(1))
  objectCount = int(lines.take(r"-- objects written: ([0-9]+)", "the objects written").group(1))
  for statement in serverRows.blockStart:
    lines.exact(statement)
  guards = []
  while serverRows.startsWith(lines, "  IF "):
    match = lines.take(
      rf"  IF EXISTS \(SELECT 1 FROM doors WHERE zone = '{shortName}' AND doorid = ([0-9]+) AND \(version = -1 OR \(version = 0(?: AND NOT \(.+\))?\)\)\)" + serverRows.signalTail,
      "a door guard",
    )
    guards.append(int(match.group(1)))
  deleted = {"doors": 0, "objects": 0}
  while serverRows.startsWith(lines, "  DELETE"):
    match = lines.take(r"  DELETE FROM (doors|object) WHERE .+;", "a delete")
    deleted["doors" if match.group(1) == "doors" else "objects"] += 1
  inserted = {"doors": [], "object": []}
  for table, columns in (("doors", doorColumns), ("object", objectColumns)):
    if serverRows.startsWith(lines, f"  INSERT INTO {table} "):
      lines.exact(f"  INSERT INTO {table} ({', '.join(columns)}) VALUES")
      while True:
        match = lines.take(r"    \((.+)\)([,;])", f"a {table} row")
        values = serverRows.literals(match.group(1), f"{fileName} line {lines.index}")
        if len(values) != len(columns):
          raise ValueError(f"{fileName} line {lines.index}: a {table} row of {len(values)} values, not {len(columns)}")
        inserted[table].append(dict(zip(columns, values)))
        if match.group(2) == ";":
          break
  for statement in serverRows.blockEnd:
    lines.exact(statement)
  lines.finish()
  if [row["doorid"] for row in inserted["doors"]] != doorIDs or guards != doorIDs or len(inserted["object"]) != objectCount:
    raise ValueError(f"{fileName}: its header, guards, and rows disagree on what it writes")
  return {"shortName": shortName, "archiveSHA256": archiveSHA256, "deleted": deleted, "doors": inserted["doors"], "objects": inserted["object"]}
