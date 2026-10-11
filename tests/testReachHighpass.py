import sys
from pathlib import Path

import numpy
import pytest

from serverReference import referenceBytes

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "server"))
import eqAxes
import laneDrawing
import playerReach
import serverMapFiles
import serverNav
from playerScale import stepHeight

pytestmark = pytest.mark.clientData("serverMaps")
# Peridot's zone row gives the safe point in server axes.
highpassSafePoint = tuple(eqAxes.zoneFromServer([-219.0, -148.0, -24.0]).tolist())
# The highest walkable floor on each backdrop mountain flanking the pass (the two largest NPC islands of Peridot's nav, west and east
# of it), with its height, the reached floor nearest it, and the rise between two side by side columns met walking straight from there
# to it: each is ringed by a face far higher than a player's step.
backdropTops = {
  "west": {"at": [-678.0, -255.0], "height": 120.25, "nearestReached": [-653.0, -224.0, -13.5], "rise": 28.25},
  "east": {"at": [76.0, 462.0], "height": 150.5, "nearestReached": [107.0, 448.0, -15.0], "rise": 33.75},
}


def noProgress(done, of, message):
  pass


def straightRises(nodes, start, end):
  """The rise between the highest floors of each two successive columns holding floor along a straight line, one column apart."""
  steps = int(numpy.hypot(end[0] - start[0], end[1] - start[1])) + 1
  heights = []
  for share in numpy.linspace(0, 1, steps + 1):
    node = nodes.floorNodeAt([start[0] + share * (end[0] - start[0]), start[1] + share * (end[1] - start[1])])
    if node is not None:
      heights.append(float(nodes.height[node]))
  return numpy.diff(heights)


def testHighpassHoldsZoneLinesAreReachedFromItsSafePoint(recastToolingRoot, tmp_path):
  mapBytes, waterBytes = referenceBytes("base/highpasshold.map"), referenceBytes("water/highpasshold.wtr")
  lane, description, status, _ = playerReach.laneFromServerFiles(mapBytes, waterBytes, [{"name": "safe point", "point": highpassSafePoint, "kind": "safePoint"}], recastToolingRoot, noProgress)
  analysis = playerReach.analyse(lane, description)
  assert status == "computed"
  # Every one of the five zone lines in Peridot's .wtr is reached from its safe point, and both pools are swum.
  assert [(line["name"], line["reached"]) for line in analysis["zoneLines"]] == [(f".wtr record {index} (ZoneLine)", True) for index in range(2, 7)]
  assert [(box["name"], box["reached"]) for box in analysis["swimBoxes"]] == [(".wtr record 0 (Water)", True), (".wtr record 1 (Water)", True)]
  nodes = analysis["nodes"]
  # And the lane does not flood everything: the backdrop mountains' tops stay out of reach.
  reached = numpy.flatnonzero(nodes.reached & ~nodes.wet)
  reachedX, reachedY = nodes.plan(reached)
  for name, top in backdropTops.items():
    node = nodes.floorNodeAt(top["at"])
    assert (playerReach.labelNames[nodes.label[node]], bool(nodes.walkable[node]), float(nodes.height[node])) == ("unreached", True, top["height"]), name
    nearest = reached[numpy.argmin(numpy.hypot(reachedX - top["at"][0], reachedY - top["at"][1]))]
    planX, planY = nodes.plan(numpy.array([nearest]))
    assert [float(planX[0]), float(planY[0]), float(nodes.height[nearest])] == top["nearestReached"], name
    rises = straightRises(nodes, top["nearestReached"], top["at"])
    assert float(rises.max()) == top["rise"] and top["rise"] > stepHeight, name

  water = serverMapFiles.readWater(waterBytes)
  targets = [{"name": f"the zone line of .wtr record {index}", "point": record["position"], "onFloor": False} for index, record in enumerate(water) if record["type"] == 3]
  inspection = serverNav.inspectNav(referenceBytes("nav/highpasshold.nav"), highpassSafePoint, targets, recastToolingRoot, noProgress)
  drawn = laneDrawing.drawLaneBesideNav(
    tmp_path / "highpassLaneBesideNav.png", serverMapFiles.mapCollision(mapBytes), laneDrawing.LanePlan(analysis, description, None, True), inspection,
    ("Players' lane from Peridot's safe point (its .map and .wtr)", "Peridot's highpasshold.nav: the NPC main piece and its islands"),
  )
  # Peridot's collision is closed all round, so no fall reaches nothing; what the lane finds are pockets entered by sliding or dropping
  # that no move leaves, each ringed and named in the legend with the marks the plan shows (no leak or catch edge, no plot, no region
  # from server files, every zone line reached) and the plan's axes.
  traps = len(analysis["problems"])
  assert [problem["kind"] for problem in analysis["problems"]] == ["trap"] * traps
  marks = [
    "walk", "swim (a floor in a swim box)", "slide (too steep to stand)", "squeeze (only a smaller player)", "taken by a zone line", "trap (no way back)",
    "walkable, never reached", "reached under a higher level", "only a smaller player under it", f"ledge: floors side by side over a step ({stepHeight:g}) apart",
    "zone line reached", "seed: where players arrive",
  ]
  assert drawn["legends"] == [
    ["relief: the highest reached floor, else the highest floor", "no floor at all"] + marks + [f"1-{traps} trap", "plan: +X (north) up, +Y left"],
    ["main piece: 3,677 polygons", "640 islands, 297 at snap risk", "Disabled and zone line: 53 polygons"],
  ]
