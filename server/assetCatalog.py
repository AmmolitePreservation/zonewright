"""The asset catalog: client graphical assets with what was measured from their files (assetSurvey) and what they were judged to be
(descriptions written in assetVocabulary's words). The repository's catalog folder holds what is worth keeping and sharing: measured/
with one file per surveyed client source, interpreted.json with the descriptions, and vocabulary.json with the terms added to the
vocabulary. The tooling root's catalog folder holds what the client regenerates and this machine alone uses: the extracted textures and
thumbnails, and the measurements of zone archives outside the client. A source's measured facts are rebuilt when its files' SHA-256 or
the survey's version changes; descriptions stay until rewritten. Texture and model ids carry a hash of the content, so a description
follows the asset across every zone that ships it."""
import concurrent.futures
import hashlib
import json
import multiprocessing
import re

import assetSurvey
import assetVocabulary
import eqLinks
import eqModels
import machineProfile
import serverRoleExtract
import zoneSurvey

# Facts that differ between the sources an asset appears in; everything else is a property of the asset itself.
perSourceKeys = (
  "archives", "shadowed", "uses", "materials", "wldMaterials", "ecosystemLayers", "archive", "modelKind", "placements", "scaleRange", "entry", "wld",
  "links", "sits", "beside",
)
# The fields a description holds, per asset kind (others take defaultFields). Each is written once and filled only where missing.
defaultFields = ("category", "tags", "description", "usage", "pairsWith")
fieldsByKind = {
  "texture": ("category", "tags", "description", "usage", "worldUnitsPerRepeat", "pairsWith"),
  "model": ("plainName", "category", "tags", "description", "usage", "dressingNotes", "pairsWith"),
}
textFields = ("plainName", "description", "usage", "dressingNotes")
# A model's tag groups, each a field of its own in the describe queue: what it is shaped like, made of and finished with, whose and from
# when, where it belongs, the mood it sets, its role in a scene, and what it is for.
modelTagGroups = ("form", "material", "finish", "style", "era", "biome", "mood", "sceneRole", "function")
descriptionKeys = ("plainName", "category", "tags", "description", "usage", "dressingNotes", "worldUnitsPerRepeat", "pairsWith")
provenanceKey = "provenance"
termPattern = re.compile(r"^[a-z][A-Za-z0-9]*$")
textSearchFields = ("plainName", "description", "usage", "dressingNotes", "category")
# The views a model's description is judged from (rendered by viewModels and kept under the tooling root), and a texture's.
modelViews = ("front", "side", "top", "threeQuarter")
textureViews = ("thumbnail",)
modelViewsVersion = 1
rolesFileName = "serverRoles.json"


def fieldsOf(kind):
  return fieldsByKind.get(kind, defaultFields)


def queueFields(kind):
  """The fields the describe queue tracks for a kind: its description fields, with a model's tags one per tag group."""
  fields = fieldsOf(kind)
  if kind != "model":
    return fields
  return tuple(field for field in fields if field != "tags") + tuple(f"tags.{group}" for group in modelTagGroups)


def writtenFields(kind, interpreted):
  if interpreted is None:
    return set()
  written = {field for field in fieldsOf(kind) if field in interpreted}
  return written | {f"tags.{group}" for group in interpreted.get("tags", {})}


