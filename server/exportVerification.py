"""verifyExport's checks: a game export read back from its folder and judged against the open, saved scene and against itself
(docs/serverFiles.md, Verifying an export). Each check is {check, passed, details}, its details listing each failure with where it
lies; every check runs whatever the others find, reading only the files it needs. The server's view is drawn from the exported files
alone (serverMapDrawing.drawServerPlan), and the comparison sheet sets each view of the scene beside the same view of the archive and
their difference, largest differences first."""
import hashlib
import io
import json
import struct
import zlib
from pathlib import Path

import numpy
from mcp.server.mcpserver.exceptions import ToolError
from PIL import Image

import buildTolerances
import eqArchive
import eqAxes
import eqgFiles
import eqTextures
import eqZones
import exportPipeline
import playerReach
import serverMapDrawing
import serverMapFiles
import serverNav
import serverRows
import viewSheets
import zoneGeometry
import zoneSources

# Peridot's Highpass Hold .map, rebuilt along the server's float32 path, lies this far at most from the client's own placing of its
# triangles (azone's round trip of the turns moves them); a .map made from the archive lies nearer, so a larger difference is a .map
# of other geometry.
collisionTolerance = 3.2e-4
# getSwimVolumes and getZoneLines give a box's corners to two decimals.
listedCornerTolerance = 0.01
failuresListed = 20
# Rows of three cells, so a sheet holds no more views than renderReviewSet's.
sheetRows = 5
# A pixel draws full red in the difference cell at this many levels, so differences inside the 99th-percentile tolerance read faint.
differenceFullRed = 2 * buildTolerances.roundTripPercentileLevels
loadedWaterMapLie = (
  'the server would log "Loaded Water Map" and drop it (water_map.cpp:48-63), and without a water map turns NPC nav pathing off'
  " (mob_movement_manager.cpp:1035)"
)
# Another zone's ground under a landing: its solid surfaces and invisible walls. Its cutout and translucent ones, which its geometry
# does not say players stand on, are not, so a landing on them alone is a finding to look at.
groundSurfaces = [zoneGeometry.surfaceCode[kind] for kind in ("solid", "invisible")]


def checked(name, failures, **details):
  return {"check": name, "passed": not failures, "details": details | {"failures": failures}}


def listedFailures(failures):
  """The first failuresListed, and a count of the rest."""
  if len(failures) <= failuresListed:
    return failures
  return failures[:failuresListed] + [{"more": len(failures) - failuresListed, "message": f"{len(failures) - failuresListed} more like these"}]


def sha256(data):
  return hashlib.sha256(data).hexdigest()


def exportedGame(path):
  """The game export at path: (archive path, short name, manifest), refusing a path that is not an absolute <short>.eqg with a game
  export's manifest beside it."""
  archivePath = Path(path)
  if not archivePath.is_absolute() or archivePath.suffix != ".eqg" or not archivePath.is_file():
    raise ToolError(f"'{path}' is not an absolute path to an exported <short>.eqg")
  short = archivePath.stem
  if not eqgFiles.zoneNamePattern.match(short):
    raise ToolError(f"'{archivePath.name}' is not named by a zone short name: {eqgFiles.zoneNameRule}")
  manifestPath = exportPipeline.manifestPath(archivePath.parent, short)
  if not manifestPath.is_file():
    raise ToolError(f"No manifest {manifestPath.name} lies beside '{path}': verifyExport reads back a game export exportZone wrote")
  try:
    manifest = json.loads(manifestPath.read_text(encoding="ascii"))
  except (OSError, ValueError) as error:
    raise ToolError(f"The manifest {manifestPath} cannot be read: {type(error).__name__}: {error}") from error
  if not isinstance(manifest, dict) or not isinstance(manifest.get("files"), dict) or not isinstance(manifest.get("blend"), str):
    raise ToolError(f"{manifestPath} is no export's manifest: it holds no files and .blend")
  if manifest.get("purpose") != "game":
    raise ToolError(f"{manifestPath} is a {manifest.get('purpose')!r} export's manifest; verifyExport reads back a game export, whose server files it checks")
  if manifest.get("shortName") != short:
    raise ToolError(f"{manifestPath} is the manifest of '{manifest.get('shortName')}', not of {archivePath.name}")
  return archivePath, short, manifest


def readFile(path):
  return path.read_bytes() if path.is_file() else None


def exportedFiles(folder, short):
  """The export's files by kind (archive, map, water, nav, rows, waysIn), each its bytes or None where none lies."""
  paths = exportPipeline.serverFilePaths(folder, short) | {"archive": folder / f"{short}.eqg"}
  return {kind: readFile(path) for kind, path in paths.items()}


def manifestCheck(folder, short, manifest):
  """Check 1: every file the manifest lists lies in place with its size and SHA-256, every file a game export always writes is listed,
  nothing of this zone's lies beside it unlisted, and every server file is named by the lowercase short name."""
  writes = {path.relative_to(folder).as_posix(): path for path in exportPipeline.exportFilePaths(folder, short) if path != exportPipeline.manifestPath(folder, short)}
  serverPaths = exportPipeline.serverFilePaths(folder, short)
  required = [f"{short}.eqg"] + [serverPaths[kind].relative_to(folder).as_posix() for kind in ("map", "water", "nav", "rows")]
  listed = manifest["files"]
  failures = []
  for name, record in listed.items():
    if name not in writes:
      failures.append({"file": name, "message": f"The manifest lists {name}, which no game export of {short} writes: its files are named by the lowercase short name"})
      continue
    if not isinstance(record, dict) or set(record) != {"bytes", "sha256"}:
      failures.append({"file": name, "message": f"The manifest's record of {name} is not {{bytes, sha256}}: {record!r}"})
      continue
    data = readFile(writes[name])
    if data is None:
      failures.append({"file": name, "message": f"{name} is missing"})
    elif len(data) != record["bytes"] or sha256(data) != record["sha256"]:
      failures.append({
        "file": name, "message": f"{name} is not the file the export wrote: {len(data):,} bytes, SHA-256 {sha256(data)}; the manifest records {record['bytes']:,}, {record['sha256']}",
      })
  for name in required:
    if name not in listed:
      failures.append({"file": name, "message": f"The manifest does not list {name}, which every game export writes"})
  for name, path in writes.items():
    if name not in listed and name not in required and path.is_file():
      failures.append({"file": name, "message": f"{name} lies in the export's place but the manifest does not list it, so it is not this export's"})
  return checked("manifest", failures, listed=len(listed))


