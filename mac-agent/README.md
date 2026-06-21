# mac-agent

The native macOS technical preview is a Swift-only local helper built by `./scripts/install_mac.sh`. It remains stopped by default and runs only while a user-controlled WebUI recording session is active.

The helper observes the frontmost application display name and bundle identifier, then sends app intervals to the authenticated localhost API. It never writes directly to SQLite. Window titles, URLs, keystrokes, passwords, input text, clipboard contents, screenshots, screen recordings, microphone input, camera input, and hidden collection are prohibited.

Build or type-check it directly:

```bash
./scripts/build_mac_agent.sh
./scripts/build_mac_agent.sh --check
```

See [docs/product/COLLECTION_ROADMAP.md](../docs/product/COLLECTION_ROADMAP.md).
