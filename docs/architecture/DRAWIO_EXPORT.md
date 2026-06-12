# draw.io Export

OpsMineFlow generates draw.io-compatible mxfile XML directly.

## Required Structure

- `mxfile`
- `diagram`
- `mxGraphModel`
- `root`
- `mxCell id="0"`
- `mxCell id="1" parent="0"`
- Activity nodes.
- Transition edges.

## Node Labels

Nodes include activity name, frequency, average duration, bottleneck flag, and automation candidate flag when available.

## Edge Labels

Edges include transition frequency and average transition duration.

## Layout

The MVP uses deterministic horizontal placement with Start and End nodes. Future versions may add swimlanes by department, app, or anonymized user.