def archiveEntries(data, label):
  archive = eqArchive.EQArchive(label, data)
  return {name: archive.read(name) for name in archive.entries}


def currentCheck(exported, rebuilt, why):
  """Check 2: the archive rebuilt in memory from the saved scene (rebuilt, or None with why it could not be) equals the exported one,
  entry by entry."""
  if exported is None:
    return checked("current", [{"message": "No archive lies at the export's path to compare"}])
  if rebuilt is None:
    return checked("current", [{"message": f"The archive cannot be rebuilt from the saved scene: {why}"}])
  try:
    ours, scene = archiveEntries(exported, "the exported archive"), archiveEntries(rebuilt, "the scene's archive")
  except (OSError, ValueError, struct.error, zlib.error) as error:
    return checked("current", [{"message": f"The exported archive cannot be read: {type(error).__name__}: {error}"}])
  failures = []
  for name in sorted(set(ours) | set(scene)):
    change = "only in the export" if name not in scene else "only in the scene's rebuild" if name not in ours else "differs" if ours[name] != scene[name] else None
    if change is not None:
      failures.append({"entry": name, "change": change, "message": f"{name} {change}: the scene changed since this export"})
  return checked("current", failures, entries=len(ours), identical=exported == rebuilt)


def parsedEntry(name, data):
  """An archive entry read as the client's reader of its kind reads it."""
  kind = name.rsplit(".", 1)[-1]
  if kind == "zon":
    return eqgFiles.parseZone(data, name)
  if kind in ("mod", "ter"):
    return eqgFiles.parseModel(data, name)
  if kind == "dds":
    if data[:4] != b"DDS ":
      raise ValueError(f"{name}: no DDS header")
    with Image.open(io.BytesIO(eqTextures.readableTexture(name, data)[1])) as image:
      image.load()
    return None
  if kind == "lit":
    return eqZones.eqgLitColors(data, name)
  raise ValueError(f"{name}: no EQG zone file is of its kind")


def boxFromCorners(minimum, maximum):
  return [(low + high) / 2 for low, high in zip(minimum, maximum)], [(high - low) / 2 for low, high in zip(minimum, maximum)]


def regionFailures(regions, swimVolumes, zoneLines):
  """The .zon's regions against the scene's swim boxes and then its own zone lines, in order: names, centers, and half extents."""
  expected = [(entry["name"], *boxFromCorners(entry["minimum"], entry["maximum"])) for entry in swimVolumes]
  expected += [(line["name"], *boxFromCorners(line["minimum"], line["maximum"])) for line in zoneLines if "clientContent" not in line]
  failures = []
  for index in range(max(len(regions), len(expected))):
    if index >= len(regions):
      name, center, _ = expected[index]
      failures.append({"region": name, "center": center, "message": f"The .zon lacks region {name}, which the scene holds at {center}"})
      continue
    region = regions[index]
    if index >= len(expected):
      failures.append({"region": region["name"], "center": list(region["center"]), "message": f"The .zon holds region {region['name']}, which the scene does not"})
      continue
    name, center, half = expected[index]
    found = [list(region["center"]), [abs(value) for value in region["halfExtents"]]]
    if region["name"] != name:
      failures.append({"region": region["name"], "center": found[0], "message": f"The .zon's region {index} is {region['name']}, where the scene's is {name}"})
    elif any(abs(a - b) > listedCornerTolerance for a, b in zip(found[0] + found[1], center + half)):
      failures.append({
        "region": name, "center": found[0],
        "message": f"Region {name} stands at {found[0]} with half extents {found[1]} in the .zon, at {center} with {half} in the scene",
      })
  return failures


def archiveCheck(archiveBytes, short, swimVolumes, zoneLines):
  """Check 3: every entry parses, every placement's model is in the archive, and the .zon's regions are the swim boxes and then the zone
  lines as the scene holds them."""
  if archiveBytes is None:
    return checked("archive", [{"message": "No archive lies at the export's path"}])
  try:
    entries = archiveEntries(archiveBytes, "the exported archive")
  except (OSError, ValueError, struct.error, zlib.error) as error:
    return checked("archive", [{"message": f"The archive cannot be read: {type(error).__name__}: {error}"}])
  failures, zone = [], None
  for name, data in sorted(entries.items()):
    try:
      parsed = parsedEntry(name, data)
    except (OSError, ValueError, struct.error, IndexError) as error:
      failures.append({"entry": name, "message": f"{name} does not parse: {type(error).__name__}: {error}"})
      continue
    if name == f"{short}.zon":
      zone = parsed
  if f"{short}.zon" not in entries:
    failures.append({"entry": f"{short}.zon", "message": f"The archive holds no {short}.zon"})
  if zone is not None:
    for index, placement in enumerate(zone["placements"]):
      if placement["model"] not in entries:
        failures.append({
          "placement": placement["name"], "model": placement["model"], "at": list(placement["position"]),
          "message": f"Placement {index} '{placement['name']}' at {list(placement['position'])} places {placement['model']}, which the archive does not hold",
        })
    failures += regionFailures(zone["regions"], swimVolumes, zoneLines)
  return checked("archive", listedFailures(failures), entries=len(entries), placements=None if zone is None else len(zone["placements"]), regions=None if zone is None else [region["name"] for region in zone["regions"]])


