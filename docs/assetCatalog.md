# Asset catalog

The asset catalog holds the EverQuest client's graphical assets in a form zone building can search and use: what each asset measurably is, read from the client's files, and what it was judged to be, written once in a shared vocabulary. It works in both directions. A need ("red sandstone for cliffs") finds assets, and an asset finds its description. Assets flow from client files into the catalog and into Blender, and back out through `exportZone` into EQ files that the catalog can survey like any client zone.

## Sources

A source is surveyed with `surveyAssets`:

| Source | Key | Holds |
|---|---|---|
| A client zone | `zone:<name>` | The textures of the archives the client loads for the zone's own geometry and objects (its zone links less character archives, in load order), its placed models, its light styles, its emitters, and, for an EQ terrain zone, its ecosystems |
| A client object, on-demand, or startup archive | `archive:<name>` | Every model (`.mod`) and texture of an EQG archive the client links that is no zone's: loaded at startup (`equipment-01.eqg`), named in `Resources/OnDemandResources.txt` (houses, furniture), or named in some zone's `<zone>_assets.txt` (`crystals.eqg`, `tradeskill_objects.eqg`), with how it links |
| A client image folder | `folder:<folder>` | The loose images in `Resources/Sky`, `Resources/WaterSwap`, `Resources/Precipitation`, or `EnvEmitterEffects` |
| An EQG zone archive | `file:<path>` | An archive outside the client, such as one `exportZone` wrote, with the emitter list beside it |

A zone that ships both a classic and an EQG version is surveyed as the EQG one, which the client loads ([clientRendering.md](clientRendering.md#which-files-load)).

## Asset kinds and ids

| Kind | Id | One asset is |
|---|---|---|
| texture | `texture/<name>@<hash>` | One image's content; the same image in many archives and zones is one asset |
| model | `model/<name>@<hash>`: an EQG model's entry and its bytes' hash, a classic actor's name and a hash of the fragments it draws | One model definition |
| light | `light/<zone>/<style>` | The lights a zone places under one name stem; classic lights, whose names say nothing, are grouped by radius and color |
| emitter | `emitter/<definition>` | One client emitter definition, with the names every zone places it under |
| ecosystem | `ecosystem/<zone>/<name>` | An EQ terrain zone's ground recipe |

Hashes are of the content (the first 8 hex digits of its SHA-256), so a description stays with its asset and a changed asset is a new one.

## The measured lane

Measured facts are rebuilt when a source's files change (by size and modification time) or when the survey's method changes (`surveyVersion` in `server/assetSurvey.py`).

Each texture carries:

- **The image:** size, format (DDS FourCC or pixel layout, or the image type), transparent and partial-alpha shares, mean color (sRGB 0-255) and a plain color name, brightness, contrast.
- **Tiling:**
  - `seamRatios`, across and down: how much the opposite edges differ against the 90th percentile of neighboring columns or rows inside the image;
  - `tiles` when both ratios are at most 1.5. Seamless textures measure 0.9 to 1.2; unique maps and atlases 2 to 5.
- **Each source's use** (`uses`):
  - its share of the zone's textured area;
  - world units per texture repeat, the square root of world area over UV area;
  - slope bands: flat under 15°, slope 15 to 45°, steep 45 to 75°, vertical 75 to 105°, overhang beyond;
  - its share on placed objects;
  - its surface kinds.
- **EQG materials:**
  - the properties the texture fills (`e_TextureDiffuse0`, `e_TextureNormal0`, ...);
  - the shaders drawing it;
  - the textures it shares materials with;
  - the models that use it.
- **Classic materials:** render methods and animation frames.
- **EQ terrain recipes:** the ecosystem layers that use it, with their slope and height ranges and how many world units each repeat spans (the terrain tile's side over the layer's repeat count).
- **Files:** a readable copy under `catalog/textures/<hash>/` (used by materials and export) and a thumbnail under `catalog/thumbnails/`.

Models carry (`server/modelMeasures.py`):

- **The model:** size (across x, along y, up z) and footprint over its drawn vertices, its bounds, where its origin sits (`base`: it stands on it; `hanging`: it hangs from it; `wallBack`: on one of its sides, a wall piece; `center`; `offset`), drawn triangles, the share of them players collide with, skinning and bones, its drawn materials with their shaders and textures, each diffuse texture's share of its drawn area, and its dominant colors (those textures' measured colors, weighed by area). A classic actor carries its archive and kind.
- **Its use in each client zone that places it:** how often, the scale range, how its placements sit (upright, or tilted about a horizontal axis), and the models most often placed within 30 units of it.
- **Its archive's links** (archive sources): startup, on-demand, and the zones whose `_assets.txt` names it.
- **Its in-game role** (`role`), as the server's own tables hold it: the `doors` rows naming it (open types, how many teleport, need a key, or trigger another door, and their zones) and the `object` rows (their types by name: a tradeskill container's kind, or a plain clickable object), with its `kinds` (`door`, `teleportDoor`, `tradeskillContainer`, `clickableObject`, or `scenery` when no row names it). It comes from `catalog/serverRoles.json`, an extract of claudeperidot's copy of Peridot's database (`server/serverRoleExtract.py` holds the read-only query and turns its output into the file), never written by hand.
- **Its views:** `viewModels` with `views` renders the front (toward its +X front), side, top, and three-quarter views once per model content, kept under the tooling root. Light styles carry counts, colors, radii, names, flicker frames, and example positions. Emitters carry per zone the count, names, lifespans, and example positions. Ecosystems carry their layers.

