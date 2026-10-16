---
name: describe-assets
description: Survey and describe EverQuest client graphical assets (textures, models, light styles, emitters, terrain ecosystems) in zonewright's asset catalog, so later work finds them by need instead of reinterpreting them. Use when cataloging a zone's assets, when a search turns up undescribed candidates, or when a description is wrong.
---

# Describing assets

The asset catalog (`docs/assetCatalog.md`) has two lanes. The measured lane comes from the files (`surveyAssets`) and is never written by hand: sizes, colors, how the client's zones use an asset, and a model's in-game role from the server's own tables. The interpreted lane is what an asset *is*, written once with `describeAssets` in the catalog's own words, so every later search reads the same language. A description is worth writing only from a real look at the asset; never describe from a name alone.

Descriptions cost tokens, so nothing is described twice:

- **Work from the queue.** `describeQueue` with the kind (and a `field`, a `source`) lists the assets missing something and those to review. Describe only what it lists.
- **Fill, don't rewrite.** A field already written stays; send only the missing fields. A field that is wrong is corrected by naming it in `rewrite`, with the reason in your own notes, never by sending everything again.
- **Say what you judged from.** Every description carries `judgedFrom`: the views you looked at (`thumbnail` for a texture; `front`, `side`, `top`, `threeQuarter` for a model, rendered by `viewModels`) and the measured facts you used (`size`, `origin`, `dominantColors`, `role`, ...). A field whose views or facts later change comes back in the queue's `review`; look again at those only.

## Procedure

1. **Survey the source** with `surveyAssets` (a zone, an object, on-demand, or startup archive such as `crystals.eqg` or `tradeskill_objects.eqg`, a client image folder, or an exported .eqg). It is cached; surveying again is cheap.
2. **Read the vocabulary** with `getAssetVocabulary` once per session. Categories are per kind; tag groups are shared: material, color, tone, biome, style, use, pattern, condition, scale, quality.
3. **Look in batches.** `viewTextures` with `source`, `described: false`, `sortBy: areaShare` shows the zone's most-used undescribed textures, 48 at a time. Look again with `tiled: true` to judge seams, `showAlpha: true` for cutouts, and `cellSide: 256` on the few that matter. For models use `viewModels`.
4. **Read the measured facts** for the batch with `findAssets` (or `getAsset` for one): `mainUse` (the zone where it covers the most area, its share, `unitsPerRepeat`, and slope bands), `materialRoles` (diffuse, normal, other properties), `tiles` and `seamRatios`, `meanColor` and `colorName`, `ecosystemLayers` (the slope and height rules an EQ terrain zone applies it under). These say how the original artists used it; your eyes say what it is.
5. **Write descriptions** with `describeAssets`, a batch at a time (all or none):
   - `category`: the narrowest category that fits.
   - `tags`: every group that clearly applies, and only those. Color and tone come from what you see in the client's light, not the raw mean color.
   - `description`: one or two sentences: what it depicts, its colors and pattern, how it reads at a distance.
   - `usage`: where it goes (surfaces, slopes, heights), the repeat that reads right, what it pairs with, and what to avoid.
   - `worldUnitsPerRepeat` for textures: start from the measured `unitsPerRepeat` and correct it by eye against a 6-unit player (a pebble is under 1 unit, a boulder 5 to 15, a cliff band 50 or more).
   - `pairsWith`: its normal map and transition partners, by id. A normal map gets category `normalMap`, a short description, and `pairsWith` its diffuse.
   - `judgedFrom`: the views and facts the batch was judged from.
6. **Grow the language sparingly.** When no term fits, `extendAssetVocabulary` with a precise meaning. Prefer an existing term that is close enough; a vocabulary with near-synonyms splits searches.

## Other kinds

- **Lights** (`findAssets` kind `light`): a style is one name stem in one zone, with its colors, radii, and counts. Describe what it lights and how (torch on a wall, brazier fill, cool magic glow) and the spacing and radius that suit it.
- **Emitters** (kind `emitter`): a client definition index with the names zones place it under. Describe only what the names and their placements make certain; leave the rest undescribed until it can be seen.
- **Models**: look with `viewModels` and `views` `["front", "side", "top", "threeQuarter"]` (each model's views side by side; rendered once and kept), read `getAsset`'s measured facts (`size` against the 6-unit player, `origin`: base stands on the ground, wallBack hangs on a wall, hanging hangs from above; `dominantColors`; `role`: whether the server's tables make it a door, a teleport, a tradeskill container, or scenery; per zone `placements`, `sits`, `beside`), and write its fields:
  - `plainName`: what a player would call it ("coldain house door", "violet crystal spire");
  - `category`: the narrowest model category (structure pieces, `furniture`, `workstation`, `door`, `container`, `lightSource`, `sign`, `prop`, `decoration`, vegetation, `rock`, ...);
  - `tags`: `form` (silhouette and how it stands), `material` and `finish`, `style` (culture), `era` (expansion), `biome` (setting), `mood`, `sceneRole` (focal, filler, detail; setPiece and variant for sets), `function`;
  - `description` and `usage`; `dressingNotes`: how the client's artists used it (the zones, how often, scale range, how it sits, what it stands beside, from the measured use); `pairsWith`: its set and variants, by id.
- **Ecosystems**: describe the ground the recipe makes and when to copy its slope and height rules.

## Quality

Rate `quality` honestly for a 2011-era zone: `good` holds up, `usable` needs care (limited repeat, distance, or pairing), `poor` is dated or flawed. A low-resolution or blurry texture is `usable` at best.
