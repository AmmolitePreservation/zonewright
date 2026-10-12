"""The bodies players play, read from the client's data, in three groups (small, medium, large), and each group's measures that geometry
is judged by, each with its source in the group's `sources`: "measured: how, where, when", "unmeasured: why the value", or "derived: from
what". A group's clearance (playerHeight, playerWidth, eyeHeight) is set by its tallest and widest body, its climbing (stepHeight,
walkableNormalZ, minimumSwimDepth) by its smallest. Every tool that judges where a player stands, climbs, swims, or looks takes them from
here, per group, and judges a spot by the groups its region (else its zone) is built for (builtFor): a built-for group that cannot pass is
a failure, any other group a finding."""
import math
from dataclasses import dataclass

groupNames = ("small", "medium", "large")
groupRaces = {"small": (8, 11, 12), "medium": (1, 3, 4, 5, 6, 7, 128, 330, 522), "large": (2, 9, 10, 130)}
raceNames = {
  1: "human", 2: "barbarian", 3: "erudite", 4: "wood elf", 5: "high elf", 6: "dark elf", 7: "half elf", 8: "dwarf", 9: "troll", 10: "ogre",
  11: "halfling", 12: "gnome", 128: "iksar", 130: "vah shir", 330: "froglok", 522: "drakkin",
}
genderNames = ("male", "female")

# (race, gender, model code, spawn height, model height, width across, depth front to back, avatarHeight), standing in the client's
# stand pose (P01, or the EQG STND) at frame 0 and drawn at the classic scale, in EQ units.
bodyRows = (
  (1, 0, "HUM", 6, 7.82, 3.61, 2.01, 3.75),
  (1, 1, "HUF", 6, 7.87, 3.02, 1.67, 3.75),
  (2, 0, "BAM", 7, 9.13, 4.28, 2.56, 4.375),
  (2, 1, "BAF", 7, 9.05, 4.34, 2.68, 4.375),
  (3, 0, "ERM", 6, 8.02, 3.61, 2.01, 3.75),
  (3, 1, "ERF", 6, 7.95, 3.01, 1.56, 3.75),
  (4, 0, "ELM", 5, 6.56, 2.54, 1.36, 3.125),
  (4, 1, "ELF", 5, 6.50, 2.17, 1.68, 3.125),
  (5, 0, "HIM", 6, 7.86, 3.04, 1.64, 3.75),
  (5, 1, "HIF", 6, 8.07, 2.60, 2.02, 3.75),
  (6, 0, "DAM", 5, 6.55, 2.54, 1.36, 3.125),
  (6, 1, "DAF", 5, 6.50, 2.17, 1.68, 3.125),
  (7, 0, "HAM", 5.5, 7.20, 2.79, 1.50, 3.4375),
  (7, 1, "HAF", 5.5, 7.14, 2.39, 1.85, 3.4375),
  (8, 0, "DWM", 4, 5.22, 3.10, 1.74, 2.5),
  (8, 1, "DWF", 4, 5.19, 2.33, 1.54, 2.5),
  (9, 0, "TRM", 8, 10.39, 4.92, 3.73, 5.0),
  (9, 1, "TRF", 8, 10.55, 4.33, 3.34, 5.0),
  (10, 0, "OGM", 9, 11.40, 6.02, 4.63, 5.625),
  (10, 1, "OGF", 9, 11.65, 5.16, 3.36, 5.625),
  (11, 0, "HOM", 3.5, 4.63, 2.21, 1.50, 2.1875),
  (11, 1, "HOF", 3.5, 4.58, 1.90, 2.04, 2.1875),
  (12, 0, "GNM", 3, 3.85, 1.93, 1.37, 1.875),
  (12, 1, "GNF", 3, 3.84, 1.62, 1.16, 1.875),
  (128, 0, "IKM", 6, 7.26, 3.42, 4.89, 3.75),
  (128, 1, "IKF", 6, 6.76, 3.52, 4.98, 3.75),
  (130, 0, "KEM", 7, 8.90, 4.27, 4.89, 4.375),
  (130, 1, "KEF", 7, 8.55, 4.12, 5.10, 4.375),
  (330, 0, "FRM", 5, 5.92, 9.45, 2.81, 3.125),
  (330, 1, "FRF", 5, 5.98, 9.45, 2.70, 3.125),
  (522, 0, "DKM", 6, 7.81, 2.72, 1.81, 4.0625),
  (522, 1, "DKF", 5.7, 7.47, 2.54, 1.58, 3.859375),
)
# No archive the client links at startup holds a froglok's stand (P01, nor a borrowed one), so it stands in its bind pose, arms held out:
# its size is no standing size, and it never limits a group.
bindPoseCodes = frozenset(("FRM", "FRF"))
bodiesSource = (
  "derived: each playable race and gender (eqLooks.playerRaces) from the client's data: the spawn height is the server's"
  " GetRaceGenderDefaultHeight (EQEmu ae4bd2833 common/races.cpp:1489, which zone/mob.cpp:3661 and npc.cpp:164 use; a player's own spawn"
  " goes out with size 0, zone/client.cpp:105, and claudelink's dumps hold height 6 for the walked human male and 5 for a dark elf female);"
  " the model is the one eqgame.exe registers for the race and gender (eqRaces.raceRegistrations), built by eqModels from the client's"
  " global archives in its stand pose at frame 0, and drawn at the scale eqRaces.spawnScale gives that height outside a NewEngineZone zone"
  " (EQEmu sends NewEngineZone false for every zone): its height and width are the posed model's extent up and side to side (arms, hair,"
  " and tails included), avatarHeight its origin over the ground (moddat.ini's ROffset times that scale); tests/testPlayerBodies.py reads"
  " them again from the client"
)

