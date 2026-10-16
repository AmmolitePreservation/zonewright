"""The client models a zone uses, copied into its archive. Every client model the scene places to ship (bridgeExport's clientModels:
scenery and interactive pieces alike) goes into the archive byte for byte, once, with the textures its drawn materials name, each taken
from the archives the client loads it with (its own first) and recorded with its archive and SHA-256. A model or texture whose name the
archive already holds with other bytes (one of ours, or another copy's; a model's own bytes drawn with other textures count as other) is
stored under its archive's name before its own (storedName): the .zon places a renamed model by that file, and the models naming a
renamed texture name that instead (withTexturesRenamed), their other bytes as the client ships them. Scenery is placed by the .zon; an
interactive piece is a doors or object row naming the copy by its entry's name (pieceRows), never also a .zon placement, so it claims
its name before any scenery and is never renamed. What cannot be copied faithfully is refused: a classic actor, a skinned model (its
animations are not bundled), a client entry patched since it was placed, an interactive piece whose name is taken, a texture under a
name the startup archives register (the client draws that one, whatever the archive holds), and a model or texture whose stored name is
taken too; a texture no archive the client loads it with holds is missing for the client too and is listed."""
import collections
import hashlib
import struct

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


def storedName(name, archive):
  """The name a copied model or texture is stored under where its own is taken: its archive's stem before it (crescent.eqg's
  av_wood01a_c.dds as crescent_av_wood01a_c.dds, mesa.eqg's obj_takruins_medrockb.mod as mesa_obj_takruins_medrockb.mod)."""
  return f"{archive.rsplit('.', 1)[0]}_{name}"


def withTexturesRenamed(modelBytes, renames, sourceName):
  """An EQGM model's bytes with the textures its materials' properties name renamed ({name: new name}, lowercase): the new names
  appended to its string table and the properties that named the old ones pointed at them; every other byte as it was."""
  if modelBytes[:4] != b"EQGM":
    raise ValueError(f"{sourceName}: magic {modelBytes[:4]!r} is not EQGM")
  version, stringLength, materialCount = struct.unpack_from("<3I", modelBytes, 4)
  if version not in eqgFiles.supportedModelVersions:
    raise ValueError(f"{sourceName}: model version {version} is not supported (only {eqgFiles.supportedModelVersions})")
  tableStart = 28
  stringTable = modelBytes[tableStart:tableStart + stringLength]
  added, offsets = bytearray(), {}
  for name, newName in renames.items():
    offsets[name] = stringLength + len(added)
    added += newName.encode("ascii") + b"\0"
  patched = bytearray(modelBytes[:tableStart + stringLength]) + added + modelBytes[tableStart + stringLength:]
  struct.pack_into("<I", patched, 8, stringLength + len(added))
  position, renamed = tableStart + stringLength + len(added), set()
  for _ in range(materialCount):
    (propertyCount,) = struct.unpack_from("<I", patched, position + 12)
    position += 16
    for _ in range(propertyCount):
      propertyType, value = struct.unpack_from("<II", patched, position + 4)
      name = eqgFiles.readString(stringTable, value).lower() if propertyType == 2 else None
      if name in offsets:
        struct.pack_into("<I", patched, position + 8, offsets[name])
        renamed.add(name)
      position += 12
  if renamed != set(renames):
    raise ValueError(f"{sourceName}: no material names {sorted(set(renames) - renamed)}")
  return bytes(patched)


