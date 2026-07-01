# Soul Tree City Generator Overview

## Concept

This generator builds a ruined fantasy city around a central Soul Tree for the GDMC competition. The city is designed as a once-living settlement founded by four guilds: Scholars, Engineers, Merchants, and Adventurers. The final output is intentionally post-invasion: broken walls, burned buildings, siege camps, hostile armies, defenders, floating dark Eye Kings, sea monsters, and giant colossi create the story through the environment itself.

## Main Pipeline

The entry point is:

```bash
python -m mc_city.main --rescan
```

The expected official workflow is to set the GDMC build area first, then run the generator. The program reads the build area from the GDMC HTTP interface, scans the world, analyzes terrain, chooses a dramatic city center, and builds the city in staged passes.

The high-level stages are:

1. Connect to the GDMC HTTP interface.
2. Pause world ticking where possible.
3. Scan the build area, or a capped central area for very large maps.
4. Generate a height map and terrain feature maps.
5. Select a dramatic but buildable city center.
6. Clear vegetation and prepare the main city area.
7. Place the Soul Tree or small-map Wish Tree.
8. Generate plaza, districts, buildings, landmarks, walls, roads, greenery, and ruins.
9. Resume world ticking.

## Terrain Adaptation

The generator uses a scanned 3D block volume plus a derived height map. The height map treats invalid columns with a sentinel value equal to `min_y`, which prevents later systems from accidentally building at the bottom of the world.

Terrain analysis produces reusable feature layers such as:

- valid terrain mask
- water mask
- slope map
- roughness map
- ridge mask
- terrain style classification (produced separately by `build_terrain_map`)

These layers guide center selection, road planning, building placement, landmark placement, and ruin staging. Buildings and landmarks avoid invalid columns and unsuitable terrain unless a specific fallback system can safely prepare the area.

## Adaptive City Scale

City radii are derived from the available build size. Small maps receive a compact layout with a smaller Wish Tree core, lower walls, and fewer landmarks. Standard maps use the full Soul Tree city. Very large maps, such as 1000x1000 areas, keep their visual scale for monster placement while limiting the detailed scan size to avoid timeout.

For large maps, the detailed scan is capped by default, while `visual_city_dims` preserves the original build area size for dramatic landmark scaling. This keeps the generator within the time budget while still allowing large-map features such as multiple Eye Kings and dark colossi.

## City Structure

The city is organized around a central sacred tree and plaza. Around it, the four guilds occupy districts and buildings. The generator supports both grid-based district placement and older ring-based placement, with the grid mode used for denser city blocks.

Major structural systems include:

- central Soul Tree or Wish Tree
- plaza and main axes
- guild buildings
- commercial streets
- adaptive city walls
- A* or grid street rendering
- terrain-aware road grading
- greenery and ruins

The city is built in layers so later systems can avoid earlier footprints. For example, landmarks reserve their footprint before district blocks are filled, and roads avoid already placed buildings.

## Landmarks and Monsters

Large landmarks are placed by angle and radius around the city center, then adjusted through nearby candidate searches. Each candidate checks footprint bounds, collision, water requirements, terrain suitability, and special monster rules.

Monster landmarks include:

- `eye_king.npy`: floating dark Eye King statues, reskinned to black and placed above the city.
- `bloop_ocean_monster_statue.npy`: a water-only ocean monster, sunk into water so its base is not exposed.
- `dark_colossus.npy`: giant grounded colossi for very large maps, facing the Soul Tree.

Monster statues do not receive artificial platforms. Eye Kings float in the sky, the ocean monster belongs in water, and only the dark colossus is grounded. Colossi can carve nearby terrain to reveal themselves and scatter rubble around their legs.

## Ruin and Narrative Layer

The narrative is primarily environmental rather than text-based. The ruin pass turns the city into an invaded settlement:

- Soul Tree damage and fire
- broken wall breaches
- scorched ground
- damaged buildings
- siege camps
- invader and defender mobs
- fireballs and lightning effects

Invaders placed near shorelines are adjusted to nearby land so they do not spawn underwater. If no valid land point is found nearby, the spawn is skipped.

Optional text narrative systems also exist, including signs, street names, and books, but the current competition-facing result emphasizes visible environmental storytelling.

## Large Map Strategy

Full 1000x1000 scans are too slow and may exceed the competition time limit. The generator therefore separates detailed scan size from visual map scale:

- detailed scan: capped central terrain scan, default 512x512
- visual scale: original build area dimensions, used to trigger large-map monster composition

This allows the generated city to remain practical while still reading as a large-scale ruined invasion scene. On a 1000x1000 map, the generator can attempt multiple dark colossi and a spread-out ring of Eye Kings without scanning the entire area.

## Safety and Robustness

The generator is designed to fail gracefully:

- scan caches are invalidated when scan parameters change
- broken raw scan caches are detected and rebuilt
- HTTP placement failures avoid updating internal state incorrectly
- invalid height-map columns are filtered before placement
- landmarks skip unsafe candidates instead of forcing impossible placement
- optional narrative passes log warnings instead of crashing the main build

This makes the generator more reliable across different Minecraft worlds and build areas.