def clientSolid(zone, entries):
  """The archive's triangles the client collides with, placed as the client places them (eqgFiles.placeVertices), in the .map's
  order: the terrain's, then each other placement's in .zon order. Float64, zone axes."""
  models = {}
  parts = []
  for placement in sorted(zone["placements"], key=lambda placement: not placement["model"].endswith(".ter")):
    if placement["model"] not in models:
      models[placement["model"]] = eqgFiles.parseModel(entries[placement["model"]], placement["model"])
    model = models[placement["model"]]
    solid = model["triangles"][(model["triangleFlags"] & eqgFiles.passableFlag) == 0]
    parts.append(eqgFiles.placeVertices(model["vertices"].astype(numpy.float64), placement)[solid])
  return numpy.concatenate(parts) if parts else numpy.zeros((0, 3, 3))


def mapCheck(mapData, archiveBytes, short):
  """Check 4: the .map inflates to its stated size and decodes to its last byte, every placement names a model it holds, it is the .map
  the archive makes, and the collision the server rebuilds from it is the archive's solid triangles as the client places them, in
  count and order, each corner within collisionTolerance."""
  if mapData is None:
    return checked("map", [{"message": "No .map lies in the export"}])
  try:
    content = serverMapFiles.readMap(mapData)
  except ValueError as error:
    return checked("map", [{"message": str(error)}])
  failures = []
  modelNames = {model["name"] for model in content["models"]}
  for index, placement in enumerate(content["placements"]):
    if placement["name"] not in modelNames:
      failures.append({
        "placement": index, "model": placement["name"], "at": placement["position"].tolist(),
        "message": f"The .map's placement {index} at {placement['position'].tolist()} names {placement['name']}, which it holds no model of: the server would skip it",
      })
  details = {"models": len(content["models"]), "placements": len(content["placements"])}
  if archiveBytes is None:
    return checked("map", failures + [{"message": "No archive lies at the export's path to make its .map from"}], **details)
  try:
    entries = archiveEntries(archiveBytes, "the exported archive")
    zone = eqgFiles.parseZone(entries[f"{short}.zon"], f"{short}.zon")
    expected = serverMapFiles.mapBytes(serverMapFiles.zoneFilesOf(archiveBytes))
    solid = clientSolid(zone, entries)
  except (OSError, KeyError, ValueError, struct.error, zlib.error, IndexError) as error:
    return checked("map", failures + [{"message": f"The archive makes no .map to compare: {type(error).__name__}: {error}"}], **details)
  if mapData != expected:
    archiveContent = serverMapFiles.readMap(expected, "the archive's .map")
    counts = {key: (len(content[key]), len(archiveContent[key])) for key in ("collidableIndices", "nonCollidableIndices", "models", "placements")}
    failures.append({
      "counts": {key: {"map": ours, "archive": theirs} for key, (ours, theirs) in counts.items()},
      "message": f"The .map is not the one the archive makes (serverMapFiles.mapBytes): this .map, then the archive's, hold {counts}",
    })
  if failures:
    return checked("map", failures, **details)
  collision = serverMapFiles.collisionTriangles(content).astype(numpy.float64)
  placed = eqAxes.serverFromZone(solid)
  details |= {"collisionTriangles": len(collision), "archiveSolidTriangles": len(placed)}
  if len(collision) != len(placed):
    failures.append({"message": f"The server collides with {len(collision):,} triangles from the .map, the client with {len(placed):,} of the archive's"})
    return checked("map", failures, **details)
  if len(collision):
    differences = numpy.abs(collision - placed).max(axis=(1, 2))
    worst = int(numpy.argmax(differences))
    details["largestDifference"] = float(differences[worst])
    over = int((differences > collisionTolerance).sum())
    if over:
      at = [round(float(value), 3) for value in eqAxes.zoneFromServer(collision[worst]).mean(axis=0)]
      failures.append({
        "triangle": worst, "at": at, "difference": float(differences[worst]),
        "message": (
          f"{over:,} of the .map's triangles lie further than {collisionTolerance:g} from the archive's, as the client places them, in order;"
          f" the furthest, triangle {worst} at {at}, by {float(differences[worst]):g}"
        ),
      })
  return checked("map", failures, **details)


def waterCheck(waterData, archiveBytes, short):
  """Check 5: the .wtr as the server reads it (magic, version 2, its records to its exact length), and the .wtr the archive's .zon
  regions make."""
  if waterData is None:
    return checked("water", [{"message": f"No .wtr lies in the export: {loadedWaterMapLie}"}])
  try:
    records = serverMapFiles.readWater(waterData)
  except ValueError as error:
    return checked("water", [{"message": f"{error}: {loadedWaterMapLie}"}])
  details = {"records": len(records)}
  if archiveBytes is None:
    return checked("water", [{"message": "No archive lies at the export's path to make its .wtr from"}], **details)
  try:
    regions = eqgFiles.parseZone(archiveEntries(archiveBytes, "the exported archive")[f"{short}.zon"], f"{short}.zon")["regions"]
    expected = serverMapFiles.waterBytes(regions)
  except (OSError, KeyError, ValueError, struct.error, zlib.error) as error:
    return checked("water", [{"message": f"The archive makes no .wtr to compare: {type(error).__name__}: {error}"}], **details)
  failures = []
  if waterData != expected:
    wanted = serverMapFiles.readWater(expected, "the archive's .wtr")
    for index in range(max(len(records), len(wanted))):
      ours = records[index] if index < len(records) else None
      theirs = wanted[index] if index < len(wanted) else None
      if ours != theirs:
        place = list((ours or theirs)["position"])
        failures.append({
          "record": index, "at": place,
          "message": f".wtr record {index} at {place} is {ours}, where the archive's regions make {theirs}",
        })
  return checked("water", listedFailures(failures), **details)


