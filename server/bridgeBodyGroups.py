"""Which body groups (playerScale) a spot in the open scene is built for: the setting of the region holding it (the smallest in plan
first, as the lane judges regions), else the zone's (setZoneProperties builtFor), else every group; and a spot's verdict from the groups
that pass it. Runs under Blender's Python."""
import bpy
import numpy

import bridgeMeshAccess
import playerScale


def zoneBuiltFor():
  # bridgeCommands imports nearly every bridge module, this one among them, so it is imported only once they all are.
  import bridgeCommands
  stored = bridgeCommands.readZoneProperties(bpy.context.scene).get("builtFor")
  return None if stored is None else tuple(stored)


def regionsByPrecedence():
  """The scene's regions, smallest in plan first, each (object, outline, bottom, top, its own builtFor or None)."""
  found = []
  for sceneObject in bpy.context.scene.objects:
    if sceneObject.type != "MESH" or bridgeMeshAccess.regionIntentProperty not in sceneObject:
      continue
    outline, bottom, top = bridgeMeshAccess.regionShape(sceneObject)
    area = abs(float(numpy.dot(outline[:, 0], numpy.roll(outline[:, 1], -1)) - numpy.dot(outline[:, 1], numpy.roll(outline[:, 0], -1)))) / 2
    own = sceneObject.get(bridgeMeshAccess.regionBuiltForProperty)
    found.append((area, sceneObject.name, outline, bottom, top, None if own is None else tuple(own)))
  found.sort(key=lambda region: (region[0], region[1]))
  return [region[1:] for region in found]


class BuiltFor:
  """The groups each spot is built for, read once from the scene's regions and zone."""

  def __init__(self):
    self.zone = zoneBuiltFor()
    self.regions = regionsByPrecedence()

  def at(self, point):
    """The groups a point [x, y, z] is built for; a point [x, y] is taken at whatever height its region spans."""
    for _, outline, bottom, top, own in self.regions:
      if (len(point) == 2 or bottom <= point[2] <= top) and bridgeMeshAccess.insidePolygon(numpy.array([[point[0], point[1]]]), outline)[0]:
        return playerScale.builtForAt(self.zone, own)
    return playerScale.builtForAt(self.zone, None)

  def judged(self, point, passingGroups):
    return playerScale.judged(passingGroups, self.at(point))


def viewerAt(point):
  """The body group whose eye a view of a point stands at: the view group (playerScale.viewGroup) of the groups the point is built for."""
  return playerScale.groups[playerScale.viewGroup(BuiltFor().at(point))]


def strictestAt(point):
  """What every group a point is built for climbs (playerScale.strictest): a build there must suit each of them."""
  return playerScale.strictest(BuiltFor().at(point))