# The RoF2 walk of 2026-10-04: one human male, a medium body.
rof2Walk = "the RoF2 client on 2026-10-04, a human male of size 6 (avatarHeight 3.75) walking straight at each site without jumping"
walkedAvatarHeight = 3.75
walkedStepHeight = 4.5
walkedWalkableNormalZ = 0.31
walkedSwimDepth = 3.75
designEyeHeight = 5.5
swimEyeAboveSurfaceValue = 1.0
walkedStepSource = (
  f"{rof2Walk}: of 20 risers in Freeport West, Freeport Sewers, the guild hall, and High Pass Hold he climbed every one from 0.50 to 4.50"
  " (4.50 under a cornice) and was stopped by every one from 5.22 to 7.82, so the limit lies in (4.50, 5.22], 1.20 to 1.39 times his"
  " avatarHeight; this is the highest climbed"
)
walkedSlopeSource = (
  f"{rof2Walk}: he climbed every one of 20 faces from 24.4 to 71.9 degrees in Freeport West, Freeport Sewers, Freeport Academy, High Pass"
  " Hold, and Nektulos and was stopped by none, so the walkable limit is at least the steepest, 71.9 degrees (normal z 0.31), and may lie"
  " steeper (one walk hints at 74 to 77, unmeasured); his speed held to 61 degrees and fell to about 62% at 72"
)
walkedSwimSource = (
  f"{rof2Walk}: walking into the Freeport Sewers' water on two paths, he walked the bed where the swim box's top stood 3.74 over it and"
  " swam from where it stood 4.59, so swimming starts at a depth in (3.74, 4.59] (in High Pass Hold he swam only over water 10 or more"
  " deep, which does not narrow it); swimming, his feet floated 3.76 to 3.93 under the box's top. The value is his avatarHeight, 3.75, at"
  " the shallow end of the bracket, so the lane errs toward swimming; that the client's threshold follows avatarHeight is a guess the walk"
  " does not test"
)


@dataclass(frozen=True)
class Body:
  race: int
  gender: int
  code: str
  spawnHeight: float
  modelHeight: float
  width: float
  depth: float
  avatarHeight: float

  @property
  def name(self):
    return f"{raceNames[self.race]} {genderNames[self.gender]}"

  def described(self):
    return {
      "race": self.race, "gender": self.gender, "name": self.name, "model": self.code, "spawnHeight": self.spawnHeight, "modelHeight": self.modelHeight,
      "width": self.width, "depth": self.depth, "avatarHeight": self.avatarHeight, "standing": self.code not in bindPoseCodes,
    }