def navPayloadBytes(data, source):
  """A .nav's inflated payload, read whole (serverMapFiles.readNav) first."""
  serverMapFiles.readNav(data, source)
  _, _, _, inflatedSize = serverMapFiles.navFileHeader.unpack_from(data, 0)
  return serverMapFiles.inflated(data[serverMapFiles.navFileHeader.size:], inflatedSize, source, serverMapFiles.navFileHeader.size)


def inVolume(corner, record, margin):
  """Whether a point (zone axes) lies in a .wtr box grown by margin (plan, height)."""
  half = numpy.abs(numpy.asarray(record["halfExtents"]) * numpy.asarray(record["scale"])) + [margin[0], margin[0], margin[1]]
  return bool((numpy.abs(numpy.asarray(corner) - numpy.asarray(record["position"])) <= half).all())


def volumeFailures(inspection, records):
  """Nav polygons of a volume's area (water, lava, the zone lines' Disabled) lying outside every .wtr box of a type making that area.
  A marked cell's center lies in its box, so a polygon's corner may lie a cell outside it, and up to the NPC's climb over its top,
  where its height is taken from a neighbouring cell's floor."""
  settings = serverNav.serverNavSettings
  margin = (settings["cellSize"], settings["cellHeight"] + settings["agentClimb"])
  boxes = {}
  for record in records:
    if record["type"] in serverNav.volumeAreas:
      boxes.setdefault(serverNav.volumeAreas[record["type"]], []).append(record)
  failures = []
  for tile in inspection["tiles"]:
    for index, polygon in enumerate(tile["polygons"]):
      if polygon["area"] not in serverNav.volumeAreas.values():
        continue
      corners = [[*point, height] for point, height in zip(polygon["outline"], polygon["heights"])]
      if not all(any(inVolume(corner, record, margin) for record in boxes.get(polygon["area"], [])) for corner in corners):
        at = [round(float(value), 2) for value in numpy.mean(corners, axis=0)]
        name = serverMapFiles.navAreaNames[polygon["area"]]
        failures.append({
          "tile": list(tile["key"]), "polygon": index, "area": name, "at": at,
          "message": f"Nav polygon {index} of tile {list(tile['key'])} at {at} is {name}, and lies outside every .wtr box that marks {name}",
        })
  return failures


def navCheck(navData, mapData, waterData, safePoint, toolingRoot, reportProgress):
  """Check 6: the .nav's container inflates; its parameters are serverNavSettings over the .map's collidable extents; the pinned helper
  rebuilt on this .map and .wtr reproduces its payload byte for byte; Detour loads every tile; and its water, lava, and Disabled
  polygons lie in their .wtr boxes. Returns (check, the nav's inspection or None)."""
  if navData is None:
    return checked("nav", [{"message": "No .nav lies in the export: the server would path NPCs straight, through walls"}]), None
  try:
    nav = serverMapFiles.readNav(navData)
  except ValueError as error:
    return checked("nav", [{"message": str(error)}]), None
  failures = []
  details = {"tiles": len(nav["tiles"]), "parameters": nav["parameters"]}
  try:
    collision = serverMapFiles.mapCollision(mapData) if mapData is not None else None
    records = serverMapFiles.readWater(waterData) if waterData is not None else None
  except ValueError as error:
    collision = records = None
    failures.append({"message": f"The .map or .wtr cannot be read, so the .nav cannot be judged against them: {error}"})
  if collision is None or records is None:
    if not failures:
      failures.append({"message": "The export lacks its .map or .wtr, so the .nav cannot be judged against them"})
  else:
    expected = serverNav.navParameters(collision)
    differing = sorted(key for key in expected if expected[key] != nav["parameters"][key])
    if differing:
      failures.append({
        "parameters": differing,
        "message": f"The .nav's parameters {differing} are {[nav['parameters'][key] for key in differing]}; serverNavSettings over the .map's extents make {[expected[key] for key in differing]}",
      })
    try:
      rebuilt, _ = serverNav.navBytes(mapData, waterData, toolingRoot, reportProgress)
    except (ToolError, ValueError) as error:
      failures.append({"message": f"The pinned helper builds no nav from this .map and .wtr: {error}"})
    else:
      ours, theirs = navPayloadBytes(navData, "the .nav"), navPayloadBytes(rebuilt, "the rebuilt nav")
      if ours != theirs:
        first = next((index for index, (a, b) in enumerate(zip(ours, theirs)) if a != b), min(len(ours), len(theirs)))
        failures.append({
          "byte": first,
          "message": f"The pinned helper rebuilt on this .map and .wtr makes another nav: its {len(theirs):,}-byte payload first differs from the .nav's {len(ours):,} at byte {first}",
        })
  try:
    inspection = serverNav.inspectNav(navData, safePoint, [], toolingRoot, reportProgress)
  except (ToolError, ValueError) as error:
    failures.append({"message": f"Detour does not load the .nav as the server does: {error}"})
    return checked("nav", failures, **details), None
  if len(inspection["tiles"]) != len(nav["tiles"]):
    failures.append({"message": f"Detour loads {len(inspection['tiles'])} of the .nav's {len(nav['tiles'])} tiles"})
  if records is not None:
    failures += listedFailures(volumeFailures(inspection, records))
  details |= {"polygons": inspection["polygons"], "islands": len(inspection["islands"]), "mainPiece": inspection["mainPiece"]}
  return checked("nav", failures, **details), inspection


