"""The measurement zone's layout (design #10, RoF2 player measurements): every feature a walker tries, in zone axes (Blender, the
.zon), each lane starting on one start line and facing +Y. buildMeasurementZone.py builds it with zonewright's tools and writes
layoutJSON beside the export, which the claudelink walk scripts read; they convert to the server's axes with the fields given here."""
import json
import math

shortName = "arttest"
zoneID = 996
longName = "Art Testing Domain"
startLineY = 0.0
startBackoff = 4.0
# Zone heading 0 faces +Y; EQ heading 128 faces the server's +x, the same direction through the axis swap (eqAxes).
walkHeadingDegrees = 0.0
walkEQHeading = 128.0
floorZ = 0.0
featureStartY = 12.0
laneSpacing = 10.0
laneWidth = 8.0
wallHeight = 20.0
extent = {"minimum": [-12.0, -30.0], "maximum": [514.0, 252.0]}
safePoint = [200.0, -20.0, 0.0, 0.0]
underworld = -60.0

riserValues = [round(4.5 + 0.05 * index, 2) for index in range(17)]
riserDepth = 20.0
# Past the 80 degrees every body climbed with the frame limiter suspended, up to a face nearly sheer.
rampDegrees = [70 + index for index in range(20)]
rampHeight = 16.0
rampTopLength = 10.0
poolCenterX = 415.0
poolWidth = 30.0
poolStartY = featureStartY
poolLength = 100.0
poolDeepest = 10.0
# The water stands half a unit under the floor, so a flood stays in its basin; depths are the water's over the bed.
poolSurfaceZ = floorZ - 0.5
poolLabelDepths = [float(depth) for depth in range(1, 10)]
corridorSectionLength = 5.0
ceilingCenterX = 450.0
ceilingInnerWidth = 16.0
ceilingValues = [round(14.0 - 0.25 * index, 2) for index in range(45)]
openingCenterX = 490.0
openingOuterWidth = 24.0
openingValues = [round(10.0 - 0.25 * index, 2) for index in range(39)]


def serverPoint(point):
  """A zone point in the server's axes (eqAxes.serverFromZone: zone y, zone x, z), which claudelink's teleport and state use."""
  return [point[1], point[0], point[2]]


def lane(feature, label, x, value, walkTo, extra=None):
  start = [x, startLineY - startBackoff, floorZ]
  return {
    "feature": feature, "label": label, "value": value, "start": start, "startServer": serverPoint(start),
    "walkDistance": walkTo - start[1], "headingDegrees": walkHeadingDegrees, "eqHeading": walkEQHeading,
  } | (extra or {})


def risers():
  return [
    lane("riser", f"riser {value:.2f}", laneSpacing * index, value, featureStartY + riserDepth / 2, {
      "block": {"minimum": [laneSpacing * index - laneWidth / 2, featureStartY, floorZ], "maximum": [laneSpacing * index + laneWidth / 2, featureStartY + riserDepth, floorZ + value]},
    })
    for index, value in enumerate(riserValues)
  ]


def rampRun(degrees):
  return rampHeight / math.tan(math.radians(degrees))


def ramps():
  first = laneSpacing * len(riserValues) + laneSpacing
  found = []
  for index, degrees in enumerate(rampDegrees):
    x = first + laneSpacing * index
    run = rampRun(degrees)
    found.append(lane("ramp", f"ramp {degrees} degrees", x, degrees, featureStartY + run + rampTopLength / 2, {
      "foot": [x, featureStartY, floorZ], "head": [x, featureStartY + run, floorZ + rampHeight], "run": run, "height": rampHeight,
      "topEnd": featureStartY + run + rampTopLength, "halfWidth": laneWidth / 2,
    }))
  return found


def poolY(depth):
  """Where the water over the pool's bed is a depth deep."""
  return poolStartY + (depth + floorZ - poolSurfaceZ) * poolLength / poolDeepest


def pool():
  """Its bed falls evenly from the floor at the start of the pool to poolDeepest under it at its far end; the water's top stands at
  poolSurfaceZ, so the water over the bed at y is (y - start) * depthPerUnit - (floor - surface) deep."""
  return lane("pool", "pool, bed sloping 0 to 10 down", poolCenterX, None, poolStartY + poolLength - 2.0, {
    "bedFrom": [poolCenterX, poolStartY, floorZ], "bedTo": [poolCenterX, poolStartY + poolLength, floorZ - poolDeepest], "width": poolWidth,
    "surfaceZ": poolSurfaceZ, "depthPerUnit": poolDeepest / poolLength, "waterStartsY": poolY(0.0),
    "labels": [{"depth": depth, "y": poolY(depth)} for depth in poolLabelDepths],
  })


def sections(values, firstY):
  return [{"value": value, "from": firstY + corridorSectionLength * index, "to": firstY + corridorSectionLength * (index + 1)} for index, value in enumerate(values)]


def ceilings():
  return lane("ceiling", "ceilings stepping down 14 to 3", ceilingCenterX, None, featureStartY + corridorSectionLength * len(ceilingValues), {
    "innerWidth": ceilingInnerWidth, "sections": sections(ceilingValues, featureStartY),
  })


def openings():
  return lane("opening", "openings narrowing 10 to 0.5", openingCenterX, None, featureStartY + corridorSectionLength * len(openingValues), {
    "outerWidth": openingOuterWidth, "sections": sections(openingValues, featureStartY),
  })


def layout():
  return {
    "shortName": shortName, "zoneID": zoneID, "frame": "zone axes (x, y, z up); startServer is the server's (zone y, zone x, z)",
    "startLineY": startLineY, "floorZ": floorZ, "safePoint": safePoint, "underworld": underworld, "extent": extent,
    "lanes": risers() + ramps() + [pool(), ceilings(), openings()],
  }


def layoutJSON():
  return json.dumps(layout(), indent=1)
