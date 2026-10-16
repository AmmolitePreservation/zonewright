import json
import os
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "server"))
import resultReports


def walk(groups):
  return {
    "verdict": "passes", "passingGroups": list(groups), "measures": {name: {"stepHeight": 3.0} for name in groups},
    "groups": {name: {"length": 10.0, "problems": [], "profile": [{"distance": 0.0}, {"distance": 10.0}]} for name in groups},
  }


def testCompactJSONDropsTheIndentationAndKeepsImagesAndTextApart():
  image = object()
  assert resultReports.compactJSON({"at": [1.0, 2.0], "name": "deck"}) == '{"at":[1.0,2.0],"name":"deck"}'
  assert resultReports.compactJSON([image, {"path": Path("C:/a")}]) == [image, '{"path":"C:\\\\a"}']
  assert resultReports.compactJSON("refused") == "refused"


def testWalksLoseTheirMeasuresAndProfilesWhereverTheyLie():
  built = {"structure": {"name": "flight"}, "walk": walk(["small", "large"]), "entrances": [{"name": "door", "walk": walk(["small"])}]}
  compact = resultReports.withoutWalkDetail(built)
  assert compact == {
    "structure": {"name": "flight"},
    "walk": {"verdict": "passes", "passingGroups": ["small", "large"], "groups": {"small": {"length": 10.0, "problems": []}, "large": {"length": 10.0, "problems": []}}},
    "entrances": [{"name": "door", "walk": {"verdict": "passes", "passingGroups": ["small"], "groups": {"small": {"length": 10.0, "problems": []}}}}],
  }
  assert built["walk"]["groups"]["small"]["profile"] == [{"distance": 0.0}, {"distance": 10.0}]


def testAStructuresLongListsComeBackCountedWithTheirFirstItems():
  built = {"joints": [{"at": index} for index in range(5)], "sections": [{"index": 0}], "legs": [1, 2, 3, 4, 5], "walk": walk(["small"])}
  compact = resultReports.compactStructure(built)
  assert compact["joints"] == {"count": 5, "first": [{"at": 0}, {"at": 1}, {"at": 2}]}
  assert compact["sections"] == {"count": 1, "first": [{"index": 0}]}
  assert compact["legs"] == [1, 2, 3, 4, 5]
  assert "measures" not in compact["walk"]


def testFindingsComeBackByKindWithTheFirstOfEachAndItsLongListsListed():
  findings = [
    {"finding": "NPC nav", "message": "plot 101"}, {"finding": "trap", "problems": [{"number": number} for number in range(1, 6)]},
    {"finding": "NPC nav", "message": "plot 102"},
  ]
  assert resultReports.groupedFindings(findings) == [
    {"finding": "NPC nav", "count": 2, "first": {"finding": "NPC nav", "message": "plot 101"}},
    {"finding": "trap", "count": 1, "first": {"finding": "trap", "problems": {"count": 5, "first": [{"number": 1}, {"number": 2}, {"number": 3}]}}},
  ]


def testTheNavAndLaneComeBackSummarized():
  probes = [{"name": "plot 101", "reached": True}, {"name": "plot 102", "reached": False}, {"name": "zone line 1", "reached": False}]
  nav = {"tiles": 4, "probes": probes, "largestIslands": [{"number": number} for number in range(1, 5)]}
  assert resultReports.compactNav(nav) == {
    "tiles": 4, "probes": {"count": 3, "reached": 1, "failing": ["plot 102", "zone line 1"]},
    "largestIslands": {"count": 4, "first": [{"number": 1}, {"number": 2}, {"number": 3}]},
  }
  containment = {
    "lane": "computed", "builtFor": ["small", "large"], "constants": {"small": {}}, "laneFiles": ["a"], "laneKeys": ["b"],
    "regions": [{"name": "bay", "reachedShare": {"small": 0.9, "large": 0.8}}],
    "plotEntrances": [{"name": "101", "reachedBy": ["small", "large"]}, {"name": "102", "reachedBy": ["large"]}],
  }
  assert resultReports.compactContainment(containment) == {
    "lane": "computed", "builtFor": ["small", "large"], "regions": {"bay": 0.8}, "plotEntrances": {"count": 2, "missed": {"count": 1, "first": ["102"]}},
  }
  assert resultReports.compactContainment("not checked: no safe point") == "not checked: no safe point"


