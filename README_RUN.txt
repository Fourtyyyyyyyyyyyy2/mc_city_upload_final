mc_city GDMC upload package

Formal run:
1. Set the GDMC build area in Minecraft/server first, for example:
   /setbuildarea ~-500 -64 ~-500 ~500 319 ~500
2. Install dependencies from requirements.txt.
3. Run from this folder:
   python -m mc_city.main --rescan

Notes:
- Do not use --at-player for official runs; it is only for local tests.
- Large build areas such as 1000x1000 keep their visual scale but the detailed
  scan is capped to 512x512 by default to avoid timeout.
- Use --no-scan-cap only if you intentionally want to force a full scan.
