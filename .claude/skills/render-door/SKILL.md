---
name: render-door
description: Place and render EverQuest doors in a zonewright Blender scene from the client's own models. Doors are every server-placed model, not only doors: lifts, teleport pads, books, banners, furniture, and housing pieces. Use whenever a scene or render needs a zone's doors, one or all of them from a claudelink or live dump.
---

# Render doors

`placeDoor` puts a door model into the open scene, closed, with its model origin at the door's position, as the server places it. The model is found the way the client finds it (see "Client models" in the README; `findModel` shows the search), built once, and cached under the tooling root. The placed door ships with the zone (README, Client models in a zone): a row of EQ's `doors` table by default, or scenery the `.zon` places with `interactive` false. It is the client's mesh: it moves, turns, and scales, and no tool edits it.

## placeDoor

| Argument | Meaning |
|---|---|
| `zone` | The zone the door belongs to; the client loads its archives after its startup archives |
| `model` | The door's name: `POKDOOR500`, `OBJ_TELEPADA`, `IT11202`, ... |
| `location` + `headingDegrees` | Blender position of the model origin and turn (0 = +Y, clockwise from above) |
| `x`, `y`, `z` + `heading` | Or EQ's values as the server and the dumps give them (heading 0 faces EQ +y, 128 faces +x); give these or the Blender pair, not both |
| `scaleFactor` | The door's scale in percent, as the dumps and EQEmu give it (default 100) |
| `source` | `"archive"` or `"archive:entry"` to take a definition other than the first the client loads |
| `name`, `collection` | Object name and collection |
| `interactive` | true (default): a `doors` row naming the model's copy in the archive; false: scenery, placed by the `.zon` |
| `openType` | An interactive door's open type (required): EQ's `doors.opentype`, as the client's own doors of the model use it (the catalog's `role`: 0 and 5 swing, 57 and 58 teleport on a click, 55 and 160 are borders and props) |
| `destination` | `{zone, x, y, z, headingDegrees}` (Blender axes): the door sends the clicker there |
| `keyItem`, `lockpick`, `noKeyring` | What opens a locked door |
| `triggerDoor`, `triggerType` | Another interactive door's object name, which opens with this one |
| `closeTimerMilliseconds` | How long it stays open (default 5000) |

An interactive door turns about Z only and its `scaleFactor` is a whole percent.

Doors are not snapped to the ground: the server's z is the model origin, and measured doors sit on their floors at that z.

## From a claudelink dump

`placeDump` places every visible door of a claudelink zone dump (with its spawns, ground items, and placed objects) at its actor's position, heading, and scale, closed, as reference: they never ship. A hidden door (`visible` false) and one the client draws no actor for are listed with why. To place one by hand, a dump's `doors` row maps to `placeDoor`: `name` is `model`, `y`, `x`, `z`, and `heading` the same names, and `scaleFactor` the same (hundredths; its actor's scale is a hundredth of it).

## From a live dump

`master\doors.tsv` columns map to arguments:

| Column | Argument |
|---|---|
| `name` | `model` |
| `x`, `y`, `z` | `x`, `y`, `z` |
| `heading` | `heading` |
| `scaleFactor` | `scaleFactor` |

Name each object after the zone and door `id` (for example `door_poknowledge_12`) so repeated doors of one model stay distinct. A row whose `visible` is 0 was hidden when captured; leave it out unless asked. A door placed by hand from a dump to render it, not to ship it, is placed with `interactive` false and removed before export.

## Where door models come from

Door models resolve through the zone's links:

- **Classic zones:** `<zone>_obj.s3d`, `_obj2`, `_2_obj`.
- **EQG zones:** the zone `.eqg` and the archives in `<zone>_assets.txt`.
- **PoK:** `poknowledge_obj3.eqg`, which `eqgame.exe` loads for that zone only.

A model may also come from an archive the client loads at startup, which loads before the zone's and so wins a name both define; IT67 is in both `equipment-01.eqg` and `gequip.s3d`, and the client keeps `equipment-01.eqg`, loaded first. `OnDemandResources.txt` entries load last.

## Limits

- **Closed only.** Doors are drawn closed; opening rotations are not applied.
- **Animated doors.** These are posed at frame 0 of their skeleton; their particle effects are not drawn (`particleCloudsNotDrawn` counts them).
- **Missing textures.** A texture missing from every linked archive draws black (the client's effect samples no texture there) and is listed in `missingTextures`.
