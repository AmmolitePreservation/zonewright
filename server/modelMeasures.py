"""What a client model measurably is, read from its file and from how the client's zones place it: its size and footprint, where its
origin sits on it, its drawn triangles, collision, skinning, materials and textures with the share of drawn area each covers, and per
zone how often, at what scales, and how it sits (upright or tilted) beside which other models. A classic actor, which has no entry of
its own, is identified by a digest of the fragments it draws."""
import collections
import hashlib
import math
import struct

import numpy

import eqgFiles
import eqSkeletons

# Where the origin sits: within this share of the model's largest extent of a face of its bounds (or of its middle) counts as on it.
originTolerance = 0.02
# A placement turned more than this about a horizontal axis is tilted.
tiltDegrees = 1.0
# Other placements within this many units of a placement are beside it.
besideRadius = 30.0
listLength = 5


def originPlacement(low, high):
  """Where a model's origin sits on its bounds: base (on its bottom: it stands on the ground), hanging (on its top), wallBack (on one of
  its sides: it hangs on a wall), center (inside its middle third on every axis), or offset."""
  extent = high - low
  tolerance = max(float(extent.max()) * originTolerance, 0.01)
  if abs(low[2]) <= tolerance and high[2] > tolerance:
    return "base"
  if abs(high[2]) <= tolerance and low[2] < -tolerance:
    return "hanging"
  if any(abs(low[axis]) <= tolerance or abs(high[axis]) <= tolerance for axis in (0, 1)):
    return "wallBack"
  if all(low[axis] + extent[axis] / 3 <= 0 <= high[axis] - extent[axis] / 3 for axis in range(3)):
    return "center"
  return "offset"


def triangleAreas(vertices, triangles):
  corners = vertices[triangles]
  return 0.5 * numpy.linalg.norm(numpy.cross(corners[:, 1] - corners[:, 0], corners[:, 2] - corners[:, 0]), axis=1)


def textureNamesOf(value):
  return value.lower() if isinstance(value, str) and value.lower().endswith((".dds", ".bmp", ".tga", ".png", ".jpg")) else None


def eqgMeasures(model):
  """An EQG model's measured facts (eqgFiles.parseModel): size [across x, along y, up z] and footprint over its drawn vertices, where
  its origin sits, drawn triangles, the share of them players collide with (not flagged passable), skinning, its drawn materials with
  their shaders and textures (drawnMaterials), and each diffuse texture's share of its drawn area (textureShares)."""
  drawn = model["triangleMaterials"] >= 0
  triangles = model["triangles"][drawn]
  materialIndices = model["triangleMaterials"][drawn]
  used = numpy.unique(triangles) if len(triangles) else numpy.zeros(0, dtype=numpy.int64)
  points = model["vertices"][used]
  finite = points[numpy.isfinite(points).all(axis=1)]
  low, high = (finite.min(0), finite.max(0)) if len(finite) else (numpy.zeros(3), numpy.zeros(3))
  size = high - low
  areas = triangleAreas(model["vertices"], triangles) if len(triangles) else numpy.zeros(0)
  areas = numpy.where(numpy.isfinite(areas), areas, 0)
  palette = sorted({int(index) for index in materialIndices})
  materials, textureAreas = [], collections.Counter()
  for index in palette:
    material = model["materials"][index]
    named = {key: textureNamesOf(value) for key, value in material["properties"].items() if textureNamesOf(value)}
    materials.append({"name": material["name"], "shader": material["shader"], "textures": named})
    diffuse = named.get("e_TextureDiffuse0")
    if diffuse:
      textureAreas[diffuse] += float(areas[materialIndices == index].sum())
  total = sum(textureAreas.values())
  passable = (model["triangleFlags"][drawn] & eqgFiles.passableFlag) != 0
  return {
    "size": [round(float(value), 2) for value in size], "footprint": round(float(size[0] * size[1]), 1),
    "bounds": [[round(float(value), 2) for value in low], [round(float(value), 2) for value in high]], "origin": originPlacement(low, high),
    "triangles": int(len(triangles)), "collisionShare": round(1 - float(passable.mean()), 3) if len(passable) else 0.0,
    "skinned": model["bones"] is not None, "bones": 0 if model["bones"] is None else len(model["bones"]["names"]), "drawnMaterials": materials,
    "textures": sorted({name for material in materials for name in material["textures"].values()}),
    "textureShares": {name: round(area / total, 3) for name, area in textureAreas.most_common()} if total > 0 else {},
  }