def bundle(clientRoot, clientModels, taken):
  """The archive entries the scene's client models bring beside ours (taken: name to bytes) and what they are: {files: name to bytes,
  zonModels: the entries the .zon names, placements: the scenery's .zon placements, rowPieces: the interactive pieces, copies: the
  manifest's records (each model with its archive, its client entry, the client's SHA-256, size, placements, rows, its textures as
  stored, storedEntry where it is stored under another name, and where it names a texture stored under another name, renamedTextures
  and the stored model's storedSha256), failures, findings}."""
  files, sources, storedModels, storedTextures, copies, failures, findings = {}, {}, {}, {}, {}, [], []
  archives, textures = {}, {}
  startup = startupTextures(clientRoot)

  def archiveOf(name):
    if name not in archives:
      archives[name] = eqArchive.EQArchive(clientRoot / name)
    return archives[name]

  def holding(name):
    return taken[name] if name in taken else files.get(name)

  def holderOf(name):
    return "ours" if name in taken else sources[name]

  def clash(name, source, objectName, reason="the archive holds one file of a name", **fields):
    failures.append(failure("two files under one name", f"{name} would hold both {holderOf(name)} and {source} (different bytes), placed by '{objectName}'; {reason}", entry=name, sources=[holderOf(name), source], **fields))

  def clientTexture(holder, name):
    if (holder, name) not in textures:
      data = archiveOf(holder).read(name)
      textures[(holder, name)] = (data, hashlib.sha256(data).hexdigest())
    return textures[(holder, name)]

  def takeModel(entry, data, looks, holder, piece):
    """The name the model is stored under, or None where it cannot be. The same model drawn with the same textures (looks: each
    texture's name and SHA-256, None where missing) keeps the copy its first placement stored, which may name renamed textures."""
    source = f"{holder}:{entry}"
    key = (entry, hashlib.sha256(data).hexdigest(), looks)
    if key in storedModels:
      return storedModels[key]
    stored = entry
    if holding(entry) is not None:
      row = piece["row"]
      if row is not None:
        rowName = row["name"] if row["table"] == "doors" else row["objectName"]
        clash(entry, source, piece["object"], f"it is an interactive piece, whose {row['table']} row names its model {rowName}, so it is not stored under another name; place one of them otherwise")
        return None
      stored = storedName(entry, holder)
      if holding(stored) is not None:
        clash(stored, source, piece["object"])
        return None
    files[stored], sources[stored], storedModels[key] = data, source, stored
    return stored

  def takeTexture(name, data, holder, objectName):
    """The name the texture is stored under, or None where it cannot be."""
    source = f"{holder}:{name}"
    held = holding(name)
    if held is None or held == data:
      if name not in taken:
        files[name], sources[name] = data, sources.get(name, source)
      return name
    key = (name, hashlib.sha256(data).hexdigest())
    if key in storedTextures:
      return storedTextures[key]
    if name in startup:
      clash(name, source, objectName)
      return None
    stored = storedName(name, holder)
    held = holding(stored)
    if held is not None and held != data:
      clash(stored, source, objectName)
      return None
    files[stored], sources[stored], storedTextures[key] = data, source, stored
    return stored

  bundled = []
  # Interactive pieces first: their rows name their models by entry, so they keep those names and scenery is stored around them.
  for piece in sorted(clientModels, key=lambda found: (found["row"] is None, found["object"])):
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
    holders = {name: next((archive for archive in record["textureArchives"] if name in archiveOf(archive).entries), None) for name in modelMeasures.eqgMeasures(model)["textures"]}
    looks = tuple((name, None if holder is None else clientTexture(holder, name)[1]) for name, holder in holders.items())
    stored = takeModel(entry, data, looks, record["archive"], piece)
    if stored is None:
      continue
    bundled.append((piece, stored))
    if stored not in copies:
      copy = copies[stored] = {"entry": entry, "archive": record["archive"], "sha256": record["sha256"], "bytes": len(data), "placements": 0, "rows": 0, "textures": [], "missingTextures": []} | ({} if stored == entry else {"storedEntry": stored})
      for textureName, holder in holders.items():
        if holder is None:
          findings.append({"finding": "texture missing for the client too", "entry": entry, "texture": textureName, "message": f"{textureName}, which {entry} names, is in no archive the client loads it with ({record['textureArchives']}): its faces draw without it in the client too"})
          copy["missingTextures"].append(textureName)
          continue
        textureData, textureDigest = clientTexture(holder, textureName)
        storedTexture = takeTexture(textureName, textureData, holder, piece["object"])
        if storedTexture is None:
          continue
        copy["textures"].append({"name": storedTexture, "archive": holder, "sha256": textureDigest, "bytes": len(textureData)})
        if storedTexture != textureName:
          copy.setdefault("renamedTextures", {})[textureName] = storedTexture
      if "renamedTextures" in copy:
        files[stored] = withTexturesRenamed(data, copy["renamedTextures"], entry)
        copy["storedSha256"] = hashlib.sha256(files[stored]).hexdigest()
    copies[stored]["rows" if piece["row"] is not None else "placements"] += 1
  counts = collections.Counter()
  placements = []
  for piece, stored in bundled:
    if piece["row"] is not None:
      continue
    actor = clientModelRows.actorName(stored)
    counts[actor] += 1
    placements.append({"model": stored, "name": f"{actor}{counts[actor]:02d}", "object": piece["object"], "position": piece["position"], "rotation": piece["rotation"], "scale": piece["scale"]})
  for copy in copies.values():
    for texture in copy["textures"]:
      registered = startup.get(texture["name"])
      if registered is not None and registered["sha256"] != texture["sha256"]:
        findings.append({"finding": "texture from startup", "texture": texture["name"], "entry": copy["entry"], "startupArchive": registered["archive"], "message": f"{texture['name']} of {copy['entry']} is registered first by {registered['archive']}, with other bytes: the client draws that one, as it does where the model came from"})
  return {
    "files": files, "zonModels": sorted({placement["model"] for placement in placements}), "placements": placements,
    "rowPieces": [piece for piece, _ in bundled if piece["row"] is not None], "copies": sorted(copies.values(), key=lambda copy: (copy["entry"], copy["archive"])),
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
