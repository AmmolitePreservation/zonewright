"""Large tool results: each written whole to a report file under the tooling root, and a compact view of it returned in its place."""

import collections
import datetime
import time

import pydantic_core

reportRetentionDays = 90
# A long list in a compact view keeps its count and this many of its first items.
listedItems = 3


def compactJSON(result):
  """A tool's result as the client receives it, without the indentation the MCP SDK gives the dicts it converts (which doubles a large
  result); images and text pass through, and a list's items stay separate content blocks, as the SDK makes them."""
  if isinstance(result, (list, tuple)):
    return [compactJSON(item) for item in result]
  if isinstance(result, dict):
    return pydantic_core.to_json(result, fallback=str).decode()
  return result


def writeReport(toolingRoot, tool, result):
  """The whole result as JSON in <tooling root>\\reports\\<tool>\\<time>.json, the tool's reports older than the retention removed first."""
  folder = toolingRoot / "reports" / tool
  folder.mkdir(parents=True, exist_ok=True)
  cutoff = time.time() - reportRetentionDays * 86400
  for old in folder.glob("*.json"):
    if old.stat().st_mtime < cutoff:
      old.unlink()
  path = folder / (datetime.datetime.now(datetime.UTC).strftime("%Y%m%d-%H%M%S-%f") + ".json")
  path.write_bytes(pydantic_core.to_json(result, fallback=str, indent=1))
  return path


def listed(items):
  return {"count": len(items), "first": items[:listedItems]}


def listedKeys(value, keys):
  return value | {key: listed(value[key]) for key in keys if isinstance(value.get(key), list)}


def longListsListed(value):
  """value with every list in it longer than listedItems listed."""
  if isinstance(value, list):
    return listed([longListsListed(item) for item in value[:listedItems]] + value[listedItems:]) if len(value) > listedItems else [longListsListed(item) for item in value]
  if isinstance(value, dict):
    return {key: longListsListed(item) for key, item in value.items()}
  return value


def withoutWalkDetail(value):
  """value with every walk in it (walkRoute's shape, wherever it lies) lacking the body groups' measures and each group's profile."""
  if isinstance(value, list):
    return [withoutWalkDetail(item) for item in value]
  if not isinstance(value, dict):
    return value
  if "passingGroups" in value and "groups" in value:
    groups = {name: {key: item for key, item in group.items() if key != "profile"} for name, group in value["groups"].items()}
    return {key: item for key, item in value.items() if key != "measures"} | {"groups": groups}
  return {key: withoutWalkDetail(item) for key, item in value.items()}


def compactStructure(result):
  """A structure's build (or a placed prefab's): its walks without detail, and its joints, sections, parts, and posts listed."""
  return listedKeys(withoutWalkDetail(result), ("joints", "sections", "parts", "posts"))


def compactCut(cut):
  """A cave's cut: its runs by name, and its junctions, floor strokes, and anchored lights listed."""
  return listedKeys(cut, ("junctions", "floorStrokes", "anchoredLights")) | {"runs": sorted(cut["runs"])}


def compactEditedCave(edited):
  return edited | {"cut": compactCut(edited["cut"])}


def compactPasses(result, touched=None):
  """A shaping pass tool's result without the mesh's whole pass list: the pass the call touched (named by touched), the active pass's
  name (None when none is), and how many passes there are; a facade's refit (removeShapingPass) as a cave edit's."""
  passes = {entry["name"]: entry for entry in result["passes"]}
  compact = {key: item for key, item in result.items() if key != "passes"} | {
    "activePass": next((name for name, entry in passes.items() if entry["active"]), None), "passCount": len(passes),
  }
  if touched is not None:
    compact["pass"] = passes[touched]
  if compact.get("refit") is not None:
    compact["refit"] = compactEditedCave(compact["refit"])
  return compact


def withoutLaneTerms(result):
  """result with its laneState, if it has one, lacking the lane's terms (playerReach.laneTerms: the constants with their sources, what
  the lane does not model, and its limits), which are the same on every call."""
  if "laneState" not in result:
    return result
  return result | {"laneState": {key: item for key, item in result["laneState"].items() if key not in ("constants", "notModeled", "limits")}}


def compactSection(section):
  """A section's report: each cave's floor samples and strokes listed, and its lane's state without the lane's terms."""
  return withoutLaneTerms(section | {"caves": [listedKeys(cave, ("floor", "strokes")) for cave in section["caves"]]})


def compactVerification(verified):
  """verifyExport's result: a passed check by name, a failed one with its failures."""
  return verified | {"checks": [
    {"check": check["check"], "passed": True} if check["passed"] else {"check": check["check"], "passed": False, "failures": check["details"]["failures"]}
    for check in verified["checks"]
  ]}


def groupedFindings(findings):
  """Findings by kind: how many, and the first with its long lists listed."""
  groups = collections.defaultdict(list)
  for finding in findings:
    groups[finding["finding"]].append(finding)
  return [{"finding": kind, "count": len(members), "first": longListsListed(members[0])} for kind, members in groups.items()]


def missedBySome(places, builtFor):
  """Places counted, with the names of those some group the zone is built for does not reach listed."""
  return {"count": len(places), "missed": listed([place["name"] for place in places if not set(builtFor) <= set(place["reachedBy"])])}


def compactContainment(containment):
  """The lane's report without its constants and file keys: each region's least reached share, and the plot entrances some group
  misses."""
  if not isinstance(containment, dict) or "plotEntrances" not in containment:
    return containment
  kept = {key: item for key, item in containment.items() if key not in ("constants", "laneFiles", "laneKeys")}
  return kept | {
    "regions": {region["name"]: min(region["reachedShare"].values()) for region in containment["regions"]},
    "plotEntrances": missedBySome(containment["plotEntrances"], containment["builtFor"]),
  }


def compactNav(nav):
  """The NPC nav's inspection: its probes counted by result with the names of those that fail, and its largest islands listed."""
  if nav is None:
    return None
  failing = [probe["name"] for probe in nav["probes"] if not probe["reached"]]
  return nav | {
    "probes": {"count": len(nav["probes"]), "reached": len(nav["probes"]) - len(failing), "failing": failing},
    "largestIslands": listed(nav["largestIslands"]),
  }


def compactExport(result):
  """checkExport's or exportZone's result: failures whole, findings by kind, the lane and nav summarized, long lists listed."""
  compact = result | {"findings": groupedFindings(result["findings"]), "containment": compactContainment(result["containment"])}
  compact |= {"excluded": [listedKeys(group, ("objects",)) for group in result["excluded"]]}
  compact = listedKeys(compact, ("regions", "structures"))
  serverFiles = result.get("serverFiles")
  if isinstance(serverFiles, dict) and serverFiles.get("built"):
    compact["serverFiles"] = serverFiles | {"nav": compactNav(serverFiles["nav"])}
  return compact