bodies = tuple(Body(*row) for row in bodyRows)


@dataclass(frozen=True)
class BodyGroup:
  name: str
  members: tuple
  tallest: Body
  widest: Body
  smallest: Body
  playerHeight: float
  playerWidth: float
  stepHeight: float
  walkableNormalZ: float
  eyeHeight: float
  swimEyeAboveSurface: float
  minimumSwimDepth: float
  sources: dict

  @property
  def steepestWalkableDegrees(self):
    return math.degrees(math.acos(self.walkableNormalZ))

  def measure(self, measureName):
    return {"value": getattr(self, measureName), "source": self.sources[measureName]}

  def described(self):
    return {
      "group": self.name, "races": [raceNames[race] for race in groupRaces[self.name]],
      "limitingBodies": {"tallest": self.tallest.name, "widest": self.widest.name, "smallest": self.smallest.name},
    } | {name: self.measure(name) for name in measureNames}


measureNames = ("playerHeight", "playerWidth", "stepHeight", "walkableNormalZ", "steepestWalkableDegrees", "eyeHeight", "swimEyeAboveSurface", "minimumSwimDepth")


def bodyGroup(name):
  members = tuple(body for body in bodies if body.race in groupRaces[name])
  standing = [body for body in members if body.code not in bindPoseCodes]
  tallest = max(standing, key=lambda body: body.modelHeight)
  widest = max(standing, key=lambda body: body.width)
  smallest = min(standing, key=lambda body: (body.spawnHeight, body.modelHeight))
  walkedHere = name == "medium"
  climbing = (
    f"measured: on a human male, a medium body, taken for every medium body until the measurement zone's walk of the {smallest.name} (the"
    " group's smallest, whose climbing sets the group's if climbing scales with size) measures the group"
    if walkedHere else
    f"unmeasured: the medium group's value, walked by a human male, until the measurement zone's walk of the {smallest.name} (the group's"
    " smallest, whose climbing sets the group's if climbing scales with size) measures it; whether the client's limits scale with size is unproven"
  )
  eyeHeight = designEyeHeight * tallest.avatarHeight / walkedAvatarHeight
  sources = {
    "playerHeight": (
      f"unmeasured: as clearance, the {tallest.name}'s model height, the group's tallest body ({bodiesSource}); the open space the client"
      " lets a player through is what the measurement zone's stepped ceilings measure"
    ),
    "playerWidth": (
      f"unmeasured: as clearance, the {widest.name}'s model width side to side, the group's widest standing body ({bodiesSource});"
      " the opening the client lets a player through is what the measurement zone's narrowing openings measure"
    ),
    "stepHeight": f"{climbing}: {walkedStepSource}",
    "walkableNormalZ": f"{climbing}: {walkedSlopeSource}",
    "steepestWalkableDegrees": "derived: walkableNormalZ as the angle from level of the steepest face the group walks",
    "eyeHeight": (
      f"unmeasured: the design eye of eye-level views, {designEyeHeight:g} for a body of avatarHeight {walkedAvatarHeight:g} scaled by"
      f" the {tallest.name}'s avatarHeight ({tallest.avatarHeight:g}); RoF2's first-person eye sits 3.69 over a human male's origin"
      " (docs/clientRendering.md, Camera), which these views do not follow; the measurement zone's walk records each body's"
    ),
    "swimEyeAboveSurface": (
      f"unmeasured: a unit over the water keeps a swimming view's eye clear of the surface; in {rof2Walk}, a swimming player's origin"
      " floated 0.10 under the swim box's top, which does not give a view's eye"
    ),
    "minimumSwimDepth": f"{climbing}: {walkedSwimSource}",
  }
  return BodyGroup(
    name=name, members=members, tallest=tallest, widest=widest, smallest=smallest, playerHeight=tallest.modelHeight, playerWidth=widest.width,
    stepHeight=walkedStepHeight, walkableNormalZ=walkedWalkableNormalZ, eyeHeight=eyeHeight, swimEyeAboveSurface=swimEyeAboveSurfaceValue,
    minimumSwimDepth=walkedSwimDepth, sources=sources,
  )


