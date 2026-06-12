from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from xml.etree import ElementTree


def build_drawio_xml(process_map: dict[str, Any], diagram_name: str = "OpsMineFlow Process Map") -> str:
    mxfile = ElementTree.Element(
        "mxfile",
        {
            "host": "app.diagrams.net",
            "modified": datetime.now(timezone.utc).isoformat(),
            "agent": "OpsMineFlow",
            "version": "0.1.0",
        },
    )
    diagram = ElementTree.SubElement(mxfile, "diagram", {"id": "opsmineflow-process-map", "name": diagram_name})
    graph = ElementTree.SubElement(
        diagram,
        "mxGraphModel",
        {
            "dx": "1200",
            "dy": "800",
            "grid": "1",
            "gridSize": "10",
            "guides": "1",
            "tooltips": "1",
            "connect": "1",
            "arrows": "1",
            "fold": "1",
            "page": "1",
            "pageScale": "1",
            "pageWidth": "1600",
            "pageHeight": "900",
            "math": "0",
            "shadow": "0",
        },
    )
    root = ElementTree.SubElement(graph, "root")
    ElementTree.SubElement(root, "mxCell", {"id": "0"})
    ElementTree.SubElement(root, "mxCell", {"id": "1", "parent": "0"})

    nodes = list(process_map.get("nodes") or [])
    edges = list(process_map.get("edges") or [])
    node_ids = _node_ids(nodes)

    _add_vertex(root, "start", "Start", 40, 120, "ellipse;whiteSpace=wrap;html=1;fillColor=#d5e8d4;strokeColor=#82b366;")
    _add_vertex(root, "end", "End", 240 + 220 * (len(nodes) + 1), 120, "ellipse;whiteSpace=wrap;html=1;fillColor=#f8cecc;strokeColor=#b85450;")

    for index, node in enumerate(nodes, start=1):
        activity = str(node["activity"])
        labels = [activity, f'freq {node.get("frequency", 0)}']
        if node.get("average_duration_seconds") is not None:
            labels.append(f'avg {float(node["average_duration_seconds"]):.0f}s')
        if node.get("bottleneck"):
            labels.append("bottleneck")
        if node.get("automation_candidate"):
            labels.append("automation")
        style = "rounded=1;whiteSpace=wrap;html=1;arcSize=8;fillColor=#dae8fc;strokeColor=#6c8ebf;"
        if node.get("bottleneck"):
            style = "rounded=1;whiteSpace=wrap;html=1;arcSize=8;fillColor=#fff2cc;strokeColor=#d6b656;"
        if node.get("automation_candidate"):
            style += "fontStyle=1;"
        _add_vertex(root, node_ids[activity], "\n".join(labels), 220 * index, 110, style)

    start_activities = process_map.get("start_activities") or {}
    end_activities = process_map.get("end_activities") or {}
    edge_index = 1
    for activity in start_activities:
        if activity in node_ids:
            _add_edge(root, f"edge_start_{edge_index}", "start", node_ids[activity], "start")
            edge_index += 1
    for edge in edges:
        source = str(edge["source"])
        target = str(edge["target"])
        if source not in node_ids or target not in node_ids:
            continue
        width = 2 + min(int(edge.get("frequency", 1)), 6)
        label = f'{edge.get("frequency", 0)} / avg {float(edge.get("average_transition_seconds", 0)):.0f}s'
        _add_edge(root, f"edge_{edge_index}", node_ids[source], node_ids[target], label, width=width)
        edge_index += 1
    for activity in end_activities:
        if activity in node_ids:
            _add_edge(root, f"edge_end_{edge_index}", node_ids[activity], "end", "end")
            edge_index += 1

    return ElementTree.tostring(mxfile, encoding="unicode")


def _node_ids(nodes: list[dict[str, Any]]) -> dict[str, str]:
    return {str(node["activity"]): f"activity_{index}" for index, node in enumerate(nodes, start=1)}


def _add_vertex(root: ElementTree.Element, cell_id: str, value: str, x: int, y: int, style: str) -> None:
    cell = ElementTree.SubElement(
        root,
        "mxCell",
        {
            "id": cell_id,
            "value": value,
            "style": style,
            "vertex": "1",
            "parent": "1",
        },
    )
    ElementTree.SubElement(
        cell,
        "mxGeometry",
        {"x": str(x), "y": str(y), "width": "160", "height": "80", "as": "geometry"},
    )


def _add_edge(
    root: ElementTree.Element,
    cell_id: str,
    source: str,
    target: str,
    value: str,
    width: int = 2,
) -> None:
    cell = ElementTree.SubElement(
        root,
        "mxCell",
        {
            "id": cell_id,
            "value": value,
            "style": f"endArrow=block;html=1;rounded=0;strokeWidth={width};",
            "edge": "1",
            "parent": "1",
            "source": source,
            "target": target,
        },
    )
    ElementTree.SubElement(cell, "mxGeometry", {"relative": "1", "as": "geometry"})

