# Releasing

## Ship a user-facing feature

When you add or change a feature users can see (a new keybinding, a new source,
a new mode), update **three** places so nothing drifts:

1. **Code + README** in this repo — implement it, document it, commit, push.
2. **Landing page** — `claude-grep-site/` (deployed to https://claude-grep.vercel.app).
3. **`llms.txt`** — the AI-readable summary in `claude-grep-site/`.

The landing page lives in a **separate folder** (not this repo):
`~/Documents/nemo-lab.nosync/claude-grep-site/`. It is plain static HTML
deployed to Vercel — there is no build step for the page itself.

## Keep the landing page in sync (enforced)

`claude-grep-site/features.json` is the single source of truth for the
user-facing feature list. A guardrail prevents the page from silently falling
behind the product:

```sh
cd ~/Documents/nemo-lab.nosync/claude-grep-site
node check-features.mjs        # ✓ / ✗ — is every feature mentioned on the page + llms.txt?
./deploy.sh                    # runs the check, then `vercel --prod` (blocks on drift)
```

So the workflow when you ship a feature is:

1. Add an entry to `claude-grep-site/features.json` (key, name, and an `expect`
   phrase that must appear on the page).
2. Write the matching copy into `index.html` and `llms.txt`.
3. Run `./deploy.sh`. If you forgot a place, the check fails and the deploy is
   refused, telling you exactly which file is missing which phrase.

## Refresh the demo

The hero/demo media is generated from a screen recording (`demo_main.mov`):

```sh
SITE=~/Documents/nemo-lab.nosync/claude-grep-site
# compressed, muted, faststart mp4 for the <video> on the site
ffmpeg -y -i demo_main.mov -an -vf "scale=1280:-2:flags=lanczos" \
  -c:v libx264 -profile:v high -pix_fmt yuv420p -crf 30 -preset veryslow \
  -movflags +faststart "$SITE/demo.mp4"
# a populated poster frame
ffmpeg -y -ss 12 -i demo_main.mov -frames:v 1 -vf "scale=1280:-2:flags=lanczos" "$SITE/demo.png"

# the GitHub README uses an inline GIF instead (no <video> on GitHub):
ffmpeg -y -i demo_main.mov -vf "fps=12,scale=1100:-1:flags=lanczos,palettegen=stats_mode=diff" /tmp/palette.png
ffmpeg -y -i demo_main.mov -i /tmp/palette.png \
  -lavfi "fps=12,scale=1100:-1:flags=lanczos[x];[x][1:v]paletteuse=dither=bayer:bayer_scale=3" demo.gif
```