def zoneLinesOwn(zoneLines):
  return [line for line in zoneLines if "clientContent" not in line]


def columnFailures(what, found, expected):
  """A row's columns that differ from what the scene's inputs make, each named."""
  return [
    {"file": what, "column": column, "found": found.get(column), "expected": expected.get(column), "message": f"{what}: {column} is {found.get(column)!r}, the scene's inputs make {expected.get(column)!r}"}
    for column in sorted(set(found) | set(expected)) if found.get(column) != expected.get(column)
  ]


def guardsExpected(zone, targets, numbers):
  """Every guard of <short>.sql by kind and the values it names: the zone's id and row, one version-0 row, a template to copy, each
  zone line target's row, and each zone_points number written."""
  short = zone["shortName"]
  guards = [
    ("zoneID", {"zoneID": zone["zoneId"], "shortName": short}), ("rowID", {"shortName": short, "zoneID": zone["zoneId"]}),
    ("duplicateRow", {"shortName": short}), ("template", {"shortName": short, "template": zone["serverTemplate"]}),
  ]
  guards += [("target", {"target": name, "targetID": targetID}) for name, targetID in sorted(targets.items())]
  return guards + [("foreignZonePoints", {"shortName": short, "number": number}) for number in numbers]


def rowsCheck(rowsData, waysInData, zone, zoneLines, entries, clientZones, skyFogColor, archiveBytes, short):
  """Check 7: our reader parses both files; the zone row's written columns are what the stored inputs make and its template is
  serverTemplate; every guard is there with its values; one zone_points row per zone line box, numbered ten times its number, at a point
  inside the box, its target id the client's; and the ways-in file holds exactly the zoneIn entries naming their neighbour's row."""
  if rowsData is None:
    return checked("rows", [{"message": f"No {short}.sql lies in the export"}])
  try:
    rows = serverRows.readRows(rowsData.decode("ascii"), f"{short}.sql")
  except (UnicodeDecodeError, ValueError) as error:
    return checked("rows", [{"message": f"{short}.sql does not read: {error}"}])
  failures = []
  try:
    expected = serverRows.zoneRowValues(zone, skyFogColor)
  except ValueError as error:
    return checked("rows", [{"message": f"The scene's inputs make no zone row to compare: {error}"}])
  if rows["shortName"] != short or rows["zoneInsert"]["absentRow"] != short or rows["zoneUpdate"]["shortName"] != short:
    failures.append({"message": f"{short}.sql writes the rows of '{rows['shortName']}', not {short}"})
  if archiveBytes is not None and rows["archiveSHA256"] != sha256(archiveBytes):
    failures.append({"message": f"{short}.sql was written for the archive {rows['archiveSHA256']}, not this one ({sha256(archiveBytes)})"})
  if rows["template"] != zone["serverTemplate"] or rows["zoneInsert"]["template"] != zone["serverTemplate"]:
    failures.append({"message": f"{short}.sql copies its other columns from '{rows['zoneInsert']['template']}', not serverTemplate '{zone['serverTemplate']}'"})
  failures += columnFailures(f"{short}.sql's zone insert", rows["zoneInsert"]["written"], expected)
  failures += columnFailures(f"{short}.sql's zone update", rows["zoneUpdate"]["values"], {key: value for key, value in expected.items() if key not in serverRows.keyColumns})
  lines = sorted(zoneLinesOwn(zoneLines), key=lambda line: line["number"])
  points = rows["zonePoints"]
  if [point["number"] for point in points] != [line["number"] * 10 for line in lines] or rows["numbersWritten"] != [point["number"] for point in points]:
    failures.append({
      "message": f"{short}.sql writes zone_points {[point['number'] for point in points]} (its header says {rows['numbersWritten']}), the zone lines {[line['name'] for line in lines]} make {[line['number'] * 10 for line in lines]}",
    })
  targets = {}
  for line in lines:
    point = next((point for point in points if point["number"] == line["number"] * 10), None)
    if point is None:
      continue
    at = [point["y"], point["x"], point["z"]]
    if not all(low <= value <= high for value, low, high in zip(at, line["minimum"], line["maximum"])):
      failures.append({"zonePoint": point["number"], "at": at, "message": f"zone_points {point['number']} stands at {at}, outside zone line {line['name']}'s box {line['minimum']} to {line['maximum']}"})
    if line["target"] is None:
      failures.append({"zonePoint": point["number"], "message": f"Zone line {line['name']} has no target in the scene; zone_points {point['number']} leads somewhere"})
      continue
    target = line["target"]["zone"]
    if target == short:
      targetID = zone["zoneId"]
    elif target in clientZones:
      targetID = targets[target] = clientZones[target]
    else:
      failures.append({"zonePoint": point["number"], "message": f"zone_points {point['number']} leads to '{target}', which the client does not register"})
      continue
    if point["target_zone_id"] != targetID:
      failures.append({"zonePoint": point["number"], "message": f"zone_points {point['number']} leads to zone id {point['target_zone_id']}, the client's id for {target} is {targetID}"})
  found = [(guard["guard"], {key: value for key, value in guard.items() if key not in ("guard", "message", "owned")}) for guard in rows["guards"]]
  for kind, values in guardsExpected(zone, targets, [point["number"] for point in points]):
    if (kind, values) not in found:
      failures.append({"guard": kind, "values": values, "message": f"{short}.sql lacks its {kind} guard on {values}"})
  failures += waysInFailures(waysInData, zone, entries, archiveBytes, short)
  details = {"zonePoints": [point["number"] for point in points], "guards": len(rows["guards"]), "waysIn": waysInData is not None}
  return checked("rows", failures, **details)


