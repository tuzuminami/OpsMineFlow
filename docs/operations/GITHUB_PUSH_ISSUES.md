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
