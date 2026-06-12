# GitHub Push Issues

## 2026-06-12 20:xx JST

Command:

```bash
git push -u origin main
```

Remote:

```text
git@github.com:tuzuminami/OpsMineFlow.git
```

Result:

```text
Host key verification failed.
fatal: Could not read from remote repository.
```

Status:

- Local commits are complete on `main`.
- Push is blocked before repository authentication because the SSH host key is not trusted in this environment.
- Next action is to verify GitHub SSH host keys and add the trusted key to `~/.ssh/known_hosts`, or push from an environment where GitHub SSH is already trusted.

## 2026-06-13 Resolution

Resolution:

- Switched `origin` from SSH to HTTPS:
  `https://github.com/tuzuminami/OpsMineFlow.git`
- Confirmed `gh auth status` was already authenticated as `tuzuminami`.
- Pushed successfully with:
  `git push -u origin main`

Result:

- Remote repository: `https://github.com/tuzuminami/OpsMineFlow`
- Default branch: `main`
- Published commit at resolution time: `a7dc5ba`

Note:

- SSH host key trust is still a separate local environment issue if SSH push is needed later.
- For this repository, HTTPS + GitHub CLI authentication is now the working publish path.