def waysInFailures(waysInData, zone, entries, archiveBytes, short):
  """The ways-in file against the stored zoneIn entries naming their neighbour's row: one guarded update per entry, pointing that row at
  the entry's arrival point and heading; no file without such an entry."""
  wanted = sorted((entry for entry in entries if entry["source"] == "placeEntry" and entry["kind"] == "zoneIn" and entry["fromNumber"] is not None), key=lambda entry: (entry["fromZone"], entry["fromNumber"]))
  if waysInData is None:
    return [{"message": f"No {short}_waysIn.sql lies in the export, and zoneIn entries {[entry['name'] for entry in wanted]} name their neighbours' rows"}] if wanted else []
  if not wanted:
    return [{"message": f"{short}_waysIn.sql lies in the export, and no zoneIn entry names a neighbour's row"}]
  try:
    waysIn = serverRows.readWaysIn(waysInData.decode("ascii"), f"{short}_waysIn.sql")
  except (UnicodeDecodeError, ValueError) as error:
    return [{"message": f"{short}_waysIn.sql does not read: {error}"}]
  failures = []
  if waysIn["shortName"] != short or (archiveBytes is not None and waysIn["archiveSHA256"] != sha256(archiveBytes)):
    failures.append({"message": f"{short}_waysIn.sql was written for '{waysIn['shortName']}' and the archive {waysIn['archiveSHA256']}, not this export"})
  expected = [
    {
      "fromZone": entry["fromZone"], "fromNumber": entry["fromNumber"], "target_zone_id": zone["zoneId"],
      **dict(zip(("target_x", "target_y", "target_z"), serverRows.arrivalPoint(entry))), "target_heading": eqAxes.eqHeadingFromHeading(entry["headingDegrees"]),
    }
    for entry in wanted
  ]
  found = [{key: value for key, value in row.items() if key != "message"} for row in waysIn["rows"]]
  if found != expected:
    failures.append({"rows": found, "message": f"{short}_waysIn.sql points {found} at the zone, the zoneIn entries {[entry['name'] for entry in wanted]} make {expected}"})
  return failures


def zoneLandingRows(rows, zoneID):
  """The zone_points rows leading back into the zone at a whole point (no coordinate kept): (row, zone-axis landing)."""
  landings = []
  for point in rows["zonePoints"]:
    target = [point["target_y"], point["target_x"], point["target_z"]]
    if point["target_zone_id"] == zoneID and eqAxes.keptCoordinate not in target:
      landings.append((point, target))
  return landings


def otherZoneGround(clientRoot, zoneName, landings):
  """Whether ground lies under each landing ([x, y, z], zone axes) in a client zone, looking down from findBestZHeightAdjust over it on
  the zone's collision as the client loads it (zoneGeometry): the height found, or None."""
  geometry = zoneGeometry.buildGeometry(clientRoot, zoneSources.loadedVariant(clientRoot, zoneName)[1])
  triangles = geometry["vertices"][geometry["triangles"][numpy.isin(geometry["triangleSurfaces"], groundSurfaces)]]
  adjust = serverNav.serverPathing["findBestZHeightAdjust"]
  found = []
  for x, y, z in landings:
    heights, _ = playerReach.heightsUnder(triangles, x, y)
    below = heights[heights <= z + adjust]
    found.append(round(float(below.max()), 3) if len(below) else None)
  return found


def groundCheck(mapData, rowsData, entries, clientRoot, clientZones, short):
  """Check 8: the ground the server finds on the .map (exportPipeline.groundFailures) under the safe point (as the zone row holds it),
  every stored entry, and every zone_points landing back in the zone; and, in another zone, the ground under the landing on that zone's
  client collision, whose underworld is unknown here, so a miss is a finding."""
  if mapData is None or rowsData is None:
    return checked("ground", [{"message": "The export lacks its .map or its rows, so no ground is looked for"}])
  try:
    collision = serverMapFiles.mapCollision(mapData)
    rows = serverRows.readRows(rowsData.decode("ascii"), f"{short}.sql")
  except (UnicodeDecodeError, ValueError) as error:
    return checked("ground", [{"message": f"The .map or the rows do not read, so no ground is looked for: {error}"}])
  values = rows["zoneUpdate"]["values"]
  zoneID = rows["zoneInsert"]["written"].get("zoneidnumber")
  places = [{"arrival": "safe point", "kind": "safePoint", "at": [values["safe_y"], values["safe_x"], values["safe_z"]]}]
  places += [{"arrival": entry["name"], "kind": entry["kind"], "at": entry["at"]} for entry in entries if entry["source"] == "placeEntry"]
  places += [{"arrival": f"zone_points {point['number']}", "kind": "teleport", "at": landing} for point, landing in zoneLandingRows(rows, zoneID)]
  failures = exportPipeline.groundFailures(collision, values["underworld"], places)
  names = {targetID: name for name, targetID in clientZones.items()}
  elsewhere = {}
  for point in rows["zonePoints"]:
    landing = [point["target_y"], point["target_x"], point["target_z"]]
    if point["target_zone_id"] != zoneID and eqAxes.keptCoordinate not in landing:
      elsewhere.setdefault(point["target_zone_id"], []).append((point["number"], landing))
  findings, landed = [], []
  for targetID, landings in sorted(elsewhere.items()):
    if targetID not in names:
      findings.append({"zone": targetID, "message": f"The client registers no zone under id {targetID}, so the ground under zone_points {[number for number, _ in landings]}'s landings is not looked for"})
      continue
    try:
      grounds = otherZoneGround(clientRoot, names[targetID], [landing for _, landing in landings])
    except (OSError, ValueError) as error:
      findings.append({"zone": names[targetID], "message": f"{names[targetID]}'s collision cannot be read, so the ground under its landings is not looked for: {error}"})
      continue
    for (number, landing), ground in zip(landings, grounds):
      landed.append({"zonePoint": number, "zone": names[targetID], "at": landing, "ground": ground})
      if ground is None:
        findings.append({
          "zonePoint": number, "zone": names[targetID], "at": landing,
          "message": f"zone_points {number} lands players at {landing} in {names[targetID]}, over no ground its collision holds from 1 over it down; its underworld is unknown here",
        })
  return checked("ground", failures, places=places, underworld=values["underworld"], otherZones=landed, findings=findings)