groups = {name: bodyGroup(name) for name in groupNames}


def groupOfCode(code):
  """The group of the playable body drawn with a model code (the scale figure's dark elf female is a medium body)."""
  found = [body for body in bodies if body.code == code]
  if not found:
    raise ValueError(f"No playable body is drawn with the model {code}")
  return next(name for name in groupNames if found[0].race in groupRaces[name])


@dataclass(frozen=True)
class Strictest:
  """What every one of some groups passes: the least step, the least steep walkable face, and the most clearance among them."""
  groups: tuple
  stepHeight: float
  walkableNormalZ: float
  playerHeight: float
  playerWidth: float

  @property
  def steepestWalkableDegrees(self):
    return math.degrees(math.acos(self.walkableNormalZ))

  def described(self):
    return {"groups": list(self.groups), "stepHeight": self.stepHeight, "steepestWalkableDegrees": round(self.steepestWalkableDegrees, 1), "playerHeight": self.playerHeight}


def strictest(names):
  chosen = [groups[name] for name in checkedGroupNames(list(names))]
  return Strictest(
    groups=tuple(body.name for body in chosen), stepHeight=min(body.stepHeight for body in chosen),
    walkableNormalZ=max(body.walkableNormalZ for body in chosen), playerHeight=max(body.playerHeight for body in chosen),
    playerWidth=max(body.playerWidth for body in chosen),
  )


def highestOrigin():
  """The highest any playable body's origin stands over its feet (its avatarHeight)."""
  return max(body.avatarHeight for body in bodies)


def shortest():
  """The group with the least clearance, which any space some player fits holds."""
  return min(groups.values(), key=lambda candidate: candidate.playerHeight)


def tallest():
  """The group with the most clearance to judge by, which frames views of what any group meets."""
  return max(groups.values(), key=lambda candidate: candidate.playerHeight)


def planGroup(builtFor):
  """The group a plan of the lane shows when none is named: of the groups the zone is built for, the one judged by the most clearance,
  whose lane marks most of what some group cannot pass."""
  return max(builtFor, key=lambda name: groups[name].playerHeight)


def viewGroup(builtFor):
  """The group whose eye a view stands at when none is named: the middle of the groups the zone is built for (the larger of two)."""
  ordered = [name for name in groupNames if name in builtFor]
  return ordered[len(ordered) // 2]


def group(name):
  if name not in groups:
    raise ValueError(f"No body group '{name}': the groups are {list(groupNames)}")
  return groups[name]


def checkedGroupNames(names):
  """A builtFor list as given: one or more distinct group names, returned in groupNames' order."""
  if not isinstance(names, (list, tuple)) or not names:
    raise ValueError(f"builtFor is a list of one or more of {list(groupNames)}, got {names!r}")
  unknown = [name for name in names if name not in groups]
  if unknown:
    raise ValueError(f"builtFor names no body group {unknown}: the groups are {list(groupNames)}")
  if len(set(names)) != len(names):
    raise ValueError(f"builtFor names a group twice: {list(names)}")
  return tuple(name for name in groupNames if name in names)


def builtForAt(zoneBuiltFor, regionBuiltFor):
  """The groups a spot is built for: its region's setting, else its zone's, else every group."""
  if regionBuiltFor is not None:
    return tuple(regionBuiltFor)
  return tuple(zoneBuiltFor) if zoneBuiltFor is not None else groupNames


def judged(passingGroups, builtFor):
  """A spot's verdict from the groups that pass it: "fails" when a group it is built for does not, "finding" when only others do not,
  "passes" when every group does; with the groups that fail."""
  failing = [name for name in groupNames if name not in passingGroups]
  builtFailing = [name for name in failing if name in builtFor]
  verdict = "fails" if builtFailing else "finding" if failing else "passes"
  return {"verdict": verdict, "passingGroups": [name for name in groupNames if name in passingGroups], "failingGroups": failing, "builtFor": list(builtFor)}


def described():
  return {"bodies": [body.described() for body in bodies], "bodiesSource": bodiesSource, "groups": [groups[name].described() for name in groupNames]}
