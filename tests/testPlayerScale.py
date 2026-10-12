import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "server"))
import eqLooks
import eqRaces
import playerScale


def testEveryPlayableRaceAndGenderIsOneBodyInOneGroup():
  races = {race for name in playerScale.groupNames for race in playerScale.groupRaces[name]}
  assert races == eqLooks.playerRaces
  assert sorted((body.race, body.gender) for body in playerScale.bodies) == sorted((race, gender) for race in eqLooks.playerRaces for gender in (0, 1))
  # Each body's model is the one eqgame.exe registers for its race and gender.
  for body in playerScale.bodies:
    assert eqLooks.raceAndGender(body.code) == (body.race, body.gender), body.code
  members = [body.code for name in playerScale.groupNames for body in playerScale.groups[name].members]
  assert sorted(members) == sorted(body.code for body in playerScale.bodies)


def testGroupsAreJudgedByTheirLimitingBodies():
  small, medium, large = (playerScale.groups[name] for name in playerScale.groupNames)
  assert (small.tallest.code, small.widest.code, small.smallest.code) == ("DWM", "DWM", "GNF")
  assert (medium.tallest.code, medium.widest.code, medium.smallest.code) == ("HIF", "HUM", "ELF")
  assert (large.tallest.code, large.widest.code, large.smallest.code) == ("OGF", "OGM", "KEF")
  assert (small.playerHeight, medium.playerHeight, large.playerHeight) == (5.22, 8.07, 11.65)
  assert (small.playerWidth, medium.playerWidth, large.playerWidth) == (3.10, 3.61, 6.02)
  # Frogloks stand in their bind pose in the model data, arms out, so their width never sets a group's.
  assert max(body.width for body in medium.members) == 9.45 and medium.playerWidth < 9.45
  # The groups do not overlap: every small body is shorter than every medium one, every medium one than every large one.
  assert max(body.modelHeight for body in small.members) < min(body.modelHeight for body in medium.members)
  assert max(body.modelHeight for body in medium.members) < min(body.modelHeight for body in large.members)
  # A group's eye follows its tallest body's avatarHeight from the design eye of a body of the walked human's.
  assert medium.eyeHeight == playerScale.designEyeHeight
  assert small.eyeHeight == pytest.approx(5.5 * 2.5 / 3.75) and large.eyeHeight == pytest.approx(5.5 * 5.625 / 3.75)


def testBodyAvatarHeightsAreTheClientsOriginsForTheirSpawnHeights():
  for body in playerScale.bodies:
    scale = eqRaces.spawnScale(body.code, body.spawnHeight, False)
    assert body.avatarHeight == pytest.approx(eqRaces.defaultAvatarOffset * scale), body.code


def testBuiltForFallsBackFromRegionToZoneToEveryGroup():
  assert playerScale.builtForAt(None, None) == playerScale.groupNames
  assert playerScale.builtForAt(["large"], None) == ("large",)
  assert playerScale.builtForAt(["large"], ["small"]) == ("small",)
  assert playerScale.checkedGroupNames(["large", "small"]) == ("small", "large")
  for bad in ([], ["huge"], ["small", "small"], "small"):
    with pytest.raises(ValueError):
      playerScale.checkedGroupNames(bad)


def testJudgedFailsOnlyForABuiltForGroup():
  assert playerScale.judged(["small", "medium"], ("small", "medium"))["verdict"] == "finding"
  assert playerScale.judged(["small"], ("small", "medium")) == {
    "verdict": "fails", "passingGroups": ["small"], "failingGroups": ["medium", "large"], "builtFor": ["small", "medium"],
  }
  assert playerScale.judged(list(playerScale.groupNames), playerScale.groupNames)["verdict"] == "passes"


def testStrictestTakesTheLeastClimbAndTheMostClearance():
  strictest = playerScale.strictest(["small", "large"])
  assert strictest.stepHeight == min(playerScale.groups[name].stepHeight for name in ("small", "large"))
  assert strictest.playerHeight == 11.65 and strictest.playerWidth == 6.02
  assert playerScale.planGroup(playerScale.groupNames) == "large" and playerScale.viewGroup(playerScale.groupNames) == "medium"
  assert playerScale.viewGroup(("small",)) == "small" and playerScale.viewGroup(("small", "large")) == "large"
  assert playerScale.groupOfCode("DAF") == "medium" and playerScale.highestOrigin() == 5.625
