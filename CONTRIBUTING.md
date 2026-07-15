# Contributing to Flatfinder

Contributions are welcome! The project uses the standard **fork → branch → pull request**
model, and **every change is reviewed and merged by the maintainer** — you can't merge your own
PR, so nothing lands without a look-over.

## How to propose a change

1. **Fork** this repo (top-right *Fork* button) — this gives you your own copy to push to.
2. **Create a branch** for your change:
   ```bash
   git checkout -b fix/clearer-error-message
   ```
3. **Make your change** and run the tests:
   ```bash
   pip install -e ".[dev]"
   pytest -q
   ```
4. **Push** to your fork and **open a Pull Request** against `main` here.
5. The maintainer reviews it, may ask for tweaks, and merges when it's ready. 🎉

You don't need permission to start — just fork and open a PR. For anything large, opening an
**issue** first to discuss is appreciated so you don't build something that won't be merged.

## What makes a PR easy to merge

- **One focused change per PR.** Small is easy to review; sprawling is not.
- **Tests pass** (`pytest -q`) and you've added tests for new behaviour where it makes sense.
- **Match the surrounding style** — no reformatting unrelated code.
- **Explain the "why"** in the PR description, not just the "what".
- **Be polite to SpareRoom** — don't remove request delays or add anything that hammers the site.

## Good first contributions

- Support for other cities / journey-planner APIs (the commute engine is TfL-only today).
- More robust SpareRoom parsing (`src/flatfinder/scraper/parse.py`) when their HTML changes.
- Extra listing-quality signals, better ranking, more tests.

## Ground rules

- Personal / educational use of the tool only; respect SpareRoom's Terms of Service.
- By contributing, you agree your work is licensed under the project's [MIT License](LICENSE).
- Be kind in issues and reviews.
