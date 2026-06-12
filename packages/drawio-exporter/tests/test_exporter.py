from __future__ import annotations

import unittest
from pathlib import Path
from xml.etree import ElementTree

from opsmineflow_drawio import build_drawio_xml
from opsmineflow_mining import build_directly_follows_graph, load_events_from_csv


ROOT = Path(__file__).resolve().parents[3]


class DrawioExporterTests(unittest.TestCase):
    def test_builds_valid_mxfile_xml(self) -> None:
        events = load_events_from_csv(ROOT / "data/sample/sample_events.csv")
        process_map = build_directly_follows_graph(events)
        xml_text = build_drawio_xml(process_map)
        root = ElementTree.fromstring(xml_text)

        self.assertEqual(root.tag, "mxfile")
        diagram = root.find("diagram")
        self.assertIsNotNone(diagram)
        model = diagram.find("mxGraphModel") if diagram is not None else None
        self.assertIsNotNone(model)
        graph_root = model.find("root") if model is not None else None
        self.assertIsNotNone(graph_root)
        cells = graph_root.findall("mxCell") if graph_root is not None else []
        vertices = [cell for cell in cells if cell.attrib.get("vertex") == "1"]
        edges = [cell for cell in cells if cell.attrib.get("edge") == "1"]
        self.assertGreaterEqual(len(vertices), len(process_map["nodes"]) + 2)
        self.assertGreaterEqual(len(edges), len(process_map["edges"]))


if __name__ == "__main__":
    unittest.main()