class AssetCatalog:
  def __init__(self, toolingRoot, repositoryCatalogRoot):
    self.toolingRoot = toolingRoot
    self.root = repositoryCatalogRoot
    self.cacheRoot = toolingRoot / "catalog"
    self.interpretedPath = self.root / "interpreted.json"
    self.vocabularyPath = self.root / "vocabulary.json"
    self.mergedAssets = None

  def measuredFolder(self, sourceKey):
    """Client sources are kept in the repository; a zone archive outside the client is this machine's alone."""
    return (self.root if sourceKey.startswith(("zone:", "folder:", "archive:")) else self.cacheRoot) / "measured"

  def sourcePath(self, sourceKey):
    slug = re.sub(r"[^a-z0-9]+", "_", sourceKey.lower()).strip("_")[-60:]
    return self.measuredFolder(sourceKey) / f"{slug}_{hashlib.sha256(sourceKey.encode()).hexdigest()[:8]}.json"

  def fileHashes(self, paths):
    """SHA-256 by file name, through the zone survey's memo of hashes by size and modification time."""
    cache = zoneSurvey.SurveyCache(self.toolingRoot)
    hashes = zoneSurvey.hashFiles(paths, cache, False)
    cache.save()
    return {path.name.lower(): hashes[path] for path in paths}

  def texturesPresent(self, surveyed):
    return all((assetSurvey.textureFolder(self.cacheRoot, facts["sha256"]) / facts["fileName"]).is_file() and assetSurvey.thumbnailPath(self.cacheRoot, facts["sha256"]).is_file()
      for facts in surveyed["assets"].values() if facts["kind"] == "texture" and "fileName" in facts)

  def isCurrent(self, sourceKey, fileHashes):
    path = self.sourcePath(sourceKey)
    if not path.is_file():
      return False
    cached = json.loads(path.read_text(encoding="utf-8"))
    return cached["surveyVersion"] == assetSurvey.surveyVersion and cached["fileHashes"] == fileHashes and self.texturesPresent(cached)

  def store(self, sourceKey, surveyed, fileHashes):
    path = self.sourcePath(sourceKey)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(surveyed | {"surveyVersion": assetSurvey.surveyVersion, "fileHashes": fileHashes}, indent=1, sort_keys=True), encoding="utf-8")
    self.mergedAssets = None

  def surveyZones(self, clientRoot, modelCacheRoot, zoneNames, refresh, reportProgress):
    """The measured lane for many client zones, the stale ones surveyed in parallel worker processes. Returns per zone whether it was
    surveyed, already current, or why it could not be read."""
    outcomes, fileHashes = {}, {}
    sourcePaths = {}
    for zoneName in zoneNames:
      try:
        sourcePaths[zoneName] = assetSurvey.clientZoneSourcePaths(clientRoot, zoneName)
      except ValueError as error:
        outcomes[zoneName] = {"error": str(error)}
    reportProgress(0, None, "checking zone file hashes")
    cache = zoneSurvey.SurveyCache(self.toolingRoot)
    hashes = zoneSurvey.hashFiles([path for paths in sourcePaths.values() for path in paths], cache, False)
    cache.save()
    for zoneName, paths in sourcePaths.items():
      fileHashes[zoneName] = {path.name.lower(): hashes[path] for path in paths}
    stale = [zoneName for zoneName in sourcePaths if refresh or not self.isCurrent(f"zone:{zoneName}", fileHashes[zoneName])]
    outcomes |= {zoneName: {"current": True} for zoneName in sourcePaths if zoneName not in stale}
    # Workers resolve classic objects through the model index; building it once here keeps them from racing to write it.
    eqModels.loadIndex(clientRoot, modelCacheRoot)

    def record(zoneName, outcome):
      if "error" in outcome:
        outcomes[zoneName] = outcome
      else:
        self.store(f"zone:{zoneName}", outcome["survey"], fileHashes[zoneName])
        outcomes[zoneName] = {"surveyed": True}

    if len(stale) == 1:
      record(stale[0], assetSurvey.surveyClientZoneInWorker(clientRoot, modelCacheRoot, self.cacheRoot, stale[0]))
      reportProgress(1, 1, f"surveyed zone:{stale[0]}")
    elif stale:
      with concurrent.futures.ProcessPoolExecutor(min(machineProfile.workerCount(), len(stale)), mp_context=multiprocessing.get_context("spawn")) as pool:
        futures = {pool.submit(assetSurvey.surveyClientZoneInWorker, clientRoot, modelCacheRoot, self.cacheRoot, zoneName): zoneName for zoneName in stale}
        try:
          for done, future in enumerate(concurrent.futures.as_completed(futures), start=1):
            record(futures[future], future.result())
            reportProgress(done, len(stale), f"surveyed zone:{futures[future]}")
        except BaseException:
          # Leaving the pool otherwise runs every queued zone before the error surfaces.
          pool.shutdown(wait=False, cancel_futures=True)
          raise
    return dict(sorted(outcomes.items()))

  def surveyArchives(self, clientRoot, archiveNames, refresh, reportProgress):
    """The measured lane for object, on-demand, and startup archives (assetSurvey.objectArchives; None for every one), the stale ones
    surveyed in parallel worker processes. An archive that is no such archive is refused. Returns per archive whether it was surveyed,
    already current, or why it could not be read."""
    linked = assetSurvey.objectArchives(clientRoot)
    names = sorted(linked) if archiveNames is None else [name.lower() for name in archiveNames]
    unknown = [name for name in names if name not in linked]
    if unknown:
      raise ValueError(f"{unknown} are no object, on-demand, or startup archives the client links (zone archives are surveyed by zone)")
    reportProgress(0, None, "checking archive hashes")
    hashes = self.fileHashes([clientRoot / name for name in names])
    keyOf = {name: f"archive:{name}" for name in names}
    sourceHashes = {name: {name: hashes[name]} | {"links": json.dumps(linked[name], sort_keys=True)} for name in names}
    stale = [name for name in names if refresh or not self.isCurrent(keyOf[name], sourceHashes[name])]
    outcomes = {name: {"current": True} for name in names if name not in stale}

    def record(name, outcome):
      if "error" in outcome:
        outcomes[name] = outcome
      else:
        self.store(keyOf[name], outcome["survey"], sourceHashes[name])
        outcomes[name] = {"surveyed": True}

    if len(stale) == 1:
      record(stale[0], assetSurvey.surveyClientArchiveInWorker(clientRoot, self.cacheRoot, stale[0], linked[stale[0]]))
      reportProgress(1, 1, f"surveyed archive:{stale[0]}")
    elif stale:
      with concurrent.futures.ProcessPoolExecutor(min(machineProfile.workerCount(), len(stale)), mp_context=multiprocessing.get_context("spawn")) as pool:
        futures = {pool.submit(assetSurvey.surveyClientArchiveInWorker, clientRoot, self.cacheRoot, name, linked[name]): name for name in stale}
        try:
          for done, future in enumerate(concurrent.futures.as_completed(futures), start=1):
            record(futures[future], future.result())
            reportProgress(done, len(stale), f"surveyed archive:{futures[future]}")
        except BaseException:
          pool.shutdown(wait=False, cancel_futures=True)
          raise
    return dict(sorted(outcomes.items()))

  def survey(self, surveyFunction, sourceKey, sourcePaths, refresh):
    """The measured lane for one source, rebuilt when its files' SHA-256 or the survey's version changed, or when this machine lacks
    the textures extracted from it."""
    fileHashes = self.fileHashes(sourcePaths)
    if not refresh and self.isCurrent(sourceKey, fileHashes):
      return json.loads(self.sourcePath(sourceKey).read_text(encoding="utf-8"))
    surveyed = surveyFunction()
    if self.fileHashes(sourcePaths) != fileHashes:
      raise ValueError(f"{sourceKey}: its files changed while it was surveyed; survey it again")
    self.store(sourceKey, surveyed, fileHashes)
    return json.loads(self.sourcePath(sourceKey).read_text(encoding="utf-8"))

  def withFilePaths(self, facts):
    """A texture's facts with where this machine keeps its extracted file and thumbnail."""
    if facts["kind"] != "texture" or "fileName" not in facts:
      return facts
    return facts | {"file": str(assetSurvey.textureFolder(self.cacheRoot, facts["sha256"]) / facts["fileName"]), "thumbnail": str(assetSurvey.thumbnailPath(self.cacheRoot, facts["sha256"]))}

  def assets(self):
    """Every surveyed asset: the facts of the first source (by source key) that holds it, and per source what differs there."""
    if self.mergedAssets is None:
      merged = {}
      paths = sorted(path for folder in (self.root / "measured", self.cacheRoot / "measured") if folder.is_dir() for path in folder.glob("*.json"))
      for source in sorted((json.loads(path.read_text(encoding="utf-8")) for path in paths), key=lambda source: source["source"]):
        for assetID, facts in source["assets"].items():
          entry = merged.setdefault(assetID, {"id": assetID, "kind": facts["kind"], "name": facts["name"], "measured": {key: value for key, value in self.withFilePaths(facts).items() if key not in perSourceKeys}, "sources": {}})
          entry["sources"][source["source"]] = {key: facts[key] for key in perSourceKeys if key in facts}
          if facts["kind"] == "emitter":
            entry["measured"]["zones"] = entry["measured"]["zones"] | facts["zones"]
      roles = self.serverRoles()
      for entry in merged.values():
        if entry["kind"] == "model":
          entry["measured"]["role"] = roles.get(eqLinks.modelKey(entry["name"].removesuffix(".mod")), serverRoleExtract.sceneryRole)
      self.mergedAssets = merged
    return self.mergedAssets

  def serverRoles(self):
    """The in-game role of each model name as the server's doors and object tables hold it (serverRoleExtract), from the committed
    extract; none before one is made."""
    path = self.root / rolesFileName
    return json.loads(path.read_text(encoding="utf-8"))["roles"] if path.is_file() else {}

  def modelViewFolder(self, asset):
    return self.cacheRoot / "modelViews" / f"{asset['measured']['sha256'][:16]}v{modelViewsVersion}"

  def viewFiles(self, asset):
    """The files a description of the asset is judged from, by view name: a texture's thumbnail, a model's rendered views."""
    if asset["kind"] == "texture":
      return {"thumbnail": assetSurvey.thumbnailPath(self.cacheRoot, asset["measured"]["sha256"])} if "sha256" in asset["measured"] else {}
    if asset["kind"] == "model" and "sha256" in asset["measured"]:
      folder = self.modelViewFolder(asset)
      return {view: folder / f"{view}.png" for view in modelViews}
    return {}

  def requireAsset(self, assetID):
    assets = self.assets()
    if assetID not in assets:
      raise ValueError(f"'{assetID}' is not in the catalog; survey the zone or folder that holds it (surveyAssets) first")
    return assets[assetID]

  def interpretations(self):
    return json.loads(self.interpretedPath.read_text(encoding="utf-8")) if self.interpretedPath.is_file() else {}

  def vocabularyExtensions(self):
    return json.loads(self.vocabularyPath.read_text(encoding="utf-8")) if self.vocabularyPath.is_file() else {"categories": {}, "tags": {}}

  def vocabulary(self):
    return assetVocabulary.merged(self.vocabularyExtensions())

  def extendVocabulary(self, group, term, meaning):
    """Add a term: group is a tag group's name, or category:<kind> for an asset kind's categories."""
    vocabulary = self.vocabulary()
    if not termPattern.match(term):
      raise ValueError(f"A term is one camelCase word starting lowercase, got '{term}'")
    if not meaning.strip():
      raise ValueError("A term needs its meaning")
    extensions = self.vocabularyExtensions()
    if group.startswith("category:"):
      kind = group.split(":", 1)[1]
      if kind not in vocabulary["categories"]:
        raise ValueError(f"No asset kind '{kind}'; kinds: {list(vocabulary['categories'])}")
      if term in vocabulary["categories"][kind]:
        raise ValueError(f"'{term}' is already a {kind} category: {vocabulary['categories'][kind][term]}")
      extensions["categories"].setdefault(kind, {})[term] = meaning.strip()
    else:
      if group not in vocabulary["tagGroups"]:
        raise ValueError(f"No tag group '{group}'; groups: {list(vocabulary['tagGroups'])} (or category:<kind>)")
      if term in vocabulary["tagGroups"][group]["terms"]:
        raise ValueError(f"'{term}' is already a {group} term: {vocabulary['tagGroups'][group]['terms'][term]}")
      extensions["tags"].setdefault(group, {})[term] = meaning.strip()
    self.root.mkdir(parents=True, exist_ok=True)
    self.vocabularyPath.write_text(json.dumps(extensions, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    return {"group": group, "term": term, "meaning": meaning.strip()}

  def validatedFields(self, description, asset, vocabulary):
    """The description's fields, each checked against the vocabulary and the asset's kind; tags split one field per group."""
    assetID, kind = description["id"], asset["kind"]
    allowed = fieldsOf(kind)
    unknown = sorted(set(description) - {"id", "judgedFrom", "rewrite"} - set(allowed))
    if unknown:
      raise ValueError(f"{assetID}: a {kind} description holds {list(allowed)}; not {unknown}")
    fields = {}
    if "category" in description:
      if description["category"] not in vocabulary["categories"][kind]:
        raise ValueError(f"{assetID}: category {description['category']!r} is not a {kind} category: {sorted(vocabulary['categories'][kind])}")
      fields["category"] = description["category"]
    if "tags" in description:
      tags = description["tags"]
      if not isinstance(tags, dict) or not tags:
        raise ValueError(f"{assetID}: tags are {{group: [terms]}}, at least one group")
      for group, terms in tags.items():
        if group not in vocabulary["tagGroups"]:
          raise ValueError(f"{assetID}: no tag group '{group}'; groups: {list(vocabulary['tagGroups'])}")
        unknownTerms = sorted(set(terms) - set(vocabulary["tagGroups"][group]["terms"])) if isinstance(terms, list) and terms else None
        if unknownTerms is None or unknownTerms:
          raise ValueError(f"{assetID}: {group} takes a non-empty list of {sorted(vocabulary['tagGroups'][group]['terms'])}; unknown: {unknownTerms}")
        fields[f"tags.{group}"] = list(terms)
    for field in textFields:
      if field in description:
        if not isinstance(description[field], str) or not description[field].strip():
          raise ValueError(f"{assetID}: {field} must be written, not empty")
        fields[field] = description[field].strip()
    if "worldUnitsPerRepeat" in description:
      repeat = description["worldUnitsPerRepeat"]
      if repeat is not None and (isinstance(repeat, bool) or not isinstance(repeat, (int, float)) or repeat <= 0):
        raise ValueError(f"{assetID}: worldUnitsPerRepeat is a positive number of units, or null where no repeat applies (a normal map's is its diffuse's)")
      fields["worldUnitsPerRepeat"] = repeat
    if "pairsWith" in description:
      if not isinstance(description["pairsWith"], list):
        raise ValueError(f"{assetID}: pairsWith is a list of asset ids")
      for partner in description["pairsWith"]:
        self.requireAsset(partner)
      fields["pairsWith"] = list(description["pairsWith"])
    if not fields:
      raise ValueError(f"{assetID}: the description writes no field")
    return fields

  def judgedFrom(self, description, asset):
    """What the description was judged from, as recorded beside each field it writes: each view named (judgedFrom.views) by its image's
    SHA-256, and each measured fact named (judgedFrom.facts) by its value now. A view must exist (viewModels renders a model's views)."""
    assetID = description["id"]
    given = description.get("judgedFrom")
    if not isinstance(given, dict) or set(given) - {"views", "facts"} or not (given.get("views") or given.get("facts")):
      raise ValueError(f"{assetID}: judgedFrom names what it was judged from: {{views: [...], facts: [...]}}, at least one; a description written from a name alone is no description")
    files = self.viewFiles(asset)
    views = {}
    for view in given.get("views", []):
      if view not in files:
        raise ValueError(f"{assetID}: no view '{view}'; a {asset['kind']} is judged from {sorted(files) or 'no views'}")
      if not files[view].is_file():
        raise ValueError(f"{assetID}: its {view} view is not rendered on this machine; look at it first (viewModels with views)")
      views[view] = hashlib.sha256(files[view].read_bytes()).hexdigest()
    facts = {}
    for fact in given.get("facts", []):
      if fact not in asset["measured"]:
        raise ValueError(f"{assetID}: no measured fact '{fact}'; its facts: {sorted(asset['measured'])}")
      facts[fact] = asset["measured"][fact]
    return {"views": views, "facts": facts}

  def describe(self, descriptions):
    """Write descriptions, all or none, field by field: a field fills only where the asset lacks it and never rewrites what is there,
    unless the description names it in rewrite (to correct a wrong one); each field written records what it was judged from. Returns
    per asset the fields written."""
    vocabulary = self.vocabulary()
    ids = [description.get("id") for description in descriptions]
    if None in ids or len(set(ids)) != len(ids):
      raise ValueError("Each description names its asset by id, and each asset is described once per call")
    interpretations = self.interpretations()
    written = {}
    for description in descriptions:
      asset = self.requireAsset(description["id"])
      fields = self.validatedFields(description, asset, vocabulary)
      rewrite = description.get("rewrite", [])
      if not isinstance(rewrite, list) or set(rewrite) - set(fields):
        raise ValueError(f"{description['id']}: rewrite names fields this description writes, of {sorted(fields)}; got {rewrite!r}")
      provenance = self.judgedFrom(description, asset)
      current = dict(interpretations.get(description["id"], {}))
      existing = {field: value for field, value in current.items() if field != provenanceKey}
      tags = dict(existing.get("tags", {}))
      clashing = []
      for field, value in fields.items():
        group = field.split(".", 1)[1] if field.startswith("tags.") else None
        held = tags.get(group) if group else existing.get(field)
        present = (group in tags) if group else (field in existing)
        if present and held != value and field not in rewrite:
          clashing.append(field)
      if clashing:
        raise ValueError(f"{description['id']}: {clashing} are written already and differ; a field is written once (name it in rewrite to correct a wrong one)")
      changed = []
      records = dict(current.get(provenanceKey, {}))
      for field, value in fields.items():
        group = field.split(".", 1)[1] if field.startswith("tags.") else None
        present = (group in tags) if group else (field in existing)
        if present and field not in rewrite:
          continue
        if group:
          tags[group] = value
        else:
          current[field] = value
        records[field] = provenance
        changed.append(field)
      if tags:
        current["tags"] = dict(sorted(tags.items()))
      current[provenanceKey] = records
      interpretations[description["id"]] = current
      written[description["id"]] = changed
    self.root.mkdir(parents=True, exist_ok=True)
    self.interpretedPath.write_text(json.dumps(interpretations, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    return dict(sorted(written.items()))

  def reviewReasons(self, asset, interpreted):
    """The fields whose judgement rests on what changed since: a view whose image now differs, or a fact whose measured value does."""
    reasons = {}
    files = self.viewFiles(asset)
    for field, record in (interpreted or {}).get(provenanceKey, {}).items():
      why = [f"view {view} changed" for view, digest in record["views"].items() if view in files and files[view].is_file() and hashlib.sha256(files[view].read_bytes()).hexdigest() != digest]
      why += [f"fact {fact} changed" for fact, value in record["facts"].items() if asset["measured"].get(fact) != value]
      if why:
        reasons[field] = why
    return reasons

  def queue(self, kind, field, source, limit):
    """The assets of a kind missing a field (or any field the describe queue tracks for the kind, queueFields), and those whose written
    fields rest on views or facts that changed since (review), each with what to judge it from: its views (present or not) and the
    measured facts."""
    tracked = queueFields(kind)
    if field is not None and field not in tracked:
      raise ValueError(f"A {kind} description's fields are {list(tracked)}; not {field!r}")
    interpretations = self.interpretations()
    entries = []
    for asset in self.assets().values():
      if asset["kind"] != kind or "problem" in asset["measured"]:
        continue
      if source is not None and not any(key == source or key == f"zone:{source.lower()}" or key == f"archive:{source.lower()}" for key in asset["sources"]):
        continue
      interpreted = interpretations.get(asset["id"])
      missing = [name for name in tracked if name not in writtenFields(kind, interpreted)]
      review = self.reviewReasons(asset, interpreted)
      if (field is None and (missing or review)) or (field is not None and (field in missing or field in review)):
        files = self.viewFiles(asset)
        entries.append({
          "id": asset["id"], "name": asset["name"], "sources": sorted(asset["sources"]), "missing": missing, "review": review,
          "views": {view: str(path) if path.is_file() else None for view, path in files.items()},
        })
    entries.sort(key=lambda entry: (-len(entry["review"]), entry["id"]))
    return {"kind": kind, "field": field, "fields": list(tracked), "total": len(entries), "assets": entries[:limit]}

  def entry(self, assetID):
    asset = self.requireAsset(assetID)
    interpreted = self.interpretations().get(assetID)
    return asset | {"interpreted": interpreted, "review": self.reviewReasons(asset, interpreted), "views": {view: str(path) if path.is_file() else None for view, path in self.viewFiles(asset).items()}}

  def find(self, kind, text, categories, tags, source, described, sortBy, limit, colors=None, minimumSide=None, tiles=None, usedOn=None):
    """Assets matching every filter given: kind, words of text (each in the id, name, description, usage, category, tags, or measured
    names and color), any of categories, every tag ({group: [terms]}, each term required), a source (zone name or source key), and
    described or not; and for textures by what was measured: any of colors (the measured color names), a smallest side of at least
    minimumSide pixels, tiling or not, and usedOn, slope bands that together hold at least half the texture's area where it is used most."""
    unknownColors = sorted(set(colors or []) - set(assetSurvey.colorNames))
    unknownBands = sorted(set(usedOn or []) - {band for band, _, _ in assetSurvey.slopeBands})
    if unknownColors or unknownBands:
      raise ValueError(f"colors are among {list(assetSurvey.colorNames)} and usedOn among {[band for band, _, _ in assetSurvey.slopeBands]}; unknown: {unknownColors + unknownBands}")
    interpretations = self.interpretations()
    words = [word for word in re.split(r"\s+", text.lower()) if word] if text else []
    matches = []
    for asset in self.assets().values():
      interpreted = interpretations.get(asset["id"])
      if kind is not None and asset["kind"] != kind:
        continue
      if described is not None and (interpreted is not None) != described:
        continue
      if source is not None and not any(key == source or key == f"zone:{source.lower()}" or key == f"folder:{source}" or key == f"archive:{source.lower()}" for key in asset["sources"]):
        continue
      if categories and (interpreted is None or interpreted.get("category") not in categories):
        continue
      if tags and (interpreted is None or any(not set(terms) <= set(interpreted.get("tags", {}).get(group, [])) for group, terms in tags.items())):
        continue
      measured = asset["measured"]
      if colors and measured.get("colorName") not in colors:
        continue
      if minimumSide is not None and min(measured.get("width", 0), measured.get("height", 0)) < minimumSide:
        continue
      if tiles is not None and measured.get("tiles") != tiles:
        continue
      if usedOn:
        use = mainUse(asset)
        if use is None or sum(use["slopeShares"][band] for band in usedOn) < 0.5:
          continue
      if words:
        haystack = " ".join([asset["id"], asset["name"], asset["measured"].get("colorName", ""), json.dumps(asset["measured"].get("zones", {})), json.dumps(asset["measured"].get("names", []))]
          + ([interpreted.get(field, "") for field in textSearchFields] + [term for terms in interpreted.get("tags", {}).values() for term in terms] if interpreted else [])).lower()
        if not all(word in haystack for word in words):
          continue
        relevance = sum(" ".join(interpreted.get(field, "") for field in textSearchFields).lower().count(word) for word in words) if interpreted else 0
      else:
        relevance = 0
      matches.append((relevance, asset, interpreted))
    sorters = {
      "relevance": lambda match: (-match[0], -largestAreaShare(match[1]), match[1]["id"]),
      "areaShare": lambda match: (-largestAreaShare(match[1]), match[1]["id"]),
      "name": lambda match: match[1]["id"],
    }
    if sortBy not in sorters:
      raise ValueError(f"sortBy is one of {list(sorters)}, got '{sortBy}'")
    matches.sort(key=sorters[sortBy])
    return {"total": len(matches), "assets": [compactEntry(asset, interpreted) for _, asset, interpreted in matches[:limit]]}


def largestAreaShare(asset):
  shares = [facts["uses"]["areaShare"] for facts in asset["sources"].values() if facts.get("uses")]
  return max(shares) if shares else 0.0


def mainUse(asset):
  """The use in the source where the texture covers the largest share of the zone: how big it repeats and on what slopes."""
  uses = [(facts["uses"], key) for key, facts in asset["sources"].items() if facts.get("uses")]
  if not uses:
    return None
  use, key = max(uses, key=lambda pair: pair[0]["areaShare"])
  return {"source": key} | {field: use[field] for field in ("areaShare", "unitsPerRepeat", "slopeShares", "objectShare")}


def compactEntry(asset, interpreted):
  measured = asset["measured"]
  entry = {"id": asset["id"], "kind": asset["kind"], "sources": sorted(asset["sources"])}
  if interpreted is not None:
    entry |= {key: interpreted[key] for key in descriptionKeys if interpreted.get(key) not in (None, [], {})}
  if asset["kind"] == "texture":
    roles = {}
    for facts in asset["sources"].values():
      for role, count in (facts.get("materials") or {}).get("roles", {}).items():
        roles[role] = roles.get(role, 0) + count
    entry |= {key: measured[key] for key in ("format", "colorName", "meanColor", "tiles", "transparentShare", "file") if key in measured}
    entry["size"] = f"{measured['width']}x{measured['height']}" if "width" in measured else None
    entry |= {key: value for key, value in (("mainUse", mainUse(asset)), ("materialRoles", roles)) if value}
  elif asset["kind"] == "model":
    entry |= {key: measured[key] for key in ("size", "footprint", "origin", "triangles", "collisionShare", "skinned", "dominantColors", "textures", "role", "problem") if key in measured}
    entry["placements"] = sum(facts.get("placements", 0) for facts in asset["sources"].values())
    entry["placedIn"] = sorted(key.split(":", 1)[1] for key, facts in asset["sources"].items() if facts.get("placements"))
  elif asset["kind"] == "light":
    entry |= {key: measured[key] for key in ("zone", "count", "colors", "radii", "names", "flickerFrames")}
  elif asset["kind"] == "emitter":
    entry["zones"] = {zone: {"count": facts["count"], "names": facts["names"]} for zone, facts in measured["zones"].items()}
  else:
    entry |= {key: measured[key] for key in ("zone", "layers", "problem") if key in measured}
  return entry
