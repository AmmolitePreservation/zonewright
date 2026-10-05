"""What the player's lane is computed from, collected in the open scene: the collision the zone ships (bridgeExport.collisionTriangles),
the swim boxes and zone lines as they stand with each line's target, every entry stored and derived (bridgeEntries) with the footing
under each place players arrive, the regions with their access, the underworld, and the pools and rivers whose swimming is undecided.
The lane itself is computed by the server (playerReach), never here. Runs under Blender's Python."""
import json

import bpy
import numpy

import bridgeAuthoring
import bridgeBoundaries
import bridgeCommands
import bridgeEntries
import bridgeExport
import bridgeMeshAccess
import bridgeSwim

seedKinds = ("safePoint", "zoneIn", "landing")


def footingRecord(ground, point):
  """The footing found under a point as standAt finds it: its point and the z of its face's normal, or None."""
  footing = ground.footing(list(point))
  if footing is None:
    return None
  return {"point": [float(value) for value in footing.point], "normalZ": abs(float(footing.normal.z))}


def zoneLineRecords(shortName):
  """The zone's own zone lines as boxes, each with its target and, for one leading back into the zone at a whole point, that point."""
  errors = bridgeBoundaries.zoneLineErrors()
  if errors:
    raise ValueError("Zone lines no lane can place: " + "; ".join(error["message"] for error in errors))
  records = []
  for line in bridgeBoundaries.zoneLineObjects():
    if not bridgeBoundaries.isAuthored(line):
      continue
    center, halfExtents = bridgeBoundaries.boxBounds(line)
    target = bridgeBoundaries.readSpec(line, bridgeMeshAccess.zoneLineProperty)
    landing = None
    if target is not None and shortName is not None and target["zone"] == shortName and all(target[axis] != bridgeBoundaries.keep for axis in bridgeBoundaries.targetCoordinates):
      landing = [float(target[axis]) for axis in bridgeBoundaries.targetCoordinates]
    records.append({"name": line.name, "center": center, "halfExtents": halfExtents, "target": target, "landing": landing})
  return records


def laneInput(outputPath):
  """Write the lane's input to outputPath (.npz): the collision triangles (float32 [n, 3, 3], zone axes), each undecided body's triangles,
  and a description of the rest as JSON."""
  bpy.context.view_layer.update()
  collision = bridgeExport.collisionTriangles()
  ground = bridgeEntries.ShippedGround(collision)
  zone = bridgeCommands.readZoneProperties(bpy.context.scene)
  listed = bridgeEntries.listEntries(ground)
  entries = []
  for entry in listed["entries"]:
    record = dict(entry)
    if entry["kind"] in seedKinds:
      record["seedFooting"] = footingRecord(ground, entry["at"])
    entries.append(record)
  bodies = bridgeSwim.swimDecisions()["undecided"]
  arrays = {"collision": collision["positions"][collision["triangles"]].astype(numpy.float32)}
  for index, body in enumerate(bodies):
    positions, triangles = bridgeMeshAccess.worldTriangles([body])
    arrays[f"undecided{index}"] = positions[triangles].astype(numpy.float32)
  description = {
    "swimBoxes": bridgeSwim.swimRegions(),
    "zoneLines": zoneLineRecords(zone.get("shortName")),
    "entries": entries,
    "notFollowed": listed["notFollowed"],
    "regions": bridgeAuthoring.getRegions()["regions"],
    "underworld": zone.get("underworld"),
    "undecidedBodies": [body.name for body in bodies],
  }
  arrays["description"] = numpy.array(json.dumps(description))
  numpy.savez(outputPath, **arrays)
  return {"outputPath": outputPath, "triangles": len(arrays["collision"])}


commands = {
  "laneInput": (laneInput, False),
}
