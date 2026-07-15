# Maintaining & publishing Flatfinder

Everything you need to run the project on GitHub: making changes, cutting releases,
accepting contributions, and how updates reach your users. For the "Windows warns me"
/ code-signing topic see **[PACKAGING.md](PACKAGING.md)**.

Repo: **https://github.com/TonyClone/spareroom-commute-finder** (private until you flip it public).

---

## 0. Your identity is already set (keep it that way)

This repo's git identity is pinned to a pseudonym, so commits never leak your real name/email:

```
user.name  = TonyClone
user.email = 175243532+TonyClone@users.noreply.github.com
```

It was set **repo-local** (`git config user.email …` inside this folder), so it doesn't affect
your other projects. Commits carry **no "Co-Authored-By" / tooling attribution**. If you ever
re-clone, set the same two values again before committing.

---

## 1. Go public + finish the listing (one time, when ready)

1. **Topics** (repo → ⚙ next to *About* → *Topics*), paste:
   `spareroom london flatshare house-share rooms-to-rent flatmate commute tfl transport-for-london journey-planner apartment-hunting web-scraping python cli housing property-search relocation`
2. Set the *About* → *Website* blank, tick "Releases" so the sidebar shows them.
3. **Flip to public:** *Settings → General → Danger Zone → Change visibility → Public.*

---

## 2. Day-to-day: make a change

Because the identity is baked in, your loop is just:

```bash
git checkout -b my-change        # optional but tidy
# ...edit code...
pytest -q
git add -A
git commit -m "Short description of the change"
git push                          # or: git push -u origin my-change  then open a PR to yourself
```

Pushing straight to `main` is fine for a solo maintainer. Using a branch + PR (even for your own
work) gives you the CI + a diff to eyeball — your call.

---

## 3. Accept contributions (fork → PR → you approve)

Outside contributors **can't push to your repo** — they fork it and open a Pull Request. Only you
can merge, so nothing lands without your approval. That flow is documented for them in
**[CONTRIBUTING.md](CONTRIBUTING.md)**, and every PR gets the checklist in
`.github/PULL_REQUEST_TEMPLATE.md`.

When a PR comes in: read the diff → click **Files changed** → leave comments or **Approve** →
**Merge** (Squash is tidiest). If CI is set up, it runs on the PR automatically.

**Recommended hardening** (optional, *Settings → Branches → Add branch ruleset* for `main`):
- ✅ *Require a pull request before merging* — forces every change through a PR.
- ✅ *Require approvals: 1* — a PR must be approved before merge.
- Leave *Allow administrators to bypass* / add yourself as a bypass actor if you still want to
  push small fixes directly to `main`. Otherwise you'll PR everything, including your own work.

For a solo project this is optional — the fork model already means you approve everything. The
ruleset just makes it enforced.

---

## 4. Cut a new release (this is what enables auto-update)

**The easy way — one command:**

```bash
python scripts/release.py 0.1.1
```

That bumps the version, commits `Release v0.1.1`, tags it, and pushes — which triggers CI to build
the binaries and publish the GitHub Release. It refuses to run on a dirty tree, so commit your work
first. That's the whole release; everything below is just what it does under the hood.

<details><summary>The manual equivalent (if you ever want to do it by hand)</summary>

1. **Bump the version** in `src/flatfinder/__init__.py` (single source of truth — `pyproject.toml`
   reads it automatically):
   ```python
   __version__ = "0.1.1"
   ```
2. **Commit** it: `git commit -am "Release v0.1.1"`
3. **Tag and push the tag:**
   ```bash
   git tag v0.1.1
   git push origin main --tags
   ```
4. That tag push triggers **`.github/workflows/release.yml`**, which builds the Windows + macOS
   executables and **creates the GitHub Release** with them attached. Watch it under the **Actions**
   tab; the Release appears under **Releases** when it's done.

> The tag (`v0.1.1`) must be **higher** than what users have installed for auto-update to offer it.
> Keep tag and `__version__` in sync.

**Prefer clicking?** *Releases → Draft a new release → Choose a tag → type `v0.1.1` → Create new
tag → Generate release notes → Publish.* Publishing creates the tag, which triggers the same build.

**Have the `gh` CLI?** `gh release create v0.1.1 --generate-notes` does steps 3–4 in one line.

</details>

---

## 5. How updates reach users (the "no going back to GitHub" part)

Your users never have to re-download by hand:

- **In the app:** menu option **9 · Update** (or `flatfinder update`). It asks GitHub for the latest
  release, and if it's newer, downloads and installs it in place — **keeping their `config.yaml`,
  `.env` keys and `data/` seen-list untouched**. They just reopen the app.
- **For you (a git checkout):** `flatfinder update` detects git and tells you to `git pull`, so it
  never clobbers your local edits. `git pull` is your update.

So the release cadence is: you push a tag → CI publishes the release → users hit **Update** (or it's
picked up next time they run `flatfinder update`). No manual downloads, no visiting GitHub.

> Note: "no going back to GitHub" means the *user* doesn't — the app still fetches the release from
> GitHub's API over the network under the hood. It needs internet at update time (not to run).

---

## 6. The launcher icon

The custom icon lives at `assets/flatfinder.ico` and is applied to the **desktop shortcut** created
by `Create Desktop Shortcut.bat` (a `.bat` file itself can't carry an icon on Windows — the shortcut
is what shows it). Because `assets/` ships in every release, **the icon survives auto-updates** — an
update overwrites it with the same file, and the shortcut keeps pointing at the stable path, so it
never disappears. If you change the icon, replace that file and cut a release.

---

## 7. Quick reference

| I want to… | Do this |
|---|---|
| Make a change | edit → `pytest -q` → `git commit -am "…"` → `git push` |
| Release it to users | bump `__version__` → `git tag vX.Y.Z` → `git push origin main --tags` |
| Let someone contribute | they fork + PR; you review + merge (see CONTRIBUTING.md) |
| Update your own copy | `git pull` |
| Update a user's copy | menu **9 · Update** / `flatfinder update` |
| Remove the Windows warning | see PACKAGING.md (SignPath free OSS signing) |