def dominantColors(textureShares, textureFacts):
  """The colors a model reads as: each diffuse texture's measured color name, weighed by the share of the drawn area it covers."""
  colors = collections.Counter()
  for name, share in textureShares.items():
    facts = textureFacts.get(name)
    if facts is not None and "colorName" in facts:
      colors[facts["colorName"]] += share
  return [{"color": color, "share": round(share, 3)} for color, share in colors.most_common(3)]


def placementSits(placement):
  """upright, or tilted: turned about a horizontal axis by more than tiltDegrees. An EQGZ placement turns by (heading, about Y, about X)
  in radians; an EQ terrain one by (about X, about Y, about Z) in degrees."""
  if "rotationDegrees" in placement:
    tilts = [float(angle) for angle in placement["rotationDegrees"][:2]]
  else:
    tilts = [math.degrees(float(angle)) for angle in placement["rotation"][1:]]
  return "tilted" if any(abs(angle) > tiltDegrees for angle in tilts) else "upright"


def placementUses(placements):
  """Per placed model name: how many placements, its scale range, how its placements sit (upright, or tilted about a horizontal axis
  by more than tiltDegrees), and the models most often placed within besideRadius of it."""
  byModel = collections.defaultdict(list)
  positions = numpy.array([placement["position"] for placement in placements], dtype=numpy.float64).reshape(-1, 3)
  names = [placement["model"] for placement in placements]
  for index, name in enumerate(names):
    byModel[name].append(index)
  modelNames = sorted(byModel)
  codeOf = {name: code for code, name in enumerate(modelNames)}
  codes = numpy.array([codeOf[name] for name in names], dtype=numpy.int64)
  keys = numpy.floor(positions[:, :2] / besideRadius).astype(numpy.int64)
  grouped = collections.defaultdict(list)
  for index, (cellX, cellY) in enumerate(keys):
    grouped[(int(cellX), int(cellY))].append(index)
  cells = {key: numpy.array(members, dtype=numpy.int64) for key, members in grouped.items()}
  empty = numpy.zeros(0, dtype=numpy.int64)
  uses = {}
  for name, indices in byModel.items():
    scales = [float(numpy.max(placements[index]["scale"])) for index in indices]
    sits = collections.Counter()
    counts = numpy.zeros(len(modelNames), dtype=numpy.int64)
    for index in indices:
      sits[placementSits(placements[index])] += 1
      cellX, cellY = keys[index]
      near = numpy.concatenate([cells.get((int(cellX) + offsetX, int(cellY) + offsetY), empty) for offsetX in (-1, 0, 1) for offsetY in (-1, 0, 1)])
      near = near[numpy.linalg.norm(positions[near] - positions[index], axis=1) <= besideRadius]
      counts += numpy.bincount(codes[near], minlength=len(modelNames))
    counts[codeOf[name]] = 0
    beside = {modelNames[code]: int(counts[code]) for code in numpy.argsort(-counts, kind="stable")[:listLength] if counts[code] > 0}
    uses[name] = {
      "placements": len(indices), "scaleRange": [round(min(scales), 3), round(max(scales), 3)], "sits": dict(sits),
      "beside": beside,
    }
  return uses


def wldActorDigest(worldFile, actorFragment, references):
  """A digest of what a classic actor draws: its actor definition and the bodies of the fragments it draws (meshes, or a skeleton and
  its meshes), with their materials' texture names, so the same actor in another archive with the same content digests alike only
  where its file stores it alike."""
  digest = hashlib.sha256(actorFragment.name.encode("latin1") + actorFragment.body)
  for fragment in references:
    digest.update(struct.pack("<I", fragment.fragmentType) + fragment.body)
    if fragment.fragmentType == 0x2D:
      mesh = worldFile.fragments[struct.unpack_from("<i", fragment.body, 4)[0] - 1]
      digest.update(mesh.body)
      for textureName in sorted(meshTextureNames(worldFile, mesh)):
        digest.update(textureName.encode("latin1"))
    elif fragment.fragmentType == 0x11:
      skeleton = worldFile.fragment(struct.unpack_from("<i", fragment.body, 4)[0], 0x10)
      digest.update(skeleton.body)
      dags, skins = eqSkeletons.readSkeleton(worldFile, skeleton)
      for mesh in eqSkeletons.skinMeshes(worldFile, skins):
        digest.update(mesh.body)
        for textureName in sorted(meshTextureNames(worldFile, mesh)):
          digest.update(textureName.encode("latin1"))
  return digest.hexdigest()


def meshTextureNames(worldFile, meshFragment):
  if meshFragment.fragmentType != 0x36:
    return set()
  return {name for material in worldFile.mesh(meshFragment)["materials"] for name in material["textureNames"]}