def testAPassToolKeepsThePassItTouchedTheActivePassAndTheCountInline():
  passes = [{"name": f"pass{index}", "strength": 1.0, "muted": index == 7, "active": index == 30} for index in range(50)]
  changed = {"object": "ground", "passes": passes}
  assert resultReports.compactPasses(changed, "pass7") == {"object": "ground", "pass": passes[7], "activePass": "pass30", "passCount": 50}
  assert len(changed["passes"]) == 50
  removed = {"object": "ground", "removed": "pass30", "passes": [entry for entry in passes if entry["name"] != "pass30"], "refit": None}
  assert resultReports.compactPasses(removed) == {"object": "ground", "removed": "pass30", "refit": None, "activePass": None, "passCount": 49}
  refit = {"restored": {"faces": 12}, "cut": {"runs": {"main": {"floor": [0] * 9}, "side": {}}, "junctions": [{"at": index} for index in range(4)], "floorStrokes": [], "anchoredLights": []}}
  facade = {"object": "ground", "removed": "facade mouth", "passes": passes[:2], "refit": refit, "remappedFaces": 40}
  assert resultReports.compactPasses(facade)["refit"] == {
    "restored": {"faces": 12},
    "cut": {"runs": ["main", "side"], "junctions": {"count": 4, "first": [{"at": 0}, {"at": 1}, {"at": 2}]}, "floorStrokes": {"count": 0, "first": []}, "anchoredLights": {"count": 0, "first": []}},
  }
  with pytest.raises(KeyError):
    resultReports.compactPasses(changed, "pass50")


def laneTerms():
  return {"constants": {"stepHeight": {"value": 3.0, "source": "measured"}}, "notModeled": ["jumps"], "limits": ["no regions"]}


def testALanesStateComesBackWithoutTheLanesTerms():
  viewed = {"width": 640, "laneState": {"lane": "reused", "seconds": 0.1, "bodyGroup": "medium", "laneFile": "C:/lane.npz"} | laneTerms()}
  assert resultReports.withoutLaneTerms(viewed) == {"width": 640, "laneState": {"lane": "reused", "seconds": 0.1, "bodyGroup": "medium", "laneFile": "C:/lane.npz"}}
  assert viewed["laneState"]["notModeled"] == ["jumps"]
  assert resultReports.withoutLaneTerms({"width": 640}) == {"width": 640}
  section = {
    "caves": [{"cave": "deep", "floor": [[0, 1]] * 5, "strokes": [{"name": "way"}]}],
    "laneState": {"lane": "computed", "seconds": 2.5, "bodyGroup": "large", "laneCut": "C:/cut.json", "counts": {"bars": 4}} | laneTerms(),
  }
  assert resultReports.compactSection(section) == {
    "caves": [{"cave": "deep", "floor": {"count": 5, "first": [[0, 1]] * 3}, "strokes": {"count": 1, "first": [{"name": "way"}]}}],
    "laneState": {"lane": "computed", "seconds": 2.5, "bodyGroup": "large", "laneCut": "C:/cut.json", "counts": {"bars": 4}},
  }


def testAReportHoldsTheWholeResultAndOldReportsAreRemoved(tmp_path):
  folder = tmp_path / "reports" / "walkRoute"
  folder.mkdir(parents=True)
  old, recent = folder / "old.json", folder / "recent.json"
  for path in (old, recent):
    path.write_text("{}", encoding="utf-8")
  aged = time.time() - (resultReports.reportRetentionDays + 1) * 86400
  os.utime(old, (aged, aged))
  path = resultReports.writeReport(tmp_path, "walkRoute", {"verdict": "passes", "at": Path("C:/b")})
  assert path.parent == folder and json.loads(path.read_text(encoding="utf-8")) == {"verdict": "passes", "at": "C:\\b"}
  assert sorted(item.name for item in folder.iterdir()) == sorted([path.name, "recent.json"])