## The interpreted lane

`describeAssets` writes, for each asset:

- `category`: one of the kind's categories;
- `tags`: `{group: [terms]}` from the shared tag groups (material, color, tone, biome, style, use, pattern, condition, scale, quality, and for models form, finish, era, mood, sceneRole, function);
- `description`: what it shows and how it reads;
- `usage`: where and how to use it;
- `worldUnitsPerRepeat`: for textures;
- `plainName` and `dressingNotes` (how the client's artists used it): for models;
- `pairsWith`: the ids of related assets (a model's set and variants).

Descriptions are written once and last (`server/assetCatalog.py`):

- **Field by field.** A field fills only where the asset lacks it; a different value for a field already written is refused unless the description names it in `rewrite`, to correct a wrong one. A model's tag groups are each a field of their own.
- **By content.** An asset's id carries its content's hash, so a description follows its asset into every zone and archive that ships it, and a changed asset is a new one; a description is never dropped.
- **What remains.** `describeQueue` lists the assets of a kind missing a field (or any), so describing goes only where something is missing.
- **What it was judged from.** Each field written records the views it was judged from (a texture's thumbnail, a model's views) by their images' SHA-256 and the measured facts by their values (`judgedFrom`); when a later survey or render changes one, `describeQueue` and `getAsset` list the fields resting on it for review (`review`).

Every word is checked against the vocabulary (`server/assetVocabulary.py`, plus terms added with `extendAssetVocabulary`), so descriptions stay searchable in one language. The `describe-assets` skill gives the procedure, and `use-assets` covers turning descriptions into a zone.

## Storage

What is worth keeping and sharing is committed in the repository's `catalog/` folder; client content and what only this machine uses stay under the tooling root (`%LOCALAPPDATA%\zonewright\catalog`):

| Path | Where | Holds |
|---|---|---|
| `measured/<source>.json` | Repository | A client zone's or image folder's measured lane, identified by its files' SHA-256 and free of machine paths, so it reads the same on every machine with the same client |
| `interpreted.json` | Repository | The descriptions |
| `vocabulary.json` | Repository | Terms added to the vocabulary |
| `serverRoles.json` | Repository | Each model name's in-game role from the server's doors and object tables |
| `textures/`, `thumbnails/` | Tooling root | Extracted images, by content hash; a survey re-extracts them when they are missing |
| `modelViews/` | Tooling root | Each model's rendered views, by content hash |
| `measured/<source>.json` | Tooling root | Measurements of zone archives outside the client, such as exports |

## Lights and emitters in Blender

A zone light is a point light whose `eqRadius` property holds the EQ radius, colored with the EQ RGB (0-1). A particle emitter is an empty whose `eqEmitterDefinition` and `eqEmitterLifespan` properties hold the client definition index and the list's lifespan field, and `eqEmitterAlwaysVisible` the seventh field some lists add. `placeLights` and `placeEmitters` make them, and `sprayWater` makes a water body's white water emitters, marked with their body (`zonewrightWaterSpray`) so they are placed again with it and go when it goes. `importZone` and `importZoneFile` bring a zone's own lights and emitters in as these objects, and `exportZone` writes them out:

- lights into the .zon, each at (field 2, -field 1, field 3) of its three position fields, as the client places it ([clientRendering.md](clientRendering.md#eqg-zones));
- emitters into `<zone>_EnvironmentEmitters.txt` beside the archive, which the client reads loose: after a header line, each line's fields by position, whatever the header says (`eqgame.exe` `0x4a1c30`, `0x4a1e00`): name, the `EnvironmentEmittersNew.edd` definition index, x, y, z in the zone's axes, the lifespan in milliseconds (the client makes an emitter only when it is above 0 and the index names one of its definitions, [clientRendering.md](clientRendering.md#particle-emitters)), and an optional seventh field, always visible when not 0. The client's seven-field lists head their last two fields `AlwaysVisible^Lifespan` and hold the lifespan (4000000 on most) in the first of them; export writes the fields in the order the client reads them.

A client-shaded view draws both as the client does ([clientRendering.md](clientRendering.md#point-lights)): a light's light on what it reaches (terrain takes only `LIB_` lights, placed objects and characters every light), and an emitter's particles from its client definition.

## Not read yet

- **EQ terrain zones:** their lights, if they have any outside the .zon.
- **Emitter definitions in the catalog:** an emitter is known by its index and the names zones give it; the definitions themselves (read for views by `eqEmitterDefinitions`) are not surveyed.
- **Classic objects' textures:** their use on placed objects (the zone's own meshes are measured).
- **Measurements taken before the readers' fixes:** the light styles' example positions in `catalog/measured` were measured at the raw .zon fields, and the lifespans of seven-field emitter lists from their seventh field; `surveyAssets` with `allZones` and `refresh` measures them again.
