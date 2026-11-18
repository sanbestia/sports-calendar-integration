# Rules for work on this repo

- Each commit that adds a feature, or changes how the repo is structured (a module or a
  folder that is added, moved or removed), also updates `README.md` in the same commit.
  The README is the only user guide.
- A merge to `main` deploys to the production VM (`.github/workflows/deploy.yml`). Merge
  a PR only when the user asks to merge that PR. "Accept" is not a merge.
- Before you start the next item of a list, ask whether the PR of the previous item
  should merge, so that the new branch starts from a `main` that has that work.
