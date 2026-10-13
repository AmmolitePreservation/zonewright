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


def testGroupsAreJudgedByTheirSampleBodies():
  small, medium, large = (playerScale.groups[name] for name in playerScale.groupNames)
  roles = lambda group: tuple(getattr(group, role).code for role in ("tallest", "shortest", "widest", "thinnest"))
  assert roles(small) == ("DWM", "GNF", "DWM", "GNF")
  assert roles(medium) == ("DKM", "ELF", "HUM", "ELF")
  assert roles(large) == ("OGM", "KEF", "OGM", "KEF")
  # Clearance is the most headroom and the widest passage a walked body of the group needed in the measurement zone: the drakkin
  # male, the medium group's tallest, needs both, a wider passage than the group's widest body.
  assert (small.playerHeight, medium.playerHeight, large.playerHeight) == (5.5, 7.75, 10.5)
  assert (small.playerWidth, medium.playerWidth, large.playerWidth) == (2.5, 4.0, 5.25)
  # Frogloks stand in their bind pose in the model data, arms out, so their width never sets a group's.
  assert max(body.width for body in medium.members) == 9.45 and medium.playerWidth < 9.45
  # The groups do not overlap: every small body is shorter than every medium one, every medium one than every large one.
  assert max(body.modelHeight for body in small.members) < min(body.modelHeight for body in medium.members)
  assert max(body.modelHeight for body in medium.members) < min(body.modelHeight for body in large.members)
  # A group's eye follows its tallest body's avatarHeight from the design eye of a body of the walked human's.
  assert medium.eyeHeight == pytest.approx(5.5 * 4.0625 / 3.75)
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
  assert strictest.playerHeight == 10.5 and strictest.playerWidth == 5.25
  assert playerScale.planGroup(playerScale.groupNames) == "large" and playerScale.viewGroup(playerScale.groupNames) == "medium"
  assert playerScale.viewGroup(("small",)) == "small" and playerScale.viewGroup(("small", "large")) == "large"
  assert playerScale.groupOfCode("DAF") == "medium" and playerScale.highestOrigin() == 5.625


def testClimbingIsTheStrictestWalkedBodysOfEachGroup():
  small, medium, large = (playerScale.groups[name] for name in playerScale.groupNames)
  # The wood elf female's step is the one the risers bracket; the dwarf male and gnome female, refused every riser, climb by its ratio.
  assert (small.stepHeight, medium.stepHeight, large.stepHeight) == (pytest.approx(1.875 * 4.95 / 3.125), 4.95, 5.3)
  assert playerScale.stepOf(playerScale.walkedBodies["DWM"]) == pytest.approx(2.5 * 4.95 / 3.125)
  for code in ("DWM", "GNF"):
    assert playerScale.stepOf(playerScale.walkedBodies[code]) < playerScale.walkedBodies[code].stepRefused == 4.5
  assert small.sources["stepHeight"].startswith("derived: the gnome female's") and medium.sources["stepHeight"].startswith("measured: ")
  assert "the course's highest riser, so the limit lies above it" in large.sources["stepHeight"]
  # Every walked body climbed the course's steepest ramp, so each group's limit is that ramp's, a lower bound.
  assert [round(group.steepestWalkableDegrees, 6) for group in (small, medium, large)] == [80, 80, 80]
  for group in (small, medium, large):
    assert "so the value is a lower bound and the limit lies steeper" in group.sources["walkableNormalZ"], group.name
  assert (small.minimumSwimDepth, medium.minimumSwimDepth, large.minimumSwimDepth) == (2.17, 3.45, 4.68)
  for group in (small, medium, large):
    for measure in ("playerHeight", "playerWidth", "stepHeight", "walkableNormalZ", "minimumSwimDepth"):
      source = group.sources[measure]
      assert "2026-10-12" in source and "190 to 230 fps" in source and "unmeasured" not in source, (group.name, measure)


def testEveryGroupWalksExactlyItsSample():
  walkedCodes = {name: [body.code for body in playerScale.walkedOf(name)] for name in playerScale.groupNames}
  assert walkedCodes == {"small": ["DWM", "GNF"], "medium": ["DKM", "ELF", "HUM"], "large": ["OGM", "KEF"]}
  assert sorted(code for codes in walkedCodes.values() for code in codes) == sorted(playerScale.walkedBodies)
  for name, codes in walkedCodes.items():
    assert {playerScale.groupOfCode(code) for code in codes} == {name}


def testASampleBodyThatHoldsNoExtremeIsRefused():
  medium = playerScale.groups["medium"]
  standing = [body for body in medium.members if body.code not in playerScale.bindPoseCodes]
  # The high elf female is the medium group's tallest model, but not its tallest by avatarHeight, the height walks follow.
  assert max(standing, key=lambda body: body.modelHeight).code == "HIF"
  roles = playerScale.sampleRoles["medium"] | {"tallest": "HIF"}
  with pytest.raises(ValueError, match="tallest sample body HIF has avatarHeight 3.75, not the group's 4.0625"):
    playerScale.sampleOf("medium", roles, standing)
  with pytest.raises(ValueError, match="is no standing body of the group"):
    playerScale.sampleOf("medium", playerScale.sampleRoles["medium"] | {"widest": "FRM"}, standing)


def testAWalkedBodyMissingAMeasureIsRefused():
  row = list(playerScale.walkedBodyRows[0])
  row[7] = None
  with pytest.raises(ValueError, match="has no ceilingPassed"):
    playerScale.WalkedBody(*row)
  row = list(playerScale.walkedBodyRows[0])
  row[2] = None
  with pytest.raises(ValueError, match="neither a riser climbed nor one refused"):
    playerScale.WalkedBody(*row)
