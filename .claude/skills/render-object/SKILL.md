---
name: render-object
description: Place and render EverQuest ground objects in a zonewright Blender scene from the client's own models: tradeskill containers (kilns, looms, forges, ovens), ground spawns, dropped items, and housing pieces. Use whenever a scene or render needs a zone's objects, one or all of them from a claudelink or live dump.
---

# Render objects

`placeObject` puts a ground object's model into the open scene, with its model origin at the object's position, as the server places it. The model is found the way the client finds it (see "Client models" in the README; `findModel` shows the search), built once, and cached under the tooling root. The placed object ships with the zone (README, Client models in a zone): scenery the `.zon` places by default, or an object row with `interactive`. It is the client's mesh: it moves, turns, and scales, and no tool edits it.

## placeObject

| Argument | Meaning |
|---|---|
| `zone` | The zone the object is in; the client loads its archives after its startup archives |
| `model` | The object's actor: `IT10800_ACTORDEF` (with or without `_ACTORDEF`), ... |
| `location` + `headingDegrees` | Blender position of the model origin and turn (0 = +Y, clockwise from above) |
| `x`, `y`, `z` + `heading` | Or EQ's values as the server and the dumps give them (heading 0 faces EQ +y, 128 faces +x); give these or the Blender pair, not both |
| `scale` | The object's scale, as the dumps give it (default 1) |
| `source` | `"archive"` or `"archive:entry"` to take a definition other than the first the client loads |
| `name`, `collection` | Object name and collection |
| `interactive` | false (default): scenery, placed by the `.zon`; true: a row of EQ's `object` table naming the model's copy in the archive |
| `objectType` | An interactive object's type: a tradeskill container's kind (`potteryWheel`, `potteryKiln`, `forge`, `brewBarrel`, `tailoring`, `jewelcrafting`, `fletching`, `baking2`, ...) or `staticUnlocked` for a plain clickable object; the catalog's `role` shows what the server's own rows give the model |
| `displayName` | An interactive object's display name |

An interactive object turns about Z only and is scaled 0.01 to 49.99; the server stands it on the zone's collision as it boots, so export refuses one standing off the ground the zone ships.

Objects are not snapped to the ground: the server's z is the model origin.

## From a claudelink dump

`placeDump` places every ground item and placed object of a claudelink zone dump (with its spawns and doors) at its actor's position, heading, and scale, as reference: they never ship. One whose actor is tilted (pitch or roll not 0) is listed, not placed, as placement turns only about Z. To place one by hand, a dump's `groundItems` or `placedObjects` row maps to `placeObject`: its actor's `definition` (or the row's `name`) is `model`, `y`, `x`, `z`, and `heading` the same names, and `scale` the same.

## From a live dump

`master\ground.tsv` columns map to arguments:

| Column | Argument |
|---|---|
| `name` | `model` |
| `x`, `y`, `z` | `x`, `y`, `z` |
| `heading` | `heading` |
| `scale` | `scale` |

Pitch and roll are 0 in every dumped row, so they are not taken. Housing items placed by players are in `placedObjects\<server>.tsv`.

## Where object models come from

Item models (`IT<number>`) come from the first archive the client loads that defines them:

- **Startup archives:** the `GEquip*` and Luclin equipment archives load before any zone, so they win a name a zone's archive also defines.
- **The zone's archives:** the neighborhood's kilns and looms come from `tradeskill_objects.eqg`, which `neighborhood_assets.txt` names.
- **On demand, last:** `OnDemandResources.txt` entries such as `phexterior1a.eqg^IT20026.MOD`.

## Limits

- **Missing textures.** A texture missing from every linked archive draws black (the client's effect samples no texture there) and is listed in `missingTextures`. The neighborhood's `OBJ_TREEM` bark is one: only its normal map ships.
- **Particles.** An object's own particle effects (actor emitters) are not drawn; a zone's environment emitters are.