def containmentCheck(mapData, waterData, entries, regions, underworld, manifest, toolingRoot, reportProgress):
  """Check 9: the lane the server files give (playerReach.laneFromServerFiles), seeded where the scene's entries stand, judged with its
  regions and plot entrances, holds no leak a game export refuses; its reached area beside the manifest's."""
  if mapData is None or waterData is None:
    return checked("containment", [{"message": "The export lacks its .map or .wtr, so no lane is had from them"}])
  seeds = [
    {"name": entry["name"], "point": entry["at"], "kind": entry["kind"], "isolated": entry.get("isolated", False), "headingDegrees": entry["headingDegrees"]}
    for entry in entries if entry["kind"] in playerReach.seedKinds
  ]
  plots = [{"name": entry["name"], "point": entry["at"]} for entry in entries if entry["kind"] == "plotEntrance"]
  try:
    lane, description, status, seconds = playerReach.laneFromServerFiles(mapData, waterData, seeds, toolingRoot, reportProgress, regions, plots, underworld)
  except (ToolError, ValueError) as error:
    return checked("containment", [{"message": f"No lane is had from the server files: {error}"}])
  analysis = playerReach.analyse(lane, description)
  failures = [exportPipeline.leakFailure(problem, underworld, "the server files' lane problem") for problem in analysis["problems"] if problem["kind"] in exportPipeline.leakKinds]
  for kind in exportPipeline.leakKinds:
    if kind in analysis["moreProblems"]:
      failures.append({"failure": kind, "more": analysis["moreProblems"][kind], "message": f"{analysis['moreProblems'][kind]['count']} more {kind} problems"})
  manifestLane = manifest.get("lane")
  return checked(
    "containment", failures, lane=status, seconds=seconds, laneFile=str(lane.path), entries=analysis["entries"], reachedArea=analysis["reachedArea"],
    manifestReachedArea=None if not isinstance(manifestLane, dict) else manifestLane.get("reachedArea"),
  )


def lookNumbers(scenePath, archivePath):
  """The difference of two renders of one view: (mean of every pixel's channels, the roundTripPercentile of each pixel's largest
  channel difference, the per-pixel largest differences)."""
  scene = numpy.asarray(viewSheets.openRender(scenePath), dtype=numpy.int64)
  archive = numpy.asarray(viewSheets.openRender(archivePath), dtype=numpy.int64)
  if scene.shape != archive.shape:
    raise ValueError(f"The renders of one pose differ in size: {scene.shape} and {archive.shape}")
  difference = numpy.abs(scene - archive)
  largest = difference.max(axis=-1)
  return float(difference.mean()), float(numpy.percentile(largest, buildTolerances.roundTripPercentile)), largest


def differenceImage(scenePath, largest):
  """The scene's render dimmed to grey, each pixel turned red by how far its archive render differs (full at differenceFullRed)."""
  grey = numpy.asarray(viewSheets.openRender(scenePath).convert("L"), dtype=numpy.float64)[..., None] * viewSheets.dimShare
  cover = numpy.clip(largest / differenceFullRed, 0, 1)[..., None]
  return Image.fromarray(numpy.round(grey * (1 - cover) + viewSheets.changeColor * cover).astype(numpy.uint8))


def looksCheck(views, unviewed):
  """Check 10: each view's renders, of the scene with what export leaves out hidden and of the archive, alike within the round-trip
  tolerances; views in the order compared, each with its numbers and files. unviewed are the failures that kept views from being
  compared (a tour that cannot be had, a pose that cannot be read)."""
  failures = list(unviewed)
  for view in views:
    if not view["passed"]:
      failures.append({
        "view": view["name"], "eye": view["pose"]["eye"],
        "message": (
          f"{view['name']} draws otherwise from the archive: mean difference {view['meanDifference']:.3f} (under {buildTolerances.roundTripMeanLevels:g} wanted),"
          f" {buildTolerances.roundTripPercentile}th percentile {view['percentileDifference']:g} (at most {buildTolerances.roundTripPercentileLevels:g} wanted)"
        ),
      })
  if not views and not unviewed:
    failures.append({"message": "No review camera and no tour eye: no view was compared"})
  return checked("looks", failures, views=views)


def comparedView(name, source, pose, scenePath, archivePath, differencePath):
  """One view's comparison, its difference image written at differencePath."""
  mean, percentile, largest = lookNumbers(scenePath, archivePath)
  differenceImage(scenePath, largest).save(differencePath, "PNG")
  passed = mean < buildTolerances.roundTripMeanLevels and percentile <= buildTolerances.roundTripPercentileLevels
  return {
    "name": name, "source": source, "pose": pose, "meanDifference": round(mean, 4), "percentileDifference": round(percentile, 3), "passed": passed,
    "scene": str(scenePath), "archive": str(archivePath), "difference": str(differencePath),
  }


def largestFirst(views):
  """The views that draw otherwise first, then those alike, each by its mean difference, largest first."""
  return sorted(views, key=lambda view: (view["passed"], -view["meanDifference"], -view["percentileDifference"]))


