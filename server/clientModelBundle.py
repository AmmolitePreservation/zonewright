"""The client models a zone uses, copied into its archive. Every client model the scene places to ship (bridgeExport's clientModels:
scenery and interactive pieces alike) goes into the archive byte for byte, once, with the textures its drawn materials name, each taken
from the archives the client loads it with (its own first) and recorded with its archive and SHA-256. Scenery is placed by the .zon;
an interactive piece is a doors or object row naming the copy (pieceRows), never also a .zon placement. What cannot be copied
faithfully is refused: a classic actor, a skinned model (its animations are not bundled), a client entry patched since it was placed,
and two different models or textures under one name; a texture no archive the client loads it with holds is missing for the client too
and is listed."""
import collections
import hashlib

import clientModelRows
import eqArchive
import eqgFiles
import eqLinks
import modelMeasures
import zoneSources

startupMemo = {}


def startupTextures(clientRoot):
  """The textures the startup EQG archives' models register (a material's texture is registered by name when its model loads,
  EQGraphicsDX9.dll 0x10013e30): name to {archive, sha256} of the first that registers it. A zone's material of the same name takes
  this one (research 6), whatever its own archive holds."""
  key = (str(clientRoot), zoneSources.clientListingFingerprint(clientRoot))
  if key in startupMemo:
    return startupMemo[key]
  textures = {}
  for link in eqLinks.globalLinks(clientRoot)["archives"]:
    if not link["archive"].endswith(".eqg"):
      continue
    archive = eqArchive.EQArchive(clientRoot / link["archive"])
    for entry in sorted(name for name in archive.entries if name.endswith((".mod", ".mds"))):
      data = archive.read(entry)
      parsed = eqgFiles.parseModel(data, entry) if entry.endswith(".mod") else eqgFiles.parseSkinnedModel(data, entry)
      for material in parsed["materials"]:
        for value in material["properties"].values():
          name = modelMeasures.textureNamesOf(value)
          if name and name in archive.entries and name not in textures:
            textures[name] = {"archive": link["archive"], "sha256": hashlib.sha256(archive.read(name)).hexdigest()}
  startupMemo.clear()
  startupMemo[key] = textures
  return textures


def failure(kind, message, **fields):
  return {"failure": kind} | fields | {"message": message}


def bundle(clientRoot, clientModels):
  """The archive entries the scene's client models bring and what they are: {files: name to bytes, zonModels: the entries the .zon
  names, placements: the scenery's .zon placements, rowPieces: the interactive pieces, copies: the manifest's records (each model with
  its archive, SHA-256, size, placements, rows, and textures), failures, findings}."""
  files, sources, copies, failures, findings = {}, {}, {}, [], []
  archives = {}

  def archiveOf(name):
    if name not in archives:
      archives[name] = eqArchive.EQArchive(clientRoot / name)
    return archives[name]

  def take(name, data, source, objectName):
    if name in files and files[name] != data:
      failures.append(failure("two client files under one name", f"{name} would hold both {sources[name]} and {source} (different bytes), placed by '{objectName}'; the archive holds one file of a name", entry=name, sources=[sources[name], source]))
      return False
    files[name], sources[name] = data, source
    return True

  bundled = []
  for piece in sorted(clientModels, key=lambda found: found["object"]):
    record = piece["model"]
    try:
      clientModelRows.requireBundleable(record)
      data = clientModelRows.modelBytes(clientRoot, record)
      model = clientModelRows.parsedModel(record, data)
    except ValueError as error:
      failures.append(failure("client model not copied", f"'{piece['object']}': {error}", object=piece["object"]))
      continue
    if model["bones"] is not None:
      failures.append(failure("client model not copied", f"'{piece['object']}': {record['archive']}:{record['entry']} is skinned; its animations are not copied, so the zone would draw it otherwise", object=piece["object"]))
      continue
    entry = record["entry"]
    if not take(entry, data, f"{record['archive']}:{entry}", piece["object"]):
      continue
    bundled.append(piece)
    copy = copies.setdefault(entry, {"entry": entry, "archive": record["archive"], "sha256": record["sha256"], "bytes": len(data), "placements": 0, "rows": 0, "textures": [], "missingTextures": []})
    copy["rows" if piece["row"] is not None else "placements"] += 1
    if copy["textures"] or copy["missingTextures"]:
      continue
    for textureName in modelMeasures.eqgMeasures(model)["textures"]:
      holder = next((name for name in record["textureArchives"] if textureName in archiveOf(name).entries), None)
      if holder is None:
        findings.append({"finding": "texture missing for the client too", "entry": entry, "texture": textureName, "message": f"{textureName}, which {entry} names, is in no archive the client loads it with ({record['textureArchives']}): its faces draw without it in the client too"})
        copy["missingTextures"].append(textureName)
        continue
      textureData = archiveOf(holder).read(textureName)
      if take(textureName, textureData, f"{holder}:{textureName}", piece["object"]):
        copy["textures"].append({"name": textureName, "archive": holder, "sha256": hashlib.sha256(textureData).hexdigest(), "bytes": len(textureData)})
  counts = collections.Counter()
  placements = []
  for piece in bundled:
    if piece["row"] is not None:
      continue
    actor = piece["model"]["actor"]
    counts[actor] += 1
    placements.append({"model": piece["model"]["entry"], "name": f"{actor}{counts[actor]:02d}", "object": piece["object"], "position": piece["position"], "rotation": piece["rotation"], "scale": piece["scale"]})
  startup = startupTextures(clientRoot)
  for copy in copies.values():
    for texture in copy["textures"]:
      registered = startup.get(texture["name"])
      if registered is not None and registered["sha256"] != texture["sha256"]:
        findings.append({"finding": "texture from startup", "texture": texture["name"], "entry": copy["entry"], "startupArchive": registered["archive"], "message": f"{texture['name']} of {copy['entry']} is registered first by {registered['archive']}, with other bytes: the client draws that one, as it does where the model came from"})
  return {
    "files": files, "zonModels": sorted({placement["model"] for placement in placements}), "placements": placements,
    "rowPieces": [piece for piece in bundled if piece["row"] is not None], "copies": sorted(copies.values(), key=lambda copy: copy["entry"]),
    "failures": failures, "findings": findings,
  }


def ownTextureFailures(clientRoot, ownTextures):
  """Our own materials' textures ({name: sha256}) stored under a name a startup model's material registers with other bytes: the
  client draws the startup one on our geometry."""
  startup = startupTextures(clientRoot)
  return [
    failure("own texture shadowed", f"Our texture {name} has the name of {startup[name]['archive']}'s, which the client registers at startup and draws on our faces instead; rename ours", texture=name, startupArchive=startup[name]["archive"])
    for name, digest in sorted(ownTextures.items()) if name in startup and startup[name]["sha256"] != digest
  ]
