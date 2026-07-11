# mc_city — GDMC 2026 Submission

Procedural "soul-tree city-state" generator for the
[Generative Design in Minecraft (GDMC)](https://gendesignmc.wikidot.com/) 2026
settlement-generation competition. It grows a ruined city around a giant soul
tree, adapting buildings, roads and landmarks to the surrounding terrain.

##  Generated map (GDMC 2026 submission)

The four official evaluation areas were generated on the single official map
(Java Edition **1.21.11**, seed `-1846931519`, default world type). Download the
resulting world here:

**[Download the generated map (Dropbox)](https://www.dropbox.com/scl/fi/8yjx28r7g2rlitiytt7mi/GDMC2026.zip?rlkey=n02ys4jux12cnh1971lkt14jb&st=6jdirok0&dl=0)**

Areas built:

- The Caldera
- Swamp Lake
- Sunflower Plains & Cherry Hill
- Savanna Valley


##  Running the generator

See **[README_RUN.txt](README_RUN.txt)** for full setup/run steps and
**[GENERATOR_OVERVIEW.md](GENERATOR_OVERVIEW.md)** for how it works.

Quick start:

```
pip install -r requirements.txt
# set the GDMC build area in-game first, then run from this folder:
python -m mc_city.main --rescan
```

Requires the GDMC HTTP Interface mod running with a world open (connects to
`http://127.0.0.1:9000`). Total generation stays within the ~10-minute
competition budget; detailed scan is capped to 512×512 on very large areas.
