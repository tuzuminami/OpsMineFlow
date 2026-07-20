# Third-Party License Review

| OSS name | GitHub full path | Use | License | Direct dependency allowed | Code reuse allowed | Commercial use allowed | Adoption | Notes |
|---|---|---|---|---|---|---|---|---|
| ActivityWatch | https://github.com/ActivityWatch/activitywatch | Optional exported data reference | MPL-2.0 | No | No | Yes with MPL terms | optional-import | Do not vendor or copy code. |
| aw-client | https://github.com/ActivityWatch/aw-client | Reference only | MPL-2.0 | No | No | Yes with MPL terms | design-reference-only | Use exported JSON/CSV instead. |
| aw-client-js | https://github.com/ActivityWatch/aw-client-js | Reference only | MPL-2.0 | No | No | Yes with MPL terms | design-reference-only | No direct dependency. |
| aw-server | https://github.com/ActivityWatch/aw-server | Reference only | MPL-2.0 | No | No | Yes with MPL terms | design-reference-only | Optional localhost import only. |
| aw-server-rust | https://github.com/ActivityWatch/aw-server-rust | Reference only | MPL-2.0 | No | No | Yes with MPL terms | design-reference-only | No code reuse. |
| aw-watcher-window | https://github.com/ActivityWatch/aw-watcher-window | Reference only | MPL-2.0 | No | No | Yes with MPL terms | design-reference-only | No native agent reuse. |
| aw-watcher-web | https://github.com/ActivityWatch/aw-watcher-web | Reference only | MPL-2.0 | No | No | Yes with MPL terms | design-reference-only | No browser extension reuse. |
| aw-webui | https://github.com/ActivityWatch/aw-webui | Reference only | MPL-2.0 | No | No | Yes with MPL terms | design-reference-only | No UI code reuse. |
| aw-tauri | https://github.com/ActivityWatch/aw-tauri | Reference only | MPL-2.0 | No | No | Yes with MPL terms | design-reference-only | No code reuse. |
| awesome-activitywatch | https://github.com/ActivityWatch/awesome-activitywatch | Research list | Mixed | No | No | Unknown | design-reference-only | Do not depend on listed projects without review. |
| PM4Py | https://github.com/process-intelligence-solutions/pm4py | Process mining concepts | AGPL-3.0 | No | No | Restricted for distribution | prohibited | Implement algorithms locally. |
| Apromore Core | https://github.com/apromore/ApromoreCore | Reference only | AGPL/GPL family review required | No | No | Restricted for distribution | prohibited | Do not depend on or copy. |
| Apromore Docker | https://github.com/apromore/ApromoreDocker | Reference only | AGPL/GPL family review required | No | No | Restricted for distribution | prohibited | Do not depend on or copy. |
| ProM | https://github.com/promworkbench/ProM | Reference only | GPL family | No | No | Restricted for distribution | prohibited | Do not depend on or copy. |
| awesome-processmining | https://github.com/TheWoops/awesome-processmining | Research list | Mixed | No | No | Unknown | design-reference-only | Review each referenced project separately. |
| RPA_UILogger | https://github.com/apromore/RPA_UILogger | Reference only | Non-commercial/unclear risk | No | No | No or unclear | prohibited | Do not use. |
| screenrpa | https://github.com/RPA-US/screenrpa | Reference only | Non-commercial/unclear risk | No | No | No or unclear | prohibited | Do not use. |
| openrpa | https://github.com/open-rpa/openrpa | Reference only | License review required | No | No | Unclear | design-reference-only | No code reuse in the core product. |
| openflow | https://github.com/open-rpa/openflow | Reference only | License review required | No | No | Unclear | design-reference-only | No code reuse in the core product. |
| rrweb | https://github.com/rrweb-io/rrweb | Future web logging review | MIT | Possibly | Only after privacy design | Yes | design-reference-only | Session replay is excluded from the current product. |
| openreplay | https://github.com/openreplay/openreplay | Reference only | ELv2/Business restrictions risk | No | No | Restricted | prohibited | Avoid direct use. |
| rrweb-server | https://github.com/alokemajumder/rrweb-server | Reference only | Review required | No | No | Unclear | design-reference-only | No product use. |
| drawio-desktop | https://github.com/jgraph/drawio-desktop | Format reference | Apache-2.0 | No | No | Yes | design-reference-only | Generate XML ourselves. |
| drawio | https://github.com/jgraph/drawio | Format reference | Apache-2.0 | No | No | Yes | design-reference-only | Do not vendor. |
| mxgraph | https://github.com/jgraph/mxgraph | Format reference | Apache-2.0 | No | No | Yes | design-reference-only | Do not vendor. |
| drawio-diagrams | https://github.com/jgraph/drawio-diagrams | Format examples | Apache-2.0 | No | No | Yes | design-reference-only | Examples only. |
| vscode-drawio | https://github.com/hediet/vscode-drawio | Reference only | MIT | No | No | Yes | design-reference-only | No code reuse. |
| DuckDB | https://github.com/duckdb/duckdb | Future local analytics | MIT | Yes | Yes | Yes | candidate | Not required for current storage. |
| SQLite | https://github.com/sqlite/sqlite | Local storage | Public domain/blessing | Yes | Yes | Yes | standard-library | Used through Python's standard library. |
| Mermaid | https://github.com/mermaid-js/mermaid | Diagrams | MIT | Yes | Yes | Yes | candidate | Avoid CDN. |
| Apache ECharts | https://github.com/apache/echarts | Charts | Apache-2.0 | Yes | Yes | Yes | candidate | Local bundle only. |
| Apache Arrow | https://github.com/apache/arrow | Future data format | Apache-2.0 | Yes | Yes | Yes | candidate | Not required for the current release. |
| Polars | https://github.com/pola-rs/polars | Future analysis | MIT | Yes | Yes | Yes | candidate | Not required for the current release. |
| pandas | https://github.com/pandas-dev/pandas | Future analysis | BSD-3-Clause | Yes | Yes | Yes | candidate | Not required for the current release. |
| NetworkX | https://github.com/networkx/networkx | Future graph analysis | BSD-3-Clause | Yes | Yes | Yes | candidate | Current implementation avoids this dependency. |
| FastAPI | https://github.com/fastapi/fastapi | Local API | MIT | Yes | Yes | Yes | direct-dependency | Localhost only. |
| Uvicorn | https://github.com/encode/uvicorn | Local API server | BSD-3-Clause | Yes | Yes | Yes | direct-dependency | Bind to 127.0.0.1. |
| Pydantic | https://github.com/pydantic/pydantic | Validation | MIT | Yes | Yes | Yes | direct-dependency | Used in local schema. |
| uv | https://github.com/astral-sh/uv | Developer setup | MIT/Apache-2.0 | Yes | Yes | Yes | candidate | Optional developer tool. |
| Tauri | https://github.com/tauri-apps/tauri | Desktop shell | MIT/Apache-2.0 | Yes | Yes | Yes | direct-dependency | Lock down external URLs. |
| tauri-plugin-dialog | https://github.com/tauri-apps/plugins-workspace | Native open/save and confirmation dialogs | Apache-2.0/MIT | Yes | Yes | Yes | direct-dependency | Rust-only dialog use; WebView receives no arbitrary filesystem path. |
| rfd | https://github.com/PolyMeilex/rfd | Native dialog backend | MIT | Yes | Yes | Yes | transitive-dependency | Pulled by tauri-plugin-dialog. |
| hmac | https://github.com/RustCrypto/MACs | Runtime sidecar proof | MIT/Apache-2.0 | Yes | Yes | Yes | direct-dependency | HMAC-SHA256 only; no network use. |
| React | https://github.com/facebook/react | UI | MIT | Yes | Yes | Yes | direct-dependency | Local bundle. |
| Vite | https://github.com/vitejs/vite | UI build | MIT | Yes | Yes | Yes | direct-dependency | Dev server is local. |
