# Adding the interface images (one manual step)

The three README image references expect these exact files:

| File | Artboard on the design canvas |
|---|---|
| `assets/ui-helio.png` | Helio — Solar Advisory RAG |
| `assets/ui-assetpilot.png` | AssetPilot — Agent Run Console |
| `assets/ui-clearline.png` | Clearline — Data Quality |

**How:** open the "Production AI Systems — Interface Suite" canvas in Claude,
select an artboard, and use its export/download control to save it as PNG
(2× if offered — crisper on retina displays and in README embeds). If no
export control is visible, a full-window screenshot of the focused artboard
at 100% zoom works identically. Name the files exactly as above, drop them
in this folder, then:

```bash
git add assets/*.png && git commit -m "Add interface mockups" && git push
```

Until the PNGs exist, GitHub shows a broken-image icon in four places
(root README gallery + one per project README) — so add them in the same
push as the first public commit, or hold this commit until they're in.