def writeComparisonSheet(views, outputPath):
  """The comparison sheet: a row per view, largest differences first and at most sheetRows of them: the scene, the archive, and the
  difference."""
  cells = []
  for view in views[:sheetRows]:
    verdict = "alike" if view["passed"] else "DIFFERS"
    cells += [
      (viewSheets.openRender(view["scene"]), f"{view['name']} ({view['source']}): the scene, what export leaves out hidden"),
      (viewSheets.openRender(view["archive"]), f"{view['name']}: the archive"),
      (viewSheets.openRender(view["difference"]), f"difference: mean {view['meanDifference']:.3f}, {buildTolerances.roundTripPercentile}th percentile {view['percentileDifference']:g}: {verdict}"),
    ]
  return viewSheets.writeGrid(cells, 3, outputPath) | {"rows": len(cells) // 3, "views": len(views)}


def serverViewLabels(zoneRegions, records, rows):
  """Each .wtr record's label for the server's view: the archive's .zon region it was written from, a zone line with the zone_points rows
  standing in its box; by its type and number without the .zon's regions."""
  if zoneRegions is None:
    return [f"{index} {serverMapFiles.waterRegionTypeNames.get(record['type'], record['type'])}" for index, record in enumerate(records)]
  labels = []
  for region, record in zip(zoneRegions, records):
    numbers = [
      point["number"] for point in ([] if rows is None else rows["zonePoints"])
      if record["type"] == serverMapFiles.waterRegionTypes[eqgFiles.zoneLinePrefix]
      and all(abs(value - center) <= abs(half) for value, center, half in zip((point["y"], point["x"]), record["position"], record["halfExtents"]))
    ]
    labels.append(region["name"] + (f": zone_points {', '.join(str(number) for number in numbers)}" if numbers else ""))
  return labels


def serverViewMarkers(rows, waysIn, zoneID):
  """The places the server's rows name, in zone plan axes: the safe point, each zone_points row's position and its landing back in the
  zone, and each way in from a neighbour's row."""
  if rows is None:
    return []
  values = rows["zoneUpdate"]["values"]
  markers = [{"at": [values["safe_y"], values["safe_x"]], "label": "safe point"}]
  markers += [{"at": [point["y"], point["x"]], "label": f"zone_points {point['number']}"} for point in rows["zonePoints"]]
  markers += [{"at": landing[:2], "label": f"landing of zone_points {point['number']}"} for point, landing in zoneLandingRows(rows, zoneID)]
  if waysIn is not None:
    markers += [{"at": [row["target_y"], row["target_x"]], "label": f"way in from {row['fromZone']} {row['fromNumber']}"} for row in waysIn["rows"]]
  return markers


def readOrWhy(read, data, what):
  """(what read gives of data, None) or (None, why not)."""
  if data is None:
    return None, f"no {what} lies in the export"
  try:
    return read(data), None
  except (OSError, KeyError, ValueError, struct.error, zlib.error, UnicodeDecodeError) as error:
    return None, f"{what} does not read: {error}"


def drawServerView(outputPath, files, inspection, manifest, short):
  """The server's view of the export from its files alone (serverMapDrawing.drawServerPlan): the .map's collision, the .wtr's boxes
  labelled with the archive's regions and the zone_points rows standing in them, the .nav's polygons with each island's reach as the
  manifest records it, and the places the rows name. Returns what was drawn and what of it could not be, with why, or why nothing could
  be."""
  content, mapWhy = readOrWhy(serverMapFiles.readMap, files["map"], "the .map")
  records, waterWhy = readOrWhy(serverMapFiles.readWater, files["water"], "the .wtr")
  if content is None or records is None:
    return {"drawn": False, "why": mapWhy or waterWhy}
  zoneRegions, regionsWhy = readOrWhy(lambda data: eqgFiles.parseZone(archiveEntries(data, "the exported archive")[f"{short}.zon"], f"{short}.zon")["regions"], files["archive"], "the archive")
  rows, rowsWhy = readOrWhy(lambda data: serverRows.readRows(data.decode("ascii"), f"{short}.sql"), files["rows"], f"{short}.sql")
  waysIn, waysInWhy = readOrWhy(lambda data: serverRows.readWaysIn(data.decode("ascii"), f"{short}_waysIn.sql"), files["waysIn"], f"{short}_waysIn.sql")
  notDrawn = {}
  if zoneRegions is not None and len(zoneRegions) != len(records):
    zoneRegions, regionsWhy = None, f"the archive's .zon holds {len(zoneRegions)} regions for the .wtr's {len(records)} records"
  if zoneRegions is None:
    notDrawn["regionNames"] = f"boxes labelled by .wtr type: {regionsWhy}"
  if rows is None:
    notDrawn["rowPlaces"] = rowsWhy
  if files["waysIn"] is not None and waysIn is None:
    notDrawn["waysIn"] = waysInWhy
  islandReach = None
  if inspection is not None:
    manifestIslands = (manifest.get("nav") or {}).get("islands")
    if not isinstance(manifestIslands, list) or [island["center"] for island in manifestIslands] != [island["center"] for island in inspection["islands"]]:
      notDrawn["islandReach"] = "the .nav's islands are not those the manifest records"
    elif any(island["playersReach"] is None for island in manifestIslands):
      notDrawn["islandReach"] = "the manifest records no players' reach for the islands"
    else:
      islandReach = [island["playersReach"] for island in manifestIslands]
  zoneID = None if rows is None else rows["zoneInsert"]["written"].get("zoneidnumber")
  drawn = serverMapDrawing.drawServerPlan(
    outputPath, content, records, serverViewLabels(zoneRegions, records, rows), inspection, serverViewMarkers(rows, waysIn, zoneID), islandReach,
  )
  return {"drawn": True, "outputPath": str(outputPath), "notDrawn": notDrawn} | drawn
